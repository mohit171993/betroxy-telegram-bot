"""Independent public-channel schedule for BETROXY Daily Quiz.

Public channel posts are intentionally separate from private DM queues so slow
OfficialBot/Business pacing can never delay the channel. Private 4 PM / 7 PM DMs
remain disabled.

Schedule (Asia/Kolkata):
- 10:00 — Quiz Open
- 16:00 — Afternoon / Top 3 reminder
- 19:00 — Last Chance
- 21:05 — Final Results (handled by daily_quiz_schedule)
"""
from datetime import datetime, timezone, timedelta
import time

import bot
import daily_quiz_schedule as schedule

v110 = schedule.v110
IST_OFFSET_HOURS = 5.5

SLOTS = (
    ("open", 10, "quiz_alert_open"),
    ("afternoon", 16, "quiz_alert_afternoon"),
    ("last_chance", 19, "quiz_alert_last_chance"),
)

_next_retry_at = {kind: 0.0 for kind, _, _ in SLOTS}


def _local_now():
    return datetime.now(timezone.utc) + timedelta(hours=IST_OFFSET_HOURS)


def _message(kind):
    if kind == "afternoon":
        return (
            "🏆 <b>Can You Reach Today's Top 3?</b>\n\n"
            "The BETROXY Daily Quiz leaderboard is still moving.\n\n"
            "🎁 <b>₹1,000 Amazon Pay Gift Voucher prize pool</b>\n"
            "🥇 1st — ₹500\n"
            "🥈 2nd — ₹300\n"
            "🥉 3rd — ₹200\n\n"
            "7 questions • 30 seconds each • one attempt today\n"
            "⏰ Entries close at <b>9:00 PM IST</b>.\n"
            "🏆 Play now and put your score on the leaderboard."
        )
    if kind == "last_chance":
        return (
            "⏰ <b>Only 2 Hours Left — Final Call</b>\n\n"
            "Today's BETROXY Daily Quiz closes at <b>9:00 PM IST</b>.\n\n"
            "🎁 <b>₹1,000 Amazon Pay Gift Voucher prize pool</b>\n"
            "🥇 1st — ₹500\n"
            "🥈 2nd — ₹300\n"
            "🥉 3rd — ₹200\n\n"
            "7 questions • 30 seconds each • one attempt today\n"
            "🔥 Final public reminder before today's leaderboard closes.\n"
            "🏆 Play now."
        )
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


def _button():
    return [[{
        "text": "🏆 Play Today's Quiz",
        "url": f"https://t.me/{bot.BOT_USERNAME}?start=dailyquiz",
    }]]


def _campaign():
    return schedule._today_campaign_windowed(test_mode=False)


def _send_once(kind, delivery_kind):
    global _next_retry_at
    if time.monotonic() < float(_next_retry_at.get(kind) or 0.0):
        return False

    campaign = _campaign()
    if v110._delivery_exists(campaign["id"], v110.CHANNEL_CHAT, delivery_kind):
        return True

    try:
        ok, data = schedule._send_text(v110.CHANNEL_CHAT, _message(kind), _button())
    except Exception:
        bot.logger.exception("PUBLIC_QUIZ_CHANNEL_POST_FAILED kind=%s exception=on", kind)
        _next_retry_at[kind] = time.monotonic() + 120.0
        return False

    if ok:
        mid = ((data.get("result") or {}).get("message_id")) if isinstance(data, dict) else None
        v110._mark_delivery(campaign["id"], v110.CHANNEL_CHAT, delivery_kind, mid)
        bot.logger.warning(
            "PUBLIC_QUIZ_CHANNEL_POST kind=%s sent=True channel=%s campaign=%s delivery=%s",
            kind, v110.CHANNEL_CHAT, campaign["id"], delivery_kind,
        )
        return True

    retry_after = 0
    if isinstance(data, dict):
        try:
            retry_after = int((data.get("parameters") or {}).get("retry_after") or 0)
        except Exception:
            retry_after = 0
    pause = max(120, retry_after + 120)
    _next_retry_at[kind] = time.monotonic() + pause
    bot.logger.warning(
        "PUBLIC_QUIZ_CHANNEL_POST_FAILED kind=%s retry_after=%ss safety_pause=%ss detail=%s",
        kind, retry_after, pause,
        (data or {}).get("description") if isinstance(data, dict) else data,
    )
    return False


def _current_slot(local):
    """Return only the current public slot; never stack old missed posts."""
    if 10 <= local.hour < 16:
        return SLOTS[0]
    if 16 <= local.hour < 19:
        return SLOTS[1]
    if 19 <= local.hour < 21:
        return SLOTS[2]
    return None


def channel_worker():
    bot.logger.warning(
        "PUBLIC_QUIZ_CHANNEL_SCHEDULE active=on channel=%s open=10:00 afternoon=16:00 "
        "last_chance=19:00 result=21:05_IST result_worker=daily_quiz_schedule "
        "private_4pm_dm=OFF private_7pm_dm=OFF independent_of_dm_queue=on",
        v110.CHANNEL_CHAT,
    )
    while True:
        try:
            if schedule.SCHEDULE_ENABLED:
                local = _local_now()
                slot = _current_slot(local)
                if slot:
                    kind, _hour, delivery_kind = slot
                    _send_once(kind, delivery_kind)
        except Exception:
            bot.logger.exception("PUBLIC_QUIZ_CHANNEL_WORKER_FAILED")
        time.sleep(20)
