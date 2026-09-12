"""Account-safety weekly policy for Telegram Business quiz reminders.

This is a narrow production overlay on daily_quiz_alerts:
- OfficialBot keeps its one-reminder-per-target-day queue.
- Direct Telegram Business DM is considered only for a recent inbound, open,
  reply-capable conversation (daily_quiz_alerts already enforces this).
- A Business-DM-only user can receive at most one successful automated quiz
  reminder in any rolling 7-day window.
- Old/dormant Business contacts stay recorded and appear in coverage reports;
  they are never forced through Telegram outside the safe recent-inbound window.
- If a dormant customer messages again, the normal recent-inbound selector makes
  them eligible automatically, subject to the 7-day Business cooldown.
"""
from datetime import datetime, timezone, timedelta

import bot

BUSINESS_REMINDER_COOLDOWN_DAYS = 7
REPORT_TABLE = "business_weekly_reminder_reports"

_installed = False
_original_eligible_business_chats = None
_original_dispatch_cycle = None
_quiz_alerts = None


def _ensure_schema():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {REPORT_TABLE} (
                    target_day DATE PRIMARY KEY,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
        conn.commit()


def _last_business_quiz_sent(user_ids):
    if not user_ids:
        return {}
    ids = [int(x) for x in user_ids]
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT telegram_user_id, MAX(created_at) AS last_sent_at
                FROM engagement_log
                WHERE telegram_user_id = ANY(%s::bigint[])
                  AND channel='business'
                  AND action_type='quiz_rewards'
                  AND status='sent'
                GROUP BY telegram_user_id
            """, (ids,))
            return {
                int(r["telegram_user_id"]): r.get("last_sent_at")
                for r in cur.fetchall()
            }


def _weekly_eligible_business_chats():
    rows = list(_original_eligible_business_chats() or [])
    if not rows:
        return []

    last_sent = _last_business_quiz_sent([
        int(r["customer_user_id"]) for r in rows
        if r.get("customer_user_id") is not None
    ])
    threshold = datetime.now(timezone.utc) - timedelta(days=BUSINESS_REMINDER_COOLDOWN_DAYS)

    due = []
    cooldown = 0
    for row in rows:
        uid = int(row["customer_user_id"])
        sent_at = last_sent.get(uid)
        if sent_at is not None and sent_at >= threshold:
            cooldown += 1
            continue
        due.append(row)

    bot.logger.warning(
        "WEEKLY_BUSINESS_REMINDER_FILTER recent_active=%s due=%s cooldown=%s cooldown_days=%s",
        len(rows), len(due), cooldown, BUSINESS_REMINDER_COOLDOWN_DAYS,
    )
    return due


def _coverage_snapshot():
    """Classify all stored Business contacts without attempting to message them."""
    now = datetime.now(timezone.utc)
    recent_after = now - timedelta(hours=int(getattr(_quiz_alerts, "BUSINESS_RECENT_HOURS", 23)))
    weekly_after = now - timedelta(days=BUSINESS_REMINDER_COOLDOWN_DAYS)

    try:
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.telegram_business_enquiries') AS t")
                if not (cur.fetchone() or {}).get("t"):
                    return {
                        "total_business": 0,
                        "bot_reachable": 0,
                        "business_active_due": 0,
                        "business_active_cooldown": 0,
                        "business_dormant": 0,
                        "opted_out": 0,
                        "unreachable": 0,
                    }

                cur.execute("""
                    SELECT DISTINCT ON (e.customer_user_id)
                           e.customer_user_id,
                           e.status,
                           c.is_enabled,
                           c.can_reply,
                           inbound.last_inbound_at,
                           COALESCE(l.reachable_bot,FALSE) AS reachable_bot,
                           COALESCE(l.opt_out,FALSE) AS opt_out,
                           COALESCE(l.lifecycle_stage,'') AS lifecycle_stage,
                           biz.last_business_sent_at
                    FROM telegram_business_enquiries e
                    JOIN telegram_business_connections c
                      ON c.connection_id=e.connection_id
                    LEFT JOIN LATERAL (
                        SELECT MAX(m.created_at) AS last_inbound_at
                        FROM telegram_business_messages m
                        WHERE m.enquiry_id=e.id
                          AND m.direction='inbound'
                    ) inbound ON TRUE
                    LEFT JOIN intelligence_leads l
                      ON l.telegram_user_id=e.customer_user_id
                    LEFT JOIN LATERAL (
                        SELECT MAX(g.created_at) AS last_business_sent_at
                        FROM engagement_log g
                        WHERE g.telegram_user_id=e.customer_user_id
                          AND g.channel='business'
                          AND g.action_type='quiz_rewards'
                          AND g.status='sent'
                    ) biz ON TRUE
                    WHERE e.customer_user_id IS NOT NULL
                    ORDER BY e.customer_user_id, inbound.last_inbound_at DESC NULLS LAST
                """)
                rows = cur.fetchall()

        out = {
            "total_business": len(rows),
            "bot_reachable": 0,
            "business_active_due": 0,
            "business_active_cooldown": 0,
            "business_dormant": 0,
            "opted_out": 0,
            "unreachable": 0,
        }

        for row in rows:
            stage = str(row.get("lifecycle_stage") or "")
            if bool(row.get("opt_out")) or stage in {"opted_out", "suppressed"}:
                out["opted_out"] += 1
                continue
            if stage == "unreachable":
                out["unreachable"] += 1
                continue
            if bool(row.get("reachable_bot")):
                out["bot_reachable"] += 1
                continue

            last_inbound = row.get("last_inbound_at")
            business_active = (
                str(row.get("status") or "") == "open"
                and bool(row.get("is_enabled"))
                and bool(row.get("can_reply"))
                and last_inbound is not None
                and last_inbound >= recent_after
            )
            if not business_active:
                out["business_dormant"] += 1
                continue

            last_sent = row.get("last_business_sent_at")
            if last_sent is not None and last_sent >= weekly_after:
                out["business_active_cooldown"] += 1
            else:
                out["business_active_due"] += 1

        return out
    except Exception:
        bot.logger.exception("WEEKLY_BUSINESS_COVERAGE_FAILED")
        return {
            "total_business": 0,
            "bot_reachable": 0,
            "business_active_due": 0,
            "business_active_cooldown": 0,
            "business_dormant": 0,
            "opted_out": 0,
            "unreachable": 0,
        }


def _claim_report(target_day):
    try:
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""INSERT INTO {REPORT_TABLE}(target_day)
                        VALUES (%s)
                        ON CONFLICT(target_day) DO NOTHING
                        RETURNING target_day""",
                    (target_day,),
                )
                row = cur.fetchone()
            conn.commit()
        return bool(row)
    except Exception:
        bot.logger.exception("WEEKLY_BUSINESS_REPORT_CLAIM_FAILED target=%s", target_day)
        return False


def _report_coverage_once(target_day):
    if not _claim_report(target_day):
        return
    snap = _coverage_snapshot()
    text = (
        "📋 <b>BETROXY Reminder Coverage</b>\n\n"
        f"Target quiz date: <b>{target_day.isoformat()}</b>\n"
        f"Stored Business contacts: <b>{snap['total_business']}</b>\n"
        f"Already reachable via OfficialBot: <b>{snap['bot_reachable']}</b>\n"
        f"Business active + weekly reminder due: <b>{snap['business_active_due']}</b>\n"
        f"Business active but in 7-day cooldown: <b>{snap['business_active_cooldown']}</b>\n"
        f"Business dormant / outside safe reply window: <b>{snap['business_dormant']}</b>\n"
        f"Opted out / suppressed: <b>{snap['opted_out']}</b>\n"
        f"Unreachable: <b>{snap['unreachable']}</b>\n\n"
        "Dormant contacts remain stored and automatically become eligible again "
        "after a new inbound Business message. Business reminders are capped at "
        "one successful automated quiz reminder per rolling 7 days."
    )
    try:
        _quiz_alerts._admin_notice(text)
    except Exception:
        bot.logger.exception("WEEKLY_BUSINESS_COVERAGE_REPORT_FAILED target=%s", target_day)

    bot.logger.warning(
        "WEEKLY_BUSINESS_COVERAGE target=%s total=%s bot=%s due=%s cooldown=%s dormant=%s opted=%s unreachable=%s",
        target_day,
        snap["total_business"], snap["bot_reachable"], snap["business_active_due"],
        snap["business_active_cooldown"], snap["business_dormant"],
        snap["opted_out"], snap["unreachable"],
    )


def _dispatch_cycle_with_weekly_policy(target_day, source):
    _report_coverage_once(target_day)
    return _original_dispatch_cycle(target_day, source)


def install(quiz_alerts):
    global _installed, _original_eligible_business_chats, _original_dispatch_cycle, _quiz_alerts
    if _installed:
        return

    _quiz_alerts = quiz_alerts
    _ensure_schema()
    _original_eligible_business_chats = quiz_alerts._eligible_business_chats
    _original_dispatch_cycle = quiz_alerts._dispatch_cycle

    quiz_alerts._eligible_business_chats = _weekly_eligible_business_chats
    quiz_alerts._dispatch_cycle = _dispatch_cycle_with_weekly_policy
    quiz_alerts.BUSINESS_REMINDER_COOLDOWN_DAYS = BUSINESS_REMINDER_COOLDOWN_DAYS
    quiz_alerts._business_weekly_policy_installed = True
    _installed = True

    bot.logger.warning(
        "WEEKLY_BUSINESS_REMINDER_POLICY active=on cadence=7d recent_inbound_only=on "
        "officialbot_preferred=on dormant_tracked=on dormant_auto_reactivate_on_inbound=on "
        "coverage_report=once_per_target_day business_hard_pacing_preserved=on account_priority=on"
    )
