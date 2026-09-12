"""Account-safety-first Daily Quiz reminder queue.

Policy:
- one quiz reminder per Telegram user per target quiz day
- OfficialBot is preferred whenever the user is reachable there
- direct Telegram Business DM is only a secondary route for recent inbound
  Business conversations with an active, reply-capable connection
- no 4 PM / 7 PM reminder campaigns
- all other proactive engagement/reminder automation stays disabled
- today's direct-DM-only users may be picked up immediately
- from the next cycle onward, the queue opens only after the previous day's
  21:05 IST final result has actually been announced
- the queue is spread very slowly until 20:30 IST on the target quiz day
- delivered users are deduplicated across OfficialBot and Business routes
- admin receives start/progress/final reports
"""
from datetime import datetime, timezone, timedelta
import hashlib
import time

import bot
import daily_quiz_schedule as schedule
import safe_reminder_delivery as safe_delivery

v110 = schedule.v110
v83 = v110.v83

INDIA_OFFSET_HOURS = 5.5
QUIZ_OPEN_HOUR = 10
QUEUE_STOP_HOUR = 20
QUEUE_STOP_MINUTE = 30
RESULT_HOUR = 21
RESULT_MINUTE = 5
BUSINESS_RECENT_HOURS = 23
PROGRESS_EVERY = 20

schedule.DUBAI_OFFSET = INDIA_OFFSET_HOURS
v110.TZ_OFFSET = INDIA_OFFSET_HOURS
v83.TZ_OFFSET = INDIA_OFFSET_HOURS

# Safe delivery remains globally conservative. Generic/legacy Business automation
# is still OFF; this module alone can request the explicit daily-quiz Business
# exception, which has a hard >=10 minute per-Business-route interval.
safe_delivery.install(v83)


def _disabled_engagement_worker():
    """Keep all legacy proactive automation idle in production safety mode."""
    bot.logger.warning(
        "PROACTIVE_ENGAGEMENT_WORKER disabled=on optin=OFF reminders=OFF sports=OFF "
        "promotions=OFF reactivation=OFF business_followup=OFF account_priority=on"
    )
    while True:
        time.sleep(3600)


# production.py starts this thread later; replace it before thread creation.
v83._worker_loop = _disabled_engagement_worker


def _local_now():
    # Existing quiz code uses a shifted UTC datetime as an IST wall-clock value.
    return datetime.now(timezone.utc) + timedelta(hours=INDIA_OFFSET_HOURS)


def _wall_datetime(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute, 0, tzinfo=timezone.utc)


def _key(day):
    # Keep the existing key shape so today's already-delivered users remain
    # deduplicated after this deployment.
    return f"daily_quiz_open:{day.isoformat()}"


def _cutoff(day):
    return _wall_datetime(day, QUEUE_STOP_HOUR, QUEUE_STOP_MINUTE)


def _past_cutoff(day, local=None):
    local = local or _local_now()
    return local >= _cutoff(day)


def _eligible_official_users():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT l.telegram_user_id
                FROM intelligence_leads l
                WHERE l.reachable_bot=TRUE
                  AND l.opt_out=FALSE
                  AND COALESCE(l.lifecycle_stage,'') NOT IN ('suppressed','unreachable','opted_out')
                ORDER BY l.telegram_user_id
            """)
            return [int(r["telegram_user_id"]) for r in cur.fetchall()]


def _eligible_business_chats():
    """Return conservative Business-DM-only candidates.

    We only use open conversations with a real recent inbound message and an
    enabled, reply-capable Business connection. This is intentionally narrower
    than "every historical DM" because account safety has priority.
    """
    try:
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.telegram_business_enquiries') AS t")
                if not (cur.fetchone() or {}).get("t"):
                    return []
                cur.execute(f"""
                    SELECT DISTINCT ON (e.customer_user_id)
                           e.id AS enquiry_id,
                           e.customer_user_id,
                           e.customer_chat_id,
                           e.connection_id,
                           inbound.last_inbound_at
                    FROM telegram_business_enquiries e
                    JOIN telegram_business_connections c
                      ON c.connection_id=e.connection_id
                    JOIN LATERAL (
                        SELECT MAX(m.created_at) AS last_inbound_at
                        FROM telegram_business_messages m
                        WHERE m.enquiry_id=e.id
                          AND m.direction='inbound'
                    ) inbound ON inbound.last_inbound_at IS NOT NULL
                    LEFT JOIN intelligence_leads l
                      ON l.telegram_user_id=e.customer_user_id
                    WHERE e.customer_user_id IS NOT NULL
                      AND e.customer_chat_id IS NOT NULL
                      AND e.status='open'
                      AND c.is_enabled=TRUE
                      AND c.can_reply=TRUE
                      AND inbound.last_inbound_at >= NOW()-INTERVAL '{BUSINESS_RECENT_HOURS} hours'
                      AND COALESCE(l.opt_out,FALSE)=FALSE
                      AND COALESCE(l.lifecycle_stage,'') NOT IN ('suppressed','unreachable','opted_out')
                    ORDER BY e.customer_user_id, inbound.last_inbound_at DESC
                """)
                return cur.fetchall()
    except Exception:
        bot.logger.exception("DAILY_QUIZ_BUSINESS_ELIGIBILITY_FAILED")
        return []


def _business_still_safe(row):
    """Re-check freshness and connection rights immediately before sending."""
    try:
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    SELECT
                        c.is_enabled,
                        c.can_reply,
                        e.status,
                        (
                            SELECT MAX(m.created_at)
                            FROM telegram_business_messages m
                            WHERE m.enquiry_id=e.id AND m.direction='inbound'
                        ) AS last_inbound_at
                    FROM telegram_business_enquiries e
                    JOIN telegram_business_connections c
                      ON c.connection_id=e.connection_id
                    WHERE e.id=%s
                      AND e.connection_id=%s
                    LIMIT 1
                """, (int(row["enquiry_id"]), str(row["connection_id"])))
                current = cur.fetchone() or {}
        last_inbound = current.get("last_inbound_at")
        if not last_inbound:
            return False
        fresh_after = datetime.now(timezone.utc) - timedelta(hours=BUSINESS_RECENT_HOURS)
        return (
            bool(current.get("is_enabled"))
            and bool(current.get("can_reply"))
            and str(current.get("status") or "") == "open"
            and last_inbound >= fresh_after
        )
    except Exception:
        bot.logger.exception("DAILY_QUIZ_BUSINESS_RECHECK_FAILED uid=%s", row.get("customer_user_id"))
        return False


def _sent_ids(message_key):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT telegram_user_id
                FROM engagement_log
                WHERE message_key=%s AND status='sent'
            """, (str(message_key),))
            return {int(r["telegram_user_id"]) for r in cur.fetchall()}


def _message_for(target_day):
    local = _local_now()
    if local.date() < target_day:
        heading = "🏆 <b>Tomorrow's BETROXY Daily Quiz</b>"
        timing = "The next quiz opens at <b>10:00 AM IST tomorrow</b> and stays open until <b>9:00 PM IST</b>."
    elif local.hour < QUIZ_OPEN_HOUR:
        heading = "🏆 <b>Today's BETROXY Daily Quiz</b>"
        timing = "Today's quiz opens at <b>10:00 AM IST</b> and stays open until <b>9:00 PM IST</b>."
    else:
        heading = "🏆 <b>Today's BETROXY Daily Quiz is OPEN</b>"
        timing = "Play anytime today until <b>9:00 PM IST</b>."

    return (
        f"{heading}\n\n"
        f"{timing}\n\n"
        "🎁 <b>₹1,000 Amazon Pay Gift Voucher prize pool</b>\n"
        "🥇 1st — ₹500\n"
        "🥈 2nd — ₹300\n"
        "🥉 3rd — ₹200\n\n"
        "7 questions • 30 seconds each • one attempt per day\n"
        "💯 Free to participate — no deposit or wager required."
    )


def _quiz_button():
    return [[{
        "text": "🏆 Daily Quiz",
        "url": f"https://t.me/{bot.BOT_USERNAME}?start=dailyquiz",
    }]]


def _send_official(uid, message_key, target_day):
    try:
        return safe_delivery.send_claimed_result(
            uid,
            "quiz_rewards",
            message_key,
            _message_for(target_day),
            _quiz_button(),
            channel="officialbot",
            detail="single daily quiz reminder; spread queue; OfficialBot preferred",
        )
    except Exception:
        bot.logger.exception("DAILY_QUIZ_SAFE_DM_FAILED uid=%s key=%s", uid, message_key)
        return {
            "sent": False, "status": "failed", "retried": 0,
            "permanent": False, "rate_limited": False,
        }


def _send_business(row, message_key, target_day):
    uid = int(row["customer_user_id"])
    if not _business_still_safe(row):
        return {
            "sent": False, "status": "business_no_longer_safe", "retried": 0,
            "permanent": False, "rate_limited": False,
        }
    try:
        return safe_delivery.send_claimed_result(
            uid,
            "quiz_rewards",
            message_key,
            _message_for(target_day),
            _quiz_button(),
            chat_id=int(row["customer_chat_id"]),
            business_connection_id=str(row["connection_id"]),
            channel="business",
            detail=(
                "single daily quiz reminder via recent inbound Business DM; "
                "explicit safety exception; one user/day"
            ),
            allow_business_automation=True,
        )
    except Exception:
        bot.logger.exception("DAILY_QUIZ_SAFE_BUSINESS_FAILED uid=%s key=%s", uid, message_key)
        return {
            "sent": False, "status": "failed", "retried": 0,
            "permanent": False, "rate_limited": False,
        }


def _send_channel_once(campaign, text):
    delivery_kind = "quiz_alert_open"
    if v110._delivery_exists(campaign["id"], v110.CHANNEL_CHAT, delivery_kind):
        return False
    ok, data = schedule._send_text(
        v110.CHANNEL_CHAT,
        text,
        [[{
            "text": "🏆 Play Today's Quiz",
            "url": f"https://t.me/{bot.BOT_USERNAME}?start=dailyquiz",
        }]],
    )
    if ok:
        mid = ((data.get("result") or {}).get("message_id")) if isinstance(data, dict) else None
        v110._mark_delivery(campaign["id"], v110.CHANNEL_CHAT, delivery_kind, mid)
    return ok


def _ensure_channel_for_today():
    local = _local_now()
    if local.hour < QUIZ_OPEN_HOUR or _past_cutoff(local.date(), local):
        return False
    campaign = schedule._today_campaign_windowed(test_mode=False)
    return _send_channel_once(campaign, _message_for(local.date()))


def _admin_notice(text):
    try:
        ok, _ = schedule._send_text(bot.ADMIN_ID, text)
        bot.logger.warning("DAILY_QUIZ_ADMIN_REPORT sent=%s", ok)
        return ok
    except Exception:
        bot.logger.exception("DAILY_QUIZ_ADMIN_REPORT_FAILED")
        return False


def _result_announced_today():
    try:
        campaign = schedule._today_campaign_windowed(test_mode=False)
        return bool(schedule._result_already_sent(campaign["id"]))
    except Exception:
        bot.logger.exception("DAILY_QUIZ_RESULT_GATE_FAILED")
        return False


def _target_day_for_queue(local=None):
    """Select today's quiz, or tomorrow only after today's result is announced."""
    local = local or _local_now()
    today = local.date()

    # Between the reminder cutoff and the final-result announcement we send
    # nothing. This avoids a late reminder for a quiz that is about to close.
    if _past_cutoff(today, local) and (
        local.hour < RESULT_HOUR
        or (local.hour == RESULT_HOUR and local.minute < RESULT_MINUTE)
    ):
        return None, "waiting_for_result"

    if (
        local.hour > RESULT_HOUR
        or (local.hour == RESULT_HOUR and local.minute >= RESULT_MINUTE)
    ):
        if _result_announced_today():
            return today + timedelta(days=1), "post_result_next_day"
        return None, "waiting_for_result"

    return today, "today"


def _queue_items(target_day):
    message_key = _key(target_day)
    already = _sent_ids(message_key)

    official_users = _eligible_official_users()
    official_ids = set(official_users)

    items = []
    for uid in official_users:
        if uid not in already:
            items.append({
                "route": "officialbot",
                "uid": int(uid),
            })

    # Business is secondary only. A user reachable through OfficialBot never gets
    # a second copy through Business.
    business_rows = _eligible_business_chats()
    for row in business_rows:
        uid = int(row["customer_user_id"])
        if uid in official_ids or uid in already:
            continue
        items.append({
            "route": "business",
            "uid": uid,
            "row": row,
        })

    # Stable pseudo-random order prevents route/user clusters while preserving
    # deterministic resume behavior after a deployment.
    def order_key(item):
        raw = f"{target_day.isoformat()}:{item['uid']}:{item['route']}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    items.sort(key=order_key)
    return {
        "message_key": message_key,
        "already": already,
        "official_eligible": len(official_users),
        "business_eligible": len([
            r for r in business_rows
            if int(r["customer_user_id"]) not in official_ids
        ]),
        "items": items,
    }


def _format_minutes(seconds):
    return max(0, int(round(seconds / 60.0)))


def _dispatch_cycle(target_day, source):
    local = _local_now()
    cutoff = _cutoff(target_day)
    if local >= cutoff:
        return {
            "target_day": target_day, "sent": 0, "remaining": 0,
            "skipped": 0, "failed": 0, "blocked": 0, "retried": 0,
        }

    state = _queue_items(target_day)
    items = state["items"]
    message_key = state["message_key"]
    already_count = len(state["already"])

    if not items:
        bot.logger.warning(
            "DAILY_QUIZ_SAFE_REMINDER_COMPLETE target=%s source=%s official_eligible=%s "
            "business_eligible=%s already_sent=%s remaining=0 account_priority=on",
            target_day, source, state["official_eligible"], state["business_eligible"], already_count,
        )
        return {
            "target_day": target_day, "sent": 0, "remaining": 0,
            "skipped": 0, "failed": 0, "blocked": 0, "retried": 0,
        }

    seconds_available = max(60.0, (cutoff - local).total_seconds())
    # Use only 90% of the available window so jitter / Telegram cooldowns do not
    # force the final sends against the 20:30 safety cutoff.
    planned_gap = max(
        safe_delivery.GLOBAL_MIN_INTERVAL,
        (seconds_available * 0.90) / max(1, len(items)),
    )
    official_pending = sum(1 for x in items if x["route"] == "officialbot")
    business_pending = sum(1 for x in items if x["route"] == "business")

    _admin_notice(
        "🛡 <b>BETROXY Daily Reminder Queue Started</b>\n\n"
        f"Target quiz date: <b>{target_day.isoformat()}</b>\n"
        f"Queue source: <b>{source}</b>\n"
        f"OfficialBot pending: <b>{official_pending}</b>\n"
        f"Eligible recent Business-DM-only pending: <b>{business_pending}</b>\n"
        f"Already delivered for this quiz date: <b>{already_count}</b>\n"
        f"Total remaining: <b>{len(items)}</b>\n\n"
        f"Planned average spacing: <b>~{_format_minutes(planned_gap)} minutes</b>\n"
        f"OfficialBot hard minimum: <b>{int(safe_delivery.OFFICIAL_MIN_INTERVAL)} seconds</b>\n"
        f"Business-DM hard minimum: <b>{int(safe_delivery.BUSINESS_MIN_INTERVAL // 60)} minutes</b>\n"
        f"Safety cutoff: <b>20:30 IST on {target_day.isoformat()}</b>\n\n"
        "One reminder maximum per user/day across both routes. "
        "4 PM, 7 PM and all other proactive campaigns remain OFF."
    )

    stats = {
        "target_day": target_day,
        "sent": 0,
        "official_sent": 0,
        "business_sent": 0,
        "already_sent": already_count,
        "retried": 0,
        "failed": 0,
        "blocked": 0,
        "skipped": 0,
        "remaining": len(items),
        "rate_limit_events": 0,
    }
    next_planned = time.monotonic()

    for index, item in enumerate(items, 1):
        now_local = _local_now()
        if now_local >= cutoff:
            bot.logger.warning(
                "DAILY_QUIZ_SAFE_REMINDER_CUTOFF target=%s processed=%s total=%s",
                target_day, index - 1, len(items),
            )
            break

        # Keep the public 10 AM channel post independent of the private queue.
        _ensure_channel_for_today()

        wait = next_planned - time.monotonic()
        if wait > 0:
            time.sleep(wait)

        route = item["route"]
        if route == "business":
            result = _send_business(item["row"], message_key, target_day)
        else:
            result = _send_official(item["uid"], message_key, target_day)

        stats["retried"] += int(result.get("retried") or 0)
        if result.get("rate_limited"):
            stats["rate_limit_events"] += 1

        if result.get("sent"):
            stats["sent"] += 1
            stats[f"{route.replace('officialbot', 'official')}_sent"] += 1
        else:
            status = str(result.get("status") or "")
            if status == "already_sent":
                stats["already_sent"] += 1
            elif status in {"business_no_longer_safe", "business_automation_disabled"}:
                stats["skipped"] += 1
            elif status == "in_progress":
                stats["skipped"] += 1
            else:
                stats["failed"] += 1
                if result.get("permanent"):
                    stats["blocked"] += 1

        # Recalculate spacing from the remaining safe window after each attempt.
        remaining_count = len(items) - index
        remaining_seconds = max(0.0, (cutoff - _local_now()).total_seconds())
        if remaining_count > 0:
            dynamic_gap = max(
                safe_delivery.GLOBAL_MIN_INTERVAL,
                (remaining_seconds * 0.90) / remaining_count,
            )
            next_planned = time.monotonic() + dynamic_gap

        if index % PROGRESS_EVERY == 0:
            _admin_notice(
                "📊 <b>BETROXY Reminder Queue Progress</b>\n\n"
                f"Target: <b>{target_day.isoformat()}</b>\n"
                f"Processed: <b>{index}/{len(items)}</b>\n"
                f"Delivered this run: <b>{stats['sent']}</b> "
                f"(Bot {stats['official_sent']} • Business {stats['business_sent']})\n"
                f"Retries: <b>{stats['retried']}</b> • Failed: <b>{stats['failed']}</b> "
                f"• Skipped: <b>{stats['skipped']}</b>\n"
                f"Rate-limit events: <b>{stats['rate_limit_events']}</b>\n\n"
                "Account-safety pacing remains active."
            )

    final_state = _queue_items(target_day)
    stats["remaining"] = len(final_state["items"])

    _admin_notice(
        "✅ <b>BETROXY Daily Reminder Queue Report</b>\n\n"
        f"Target quiz date: <b>{target_day.isoformat()}</b>\n"
        f"Delivered this run: <b>{stats['sent']}</b>\n"
        f"OfficialBot delivered: <b>{stats['official_sent']}</b>\n"
        f"Business DM delivered: <b>{stats['business_sent']}</b>\n"
        f"Retries used: <b>{stats['retried']}</b>\n"
        f"Failed: <b>{stats['failed']}</b>\n"
        f"Blocked/unreachable: <b>{stats['blocked']}</b>\n"
        f"Skipped for Business safety: <b>{stats['skipped']}</b>\n"
        f"Rate-limit events: <b>{stats['rate_limit_events']}</b>\n"
        f"Still remaining at this point: <b>{stats['remaining']}</b>\n\n"
        "No 4 PM or 7 PM DM campaign. Other proactive campaigns remain OFF."
    )

    bot.logger.warning(
        "DAILY_QUIZ_SAFE_REMINDER target=%s source=%s official_eligible=%s "
        "business_eligible=%s sent=%s official_sent=%s business_sent=%s retried=%s "
        "failed=%s blocked=%s skipped=%s rate_limits=%s remaining=%s "
        "global_min=%ss business_min=%ss account_priority=on",
        target_day, source, state["official_eligible"], state["business_eligible"],
        stats["sent"], stats["official_sent"], stats["business_sent"], stats["retried"],
        stats["failed"], stats["blocked"], stats["skipped"], stats["rate_limit_events"],
        stats["remaining"], int(safe_delivery.GLOBAL_MIN_INTERVAL),
        int(safe_delivery.BUSINESS_MIN_INTERVAL),
    )
    return stats


def alert_worker():
    last_target = None
    bot.logger.warning(
        "DAILY_QUIZ_ALERT_WORKER safety_mode=on daily_dm=1 dedupe_across_routes=on "
        "officialbot_preferred=on direct_business_dm=recent_inbound_secondary "
        "business_min=%sm afternoon_dm=OFF evening_dm=OFF "
        "today_immediate_resume=on future_queue_starts_after_21:05_result_announcement "
        "stop=20:30_target_day admin_reports=start+progress+final account_priority=on",
        int(safe_delivery.BUSINESS_MIN_INTERVAL // 60),
    )

    while True:
        try:
            if schedule.SCHEDULE_ENABLED:
                _ensure_channel_for_today()
                local = _local_now()
                target_day, source = _target_day_for_queue(local)

                if target_day is not None and target_day != last_target:
                    _dispatch_cycle(target_day, source)
                    last_target = target_day
        except Exception:
            bot.logger.exception("DAILY_QUIZ_ALERT_WORKER_FAILED")
        time.sleep(30)


# Fixed production media manager: exact approved Telegram file_ids only.
import channel_media_manager as channel_media_manager
channel_media_manager.install(v110, schedule)

# Admin-only private end-to-end media test. Never posts to the public channel.
import channel_media_private_test as channel_media_private_test
channel_media_private_test.install()
