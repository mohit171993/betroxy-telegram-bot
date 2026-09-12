"""Account-safety-first daily quiz reminder policy.

Production policy:
- one automated quiz DM per day only
- OfficialBot users only
- no automated Telegram Business DMs
- no 4 PM or 7 PM DM reminder campaigns
- sends are staggered very slowly through safe_reminder_delivery
- campaign can resume after a restart without duplicating delivered messages
- admin receives a start/resume report and a completion/partial report
"""
from datetime import datetime, timezone, timedelta
import time

import bot
import daily_quiz_schedule as schedule
import safe_reminder_delivery as safe_delivery

v110 = schedule.v110
v83 = v110.v83

INDIA_OFFSET_HOURS = 5.5
START_HOUR = 10
STOP_HOUR = 20
STOP_MINUTE = 30

schedule.DUBAI_OFFSET = INDIA_OFFSET_HOURS
v110.TZ_OFFSET = INDIA_OFFSET_HOURS
v83.TZ_OFFSET = INDIA_OFFSET_HOURS

# Install before any production alert worker starts. The shared safety layer
# enforces >=60 seconds between automated OfficialBot DMs and hard-blocks
# automated Telegram Business DMs.
safe_delivery.install(v83)


def _local_now():
    return datetime.now(timezone.utc) + timedelta(hours=INDIA_OFFSET_HOURS)


def _key(day):
    return f"daily_quiz_open:{day.isoformat()}"


def _eligible_users():
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


def _sent_ids(message_key):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT telegram_user_id
                FROM engagement_log
                WHERE message_key=%s AND status='sent'
            """, (str(message_key),))
            return {int(r["telegram_user_id"]) for r in cur.fetchall()}


def _send_official(uid, message_key, text):
    rows = [[{"text": "🏆 Play Today's Quiz", "url": f"https://t.me/{bot.BOT_USERNAME}?start=dailyquiz"}]]
    try:
        return safe_delivery.send_claimed_result(
            uid,
            "quiz_rewards",
            message_key,
            text,
            rows,
            channel="officialbot",
            detail="single daily quiz reminder; account-safety staggered delivery",
        )
    except Exception:
        bot.logger.exception("DAILY_QUIZ_SAFE_DM_FAILED uid=%s key=%s", uid, message_key)
        return {"sent": False, "status": "failed", "retried": 0, "permanent": False}


def _send_channel_once(campaign, text):
    delivery_kind = "quiz_alert_open"
    if v110._delivery_exists(campaign["id"], v110.CHANNEL_CHAT, delivery_kind):
        return False
    ok, data = schedule._send_text(
        v110.CHANNEL_CHAT,
        text,
        [[{"text": "🏆 Play Today's Quiz", "url": f"https://t.me/{bot.BOT_USERNAME}?start=dailyquiz"}]],
    )
    if ok:
        mid = ((data.get("result") or {}).get("message_id")) if isinstance(data, dict) else None
        v110._mark_delivery(campaign["id"], v110.CHANNEL_CHAT, delivery_kind, mid)
    return ok


def _message():
    return (
        "🏆 <b>Today's BETROXY Daily Quiz is OPEN</b>\n\n"
        "Play anytime today until <b>9:00 PM IST</b>.\n\n"
        "🎁 <b>₹1,000 Amazon Pay Gift Voucher prize pool</b>\n"
        "🥇 1st — ₹500\n"
        "🥈 2nd — ₹300\n"
        "🥉 3rd — ₹200\n\n"
        "7 questions • 30 seconds each • one attempt today\n"
        "💯 Free to participate — no deposit or wager required."
    )


def _admin_notice(text):
    try:
        ok, _ = schedule._send_text(bot.ADMIN_ID, text)
        bot.logger.warning("DAILY_QUIZ_ADMIN_REPORT sent=%s", ok)
        return ok
    except Exception:
        bot.logger.exception("DAILY_QUIZ_ADMIN_REPORT_FAILED")
        return False


def _past_stop_time(local=None):
    local = local or _local_now()
    if local.hour > STOP_HOUR:
        return True
    return local.hour == STOP_HOUR and local.minute >= STOP_MINUTE


def _dispatch_daily():
    local = _local_now()
    campaign = schedule._today_campaign_windowed(test_mode=False)
    message_key = _key(local.date())
    text = _message()

    # Public channel post is one-per-day and does not create a DM burst.
    channel = _send_channel_once(campaign, text)

    users = _eligible_users()
    already = _sent_ids(message_key)
    pending = [uid for uid in users if uid not in already]
    estimated_minutes = int(round((len(pending) * safe_delivery.OFFICIAL_MIN_INTERVAL) / 60.0)) if pending else 0

    if not pending:
        bot.logger.warning(
            "DAILY_QUIZ_SAFE_REMINDER_COMPLETE eligible=%s sent_before=%s remaining=0 business_dm=OFF afternoon=OFF evening=OFF channel=%s",
            len(users), len(already), channel,
        )
        return {
            "eligible": len(users), "sent": 0, "already_sent": len(already),
            "retried": 0, "failed": 0, "blocked": 0, "remaining": 0,
        }

    _admin_notice(
        "🛡 <b>BETROXY Daily Reminder Started</b>\n\n"
        f"Eligible OfficialBot users: <b>{len(users)}</b>\n"
        f"Already delivered today: <b>{len(already)}</b>\n"
        f"Remaining now: <b>{len(pending)}</b>\n"
        f"Minimum gap: <b>{int(safe_delivery.OFFICIAL_MIN_INTERVAL)} seconds</b> per DM\n"
        f"Estimated remaining time: <b>~{estimated_minutes} minutes</b>\n\n"
        "Direct Business DM: <b>OFF</b>\n"
        "4 PM reminder: <b>OFF</b>\n"
        "7 PM reminder: <b>OFF</b>\n\n"
        "Account protection has priority. Telegram RetryAfter will pause the queue automatically."
    )

    stats = {
        "eligible": len(users),
        "sent": 0,
        "already_sent": len(already),
        "retried": 0,
        "failed": 0,
        "blocked": 0,
        "remaining": len(pending),
    }

    for index, uid in enumerate(pending, 1):
        if _past_stop_time():
            bot.logger.warning(
                "DAILY_QUIZ_SAFE_REMINDER_CUTOFF processed=%s pending_total=%s cutoff=%02d:%02d_IST",
                index - 1, len(pending), STOP_HOUR, STOP_MINUTE,
            )
            break

        result = _send_official(uid, message_key, text)
        stats["retried"] += int(result.get("retried") or 0)
        if result.get("sent"):
            stats["sent"] += 1
        elif str(result.get("status") or "") == "already_sent":
            stats["already_sent"] += 1
        elif str(result.get("status") or "") == "in_progress":
            pass
        else:
            stats["failed"] += 1
            if result.get("permanent"):
                stats["blocked"] += 1

        # The shared delivery layer already enforces the minimum interval before
        # each real send. This tiny sleep only yields CPU between dedupe results.
        time.sleep(0.1)

    final_sent_ids = _sent_ids(message_key)
    stats["remaining"] = max(0, len([uid for uid in users if uid not in final_sent_ids]))

    _admin_notice(
        "✅ <b>BETROXY Daily Reminder Report</b>\n\n"
        f"Eligible: <b>{stats['eligible']}</b>\n"
        f"Delivered in this run: <b>{stats['sent']}</b>\n"
        f"Already delivered earlier: <b>{stats['already_sent']}</b>\n"
        f"Retries used: <b>{stats['retried']}</b>\n"
        f"Failed: <b>{stats['failed']}</b>\n"
        f"Blocked/unreachable: <b>{stats['blocked']}</b>\n"
        f"Still remaining: <b>{stats['remaining']}</b>\n\n"
        "Direct Business DM: <b>OFF</b> • 4 PM: <b>OFF</b> • 7 PM: <b>OFF</b>"
    )

    bot.logger.warning(
        "DAILY_QUIZ_SAFE_REMINDER eligible=%s sent=%s already_sent=%s retried=%s failed=%s blocked=%s remaining=%s business_dm=OFF afternoon=OFF evening=OFF interval=%ss channel=%s",
        stats["eligible"], stats["sent"], stats["already_sent"], stats["retried"],
        stats["failed"], stats["blocked"], stats["remaining"],
        int(safe_delivery.OFFICIAL_MIN_INTERVAL), channel,
    )
    return stats


def alert_worker():
    last_run_day = None
    bot.logger.warning(
        "DAILY_QUIZ_ALERT_WORKER safety_mode=on daily_dm=1 officialbot_only=on direct_business_dm=OFF "
        "afternoon_dm=OFF evening_dm=OFF start=10:00 stop=20:30 tz=Asia/Kolkata interval=%ss admin_reports=on account_priority=on",
        int(safe_delivery.OFFICIAL_MIN_INTERVAL),
    )
    while True:
        try:
            if schedule.SCHEDULE_ENABLED:
                now = _local_now()
                in_window = (
                    now.hour >= START_HOUR
                    and not _past_stop_time(now)
                )
                if in_window and now.date() != last_run_day:
                    _dispatch_daily()
                    last_run_day = now.date()
        except Exception:
            bot.logger.exception("DAILY_QUIZ_ALERT_WORKER_FAILED")
        time.sleep(30)


# Fixed production media manager: exact approved Telegram file_ids only.
import channel_media_manager as channel_media_manager
channel_media_manager.install(v110, schedule)

# Admin-only private end-to-end media test. Never posts to the public channel.
import channel_media_private_test as channel_media_private_test
channel_media_private_test.install()
