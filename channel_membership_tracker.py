"""Track conversion of known BETROXY users into the public updates channel.

This worker is read-only from the customer perspective. It never sends a customer
message. It checks channel membership slowly, stores the latest state, and sends
an admin conversion report once a daily audit finishes.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import threading
import time

import requests

import bot

CHANNEL_HANDLE = "@betroxyupdates"
CHANNEL_URL = "https://t.me/betroxyupdates"
CHECK_INTERVAL_SECONDS = 1.0
RUN_EVERY_HOURS = 24
START_DELAY_SECONDS = 120
RETRY_AFTER_CUSHION_SECONDS = 60

STATUS_TABLE = "betroxy_channel_membership_status"
RUN_TABLE = "betroxy_channel_membership_audits"

_installed = False
_admin_notice = None


def _ensure_schema():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {STATUS_TABLE} (
                    telegram_user_id BIGINT PRIMARY KEY,
                    source_officialbot BOOLEAN NOT NULL DEFAULT FALSE,
                    source_business BOOLEAN NOT NULL DEFAULT FALSE,
                    is_member BOOLEAN,
                    member_status TEXT,
                    first_joined_at TIMESTAMPTZ,
                    last_checked_at TIMESTAMPTZ,
                    last_success_at TIMESTAMPTZ,
                    last_error TEXT
                )
            """)
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {RUN_TABLE} (
                    id BIGSERIAL PRIMARY KEY,
                    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    completed_at TIMESTAMPTZ,
                    total_target INTEGER NOT NULL DEFAULT 0,
                    checked INTEGER NOT NULL DEFAULT 0,
                    members INTEGER NOT NULL DEFAULT 0,
                    nonmembers INTEGER NOT NULL DEFAULT 0,
                    errors INTEGER NOT NULL DEFAULT 0,
                    new_joins INTEGER NOT NULL DEFAULT 0
                )
            """)
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{STATUS_TABLE}_checked "
                f"ON {STATUS_TABLE}(last_checked_at)"
            )
        conn.commit()


def _known_targets():
    """Return unique users plus whether each is reachable via Bot and/or Business."""
    targets = {}
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT telegram_user_id
                FROM intelligence_leads
                WHERE telegram_user_id IS NOT NULL
                  AND reachable_bot=TRUE
            """)
            for row in cur.fetchall():
                uid = int(row["telegram_user_id"])
                targets.setdefault(uid, {"uid": uid, "official": False, "business": False})["official"] = True

            cur.execute("SELECT to_regclass('public.telegram_business_enquiries') AS t")
            if (cur.fetchone() or {}).get("t"):
                cur.execute("""
                    SELECT DISTINCT customer_user_id
                    FROM telegram_business_enquiries
                    WHERE customer_user_id IS NOT NULL
                """)
                for row in cur.fetchall():
                    uid = int(row["customer_user_id"])
                    targets.setdefault(uid, {"uid": uid, "official": False, "business": False})["business"] = True
    return [targets[k] for k in sorted(targets)]


def _last_completed_at():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT completed_at FROM {RUN_TABLE} "
                "WHERE completed_at IS NOT NULL ORDER BY completed_at DESC LIMIT 1"
            )
            row = cur.fetchone()
    return row.get("completed_at") if row else None


def _claim_run(total_target):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {RUN_TABLE}(total_target) VALUES (%s) RETURNING id",
                (int(total_target),),
            )
            run_id = int(cur.fetchone()["id"])
        conn.commit()
    return run_id


def _member_api(uid):
    url = f"https://api.telegram.org/bot{bot.BOT_TOKEN}/getChatMember"
    for attempt in (1, 2):
        try:
            response = requests.post(
                url,
                data={"chat_id": CHANNEL_HANDLE, "user_id": int(uid)},
                timeout=20,
            )
            try:
                data = response.json()
            except Exception:
                data = {}

            if response.status_code == 429 or int(data.get("error_code") or 0) == 429:
                retry_after = int(((data.get("parameters") or {}).get("retry_after")) or 60)
                pause = retry_after + RETRY_AFTER_CUSHION_SECONDS
                bot.logger.warning(
                    "CHANNEL_MEMBERSHIP_RATE_LIMIT uid=%s retry_after=%ss pause=%ss attempt=%s/2",
                    uid, retry_after, pause, attempt,
                )
                time.sleep(pause)
                continue

            if not response.ok or not data.get("ok"):
                desc = str(data.get("description") or f"HTTP {response.status_code}")[:500]
                return None, None, desc

            result = data.get("result") or {}
            status = str(result.get("status") or "unknown")
            if status in {"creator", "administrator", "member"}:
                is_member = True
            elif status == "restricted":
                is_member = bool(result.get("is_member", True))
            elif status in {"left", "kicked"}:
                is_member = False
            else:
                is_member = None
            return is_member, status, None
        except Exception as exc:
            if attempt == 2:
                return None, None, str(exc)[:500]
            time.sleep(5)
    return None, None, "unknown membership error"


def _save_result(target, is_member, member_status, error):
    uid = int(target["uid"])
    now = datetime.now(timezone.utc)
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT is_member FROM {STATUS_TABLE} WHERE telegram_user_id=%s",
                (uid,),
            )
            previous = cur.fetchone()
            prior_member = previous.get("is_member") if previous else None

            if error is None:
                cur.execute(f"""
                    INSERT INTO {STATUS_TABLE}(
                        telegram_user_id, source_officialbot, source_business,
                        is_member, member_status, first_joined_at,
                        last_checked_at, last_success_at, last_error
                    ) VALUES (%s,%s,%s,%s,%s,
                              CASE WHEN %s THEN NOW() ELSE NULL END,
                              NOW(),NOW(),NULL)
                    ON CONFLICT(telegram_user_id) DO UPDATE SET
                        source_officialbot=EXCLUDED.source_officialbot,
                        source_business=EXCLUDED.source_business,
                        is_member=EXCLUDED.is_member,
                        member_status=EXCLUDED.member_status,
                        first_joined_at=CASE
                            WHEN EXCLUDED.is_member=TRUE
                            THEN COALESCE({STATUS_TABLE}.first_joined_at, NOW())
                            ELSE {STATUS_TABLE}.first_joined_at
                        END,
                        last_checked_at=NOW(),
                        last_success_at=NOW(),
                        last_error=NULL
                """, (
                    uid, bool(target["official"]), bool(target["business"]),
                    is_member, member_status, bool(is_member),
                ))
            else:
                cur.execute(f"""
                    INSERT INTO {STATUS_TABLE}(
                        telegram_user_id, source_officialbot, source_business,
                        last_checked_at, last_error
                    ) VALUES (%s,%s,%s,NOW(),%s)
                    ON CONFLICT(telegram_user_id) DO UPDATE SET
                        source_officialbot=EXCLUDED.source_officialbot,
                        source_business=EXCLUDED.source_business,
                        last_checked_at=NOW(),
                        last_error=EXCLUDED.last_error
                """, (uid, bool(target["official"]), bool(target["business"]), str(error)[:500]))
        conn.commit()

    # First baseline discovery is not counted as a conversion. Only a previously
    # confirmed non-member becoming a member is a new join.
    return previous is not None and prior_member is False and is_member is True


def _current_summary(targets):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT telegram_user_id,is_member,last_error FROM {STATUS_TABLE}")
            state = {int(r["telegram_user_id"]): r for r in cur.fetchall()}

    def segment(rows):
        total = len(rows)
        members = sum(1 for t in rows if (state.get(int(t["uid"])) or {}).get("is_member") is True)
        known_nonmembers = sum(1 for t in rows if (state.get(int(t["uid"])) or {}).get("is_member") is False)
        unknown = total - members - known_nonmembers
        return {"total": total, "members": members, "nonmembers": known_nonmembers, "unknown": unknown}

    official = [t for t in targets if t["official"]]
    business = [t for t in targets if t["business"]]
    business_only = [t for t in targets if t["business"] and not t["official"]]
    overlap = [t for t in targets if t["business"] and t["official"]]
    all_users = segment(targets)
    return {
        "all": all_users,
        "official": segment(official),
        "business": segment(business),
        "business_only": segment(business_only),
        "overlap": segment(overlap),
    }


def _pct(part, whole):
    if not whole:
        return "0.0%"
    return f"{(100.0 * part / whole):.1f}%"


def _send_admin_report(summary, run_stats):
    all_s = summary["all"]
    bot_s = summary["official"]
    biz_s = summary["business"]
    biz_only_s = summary["business_only"]
    overlap_s = summary["overlap"]
    text = (
        "📢 <b>BETROXY Channel Conversion Report</b>\n\n"
        f"Channel: <b>{CHANNEL_HANDLE}</b>\n"
        f"Known unique Bot/Business users: <b>{all_s['total']}</b>\n"
        f"✅ Confirmed channel members: <b>{all_s['members']}</b> "
        f"({_pct(all_s['members'], all_s['total'])})\n"
        f"❌ Confirmed not joined: <b>{all_s['nonmembers']}</b>\n"
        f"❓ Unknown/check error: <b>{all_s['unknown']}</b>\n"
        f"🆕 New joins since previous audit: <b>{run_stats['new_joins']}</b>\n\n"
        f"🤖 OfficialBot reachable: <b>{bot_s['members']}/{bot_s['total']}</b> joined\n"
        f"💬 Business contacts: <b>{biz_s['members']}/{biz_s['total']}</b> joined\n"
        f"💬 Business-only: <b>{biz_only_s['members']}/{biz_only_s['total']}</b> joined\n"
        f"🔁 Bot + Business overlap: <b>{overlap_s['members']}/{overlap_s['total']}</b> joined\n\n"
        f"Audit checked: <b>{run_stats['checked']}/{run_stats['total_target']}</b>\n"
        f"API/check errors this audit: <b>{run_stats['errors']}</b>\n\n"
        "This audit is read-only and does not send any customer DM. "
        "It runs slowly once per day to measure conversion from the reminder CTA."
    )
    try:
        if _admin_notice:
            return bool(_admin_notice(text))
    except Exception:
        bot.logger.exception("CHANNEL_MEMBERSHIP_ADMIN_REPORT_FAILED")
    return False


def _run_audit():
    targets = _known_targets()
    run_id = _claim_run(len(targets))
    stats = {
        "total_target": len(targets),
        "checked": 0,
        "members": 0,
        "nonmembers": 0,
        "errors": 0,
        "new_joins": 0,
    }

    bot.logger.warning(
        "CHANNEL_MEMBERSHIP_AUDIT_START run=%s channel=%s targets=%s interval=%ss",
        run_id, CHANNEL_HANDLE, len(targets), CHECK_INTERVAL_SECONDS,
    )

    for index, target in enumerate(targets, 1):
        is_member, status, error = _member_api(target["uid"])
        new_join = _save_result(target, is_member, status, error)
        stats["checked"] += 1
        if error is not None or is_member is None:
            stats["errors"] += 1
        elif is_member:
            stats["members"] += 1
        else:
            stats["nonmembers"] += 1
        if new_join:
            stats["new_joins"] += 1

        if index % 50 == 0:
            bot.logger.warning(
                "CHANNEL_MEMBERSHIP_AUDIT_PROGRESS run=%s checked=%s/%s members=%s nonmembers=%s errors=%s new_joins=%s",
                run_id, index, len(targets), stats["members"], stats["nonmembers"], stats["errors"], stats["new_joins"],
            )
        if index < len(targets):
            time.sleep(CHECK_INTERVAL_SECONDS)

    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                UPDATE {RUN_TABLE}
                SET completed_at=NOW(), checked=%s, members=%s, nonmembers=%s,
                    errors=%s, new_joins=%s
                WHERE id=%s
            """, (
                stats["checked"], stats["members"], stats["nonmembers"],
                stats["errors"], stats["new_joins"], run_id,
            ))
        conn.commit()

    summary = _current_summary(targets)
    _send_admin_report(summary, stats)
    bot.logger.warning(
        "CHANNEL_MEMBERSHIP_AUDIT_COMPLETE run=%s targets=%s checked=%s members=%s nonmembers=%s errors=%s new_joins=%s",
        run_id, len(targets), stats["checked"], stats["members"], stats["nonmembers"], stats["errors"], stats["new_joins"],
    )
    return summary


def snapshot():
    """Return persisted conversion counts without making Telegram API calls."""
    try:
        targets = _known_targets()
        return _current_summary(targets)
    except Exception:
        bot.logger.exception("CHANNEL_MEMBERSHIP_SNAPSHOT_FAILED")
        return None


def _worker():
    time.sleep(START_DELAY_SECONDS)
    while True:
        try:
            last = _last_completed_at()
            due = last is None or datetime.now(timezone.utc) - last >= timedelta(hours=RUN_EVERY_HOURS)
            if due:
                _run_audit()
        except Exception:
            bot.logger.exception("CHANNEL_MEMBERSHIP_AUDIT_FAILED")
        time.sleep(30 * 60)


def install(admin_notice):
    global _installed, _admin_notice
    if _installed:
        return
    _admin_notice = admin_notice
    _ensure_schema()
    threading.Thread(
        target=_worker,
        name="betroxy-channel-membership-audit",
        daemon=True,
    ).start()
    _installed = True
    bot.logger.warning(
        "CHANNEL_MEMBERSHIP_TRACKER active=on channel=%s daily_audit=on interval=%ss "
        "read_only=on customer_dm=off admin_report=on",
        CHANNEL_HANDLE, CHECK_INTERVAL_SECONDS,
    )
