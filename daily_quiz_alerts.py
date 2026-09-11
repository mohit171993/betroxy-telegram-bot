"""Daily quiz alerts: 10:00 and 19:00 Asia/Dubai.

10:00 -> all reachable bot users + channel.
19:00 -> reachable users who have not completed today's quiz + channel.
Uses engagement_log message keys for once-per-day delivery.
"""
import time
from datetime import datetime, timezone, timedelta

import bot
import daily_quiz_schedule as schedule

v110 = schedule.v110
v83 = v110.v83


def _local_now():
    return datetime.now(timezone.utc) + timedelta(hours=4)


def _key(kind, day):
    return f"daily_quiz_{kind}:{day.isoformat()}"


def _eligible_users(campaign_id, reminder=False):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            if reminder:
                cur.execute("""
                    SELECT DISTINCT l.telegram_user_id
                    FROM intelligence_leads l
                    WHERE l.reachable_bot=TRUE AND l.opt_out=FALSE
                      AND COALESCE(l.lifecycle_stage,'') NOT IN ('suppressed','unreachable','opted_out')
                      AND NOT EXISTS (
                        SELECT 1 FROM v110_quiz_entries e
                        WHERE e.campaign_id=%s AND e.telegram_user_id=l.telegram_user_id
                          AND e.completed_at IS NOT NULL
                      )
                """, (int(campaign_id),))
            else:
                cur.execute("""
                    SELECT DISTINCT l.telegram_user_id
                    FROM intelligence_leads l
                    WHERE l.reachable_bot=TRUE AND l.opt_out=FALSE
                      AND COALESCE(l.lifecycle_stage,'') NOT IN ('suppressed','unreachable','opted_out')
                """)
            return [int(r["telegram_user_id"]) for r in cur.fetchall()]


def _send_dm(uid, message_key, text):
    rows = [[{"text": "🏆 Play Today's Quiz", "url": f"https://t.me/{bot.BOT_USERNAME}?start=dailyquiz"}]]
    try:
        return bool(v83._send_claimed(uid, "quiz_rewards", message_key, text, rows))
    except Exception:
        bot.logger.exception("DAILY_QUIZ_ALERT_DM_FAILED uid=%s key=%s", uid, message_key)
        return False


def _send_channel_once(campaign, kind, text):
    delivery_kind = f"quiz_alert_{kind}"
    if v110._delivery_exists(campaign["id"], v110.CHANNEL_CHAT, delivery_kind):
        return False
    ok, data = schedule._send_text(v110.CHANNEL_CHAT, text, [[{"text": "🏆 Play Today's Quiz", "url": f"https://t.me/{bot.BOT_USERNAME}?start=dailyquiz"}]])
    if ok:
        mid = ((data.get("result") or {}).get("message_id")) if isinstance(data, dict) else None
        v110._mark_delivery(campaign["id"], v110.CHANNEL_CHAT, delivery_kind, mid)
    return ok


def _dispatch(kind):
    local = _local_now()
    campaign = schedule._today_campaign_windowed(test_mode=False)
    reminder = kind == "reminder"
    key = _key(kind, local.date())
    if reminder:
        text = (
            "⏰ <b>BETROXY Daily Quiz — Last Chance Today</b>\n\n"
            "You haven't completed today's 7-question challenge yet.\n\n"
            "🎁 <b>₹1,000 Amazon Pay Gift Voucher prize pool</b>\n"
            "🥇 1st — ₹500\n"
            "🥈 2nd — ₹300\n"
            "🥉 3rd — ₹200\n\n"
            "⏱ 30 seconds per question\n"
            "💯 Free to participate — no deposit or wager required\n"
            "⏰ Closes at <b>9:00 PM Dubai time</b>\n\n"
            "🏆 Play now before entries close."
        )
    else:
        text = (
            "🏆 <b>Today's BETROXY Daily Quiz is OPEN</b>\n\n"
            "Play anytime today until <b>9:00 PM Dubai time</b>.\n\n"
            "🎁 <b>₹1,000 Amazon Pay Gift Voucher prize pool</b>\n"
            "🥇 1st — ₹500\n"
            "🥈 2nd — ₹300\n"
            "🥉 3rd — ₹200\n\n"
            "7 questions • 30 seconds each • one attempt today\n"
            "💯 Free to participate — no deposit or wager required."
        )
    users = _eligible_users(campaign["id"], reminder=reminder)
    sent = 0
    for uid in users:
        if _send_dm(uid, key, text):
            sent += 1
        time.sleep(0.06)
    channel = _send_channel_once(campaign, kind, text)
    bot.logger.warning("DAILY_QUIZ_ALERT kind=%s dm_sent=%s eligible=%s channel=%s prizes=amazonpay_500_300_200", kind, sent, len(users), channel)


def alert_worker():
    last_slot = None
    bot.logger.warning("DAILY_QUIZ_ALERT_WORKER active=on open=10:00 reminder=19:00 tz=Asia/Dubai prizes=amazonpay_500_300_200")
    while True:
        try:
            if schedule.SCHEDULE_ENABLED:
                now = _local_now()
                slot = None
                if now.hour == 10 and now.minute < 5:
                    slot = (now.date(), "open")
                elif now.hour == 19 and now.minute < 5:
                    slot = (now.date(), "reminder")
                if slot and slot != last_slot:
                    _dispatch(slot[1])
                    last_slot = slot
        except Exception:
            bot.logger.exception("DAILY_QUIZ_ALERT_WORKER_FAILED")
        time.sleep(30)
