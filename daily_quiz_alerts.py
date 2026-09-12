"""Daily quiz alerts in India Standard Time (Asia/Kolkata).

10:00 IST -> all reachable OfficialBot users + eligible recent Business DMs + channel.
16:00 IST -> reachable OfficialBot users who have not completed today's quiz + channel.
19:00 IST -> reachable OfficialBot users who have not completed today's quiz + channel.

All automated DM delivery goes through the shared safe queue: conservative pacing,
Telegram RetryAfter handling, transient retries, failed-job recovery and per-user
message-key dedupe.
"""
from datetime import datetime, timezone, timedelta

import bot
import daily_quiz_schedule as schedule
import safe_reminder_delivery as safe_delivery

v110 = schedule.v110
v83 = v110.v83

INDIA_OFFSET_HOURS = 5.5

schedule.DUBAI_OFFSET = INDIA_OFFSET_HOURS
v110.TZ_OFFSET = INDIA_OFFSET_HOURS
v83.TZ_OFFSET = INDIA_OFFSET_HOURS

# Install before production starts any engagement/quiz-alert worker. This patches
# v83's automated send path as well, so reminders, sports, promotions,
# reactivation and quiz automation all share the same slow queue.
safe_delivery.install(v83)


def _local_now():
    return datetime.now(timezone.utc) + timedelta(hours=INDIA_OFFSET_HOURS)


def _key(kind, day):
    return f"daily_quiz_{kind}:{day.isoformat()}"


def _eligible_users(campaign_id, incomplete_only=False):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            if incomplete_only:
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


def _eligible_business_chats():
    """Recent direct Business DMs that Telegram currently allows us to reply to.

    Keep this deliberately conservative: enabled/reply-capable Business
    connection, open enquiry, a real inbound customer message in the last 23
    hours, and no known opt-out/suppression. Business DMs receive only the 10 AM
    opening reminder; 4 PM/7 PM stay on OfficialBot so we do not repeatedly push
    a Business conversation.
    """
    try:
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.telegram_business_enquiries') AS t")
                if not (cur.fetchone() or {}).get("t"):
                    return []
                cur.execute("""
                    SELECT DISTINCT ON (e.customer_user_id)
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
                        WHERE m.enquiry_id=e.id AND m.direction='inbound'
                    ) inbound ON inbound.last_inbound_at IS NOT NULL
                    LEFT JOIN intelligence_leads l
                      ON l.telegram_user_id=e.customer_user_id
                    WHERE e.customer_user_id IS NOT NULL
                      AND e.status='open'
                      AND c.is_enabled=TRUE
                      AND c.can_reply=TRUE
                      AND inbound.last_inbound_at >= NOW()-INTERVAL '23 hours'
                      AND COALESCE(l.opt_out,FALSE)=FALSE
                      AND COALESCE(l.lifecycle_stage,'') NOT IN ('suppressed','unreachable','opted_out')
                    ORDER BY e.customer_user_id, inbound.last_inbound_at DESC
                """)
                return cur.fetchall()
    except Exception:
        bot.logger.exception("DAILY_QUIZ_BUSINESS_ELIGIBILITY_FAILED")
        return []


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
        )
    except Exception:
        bot.logger.exception("DAILY_QUIZ_ALERT_DM_FAILED uid=%s key=%s", uid, message_key)
        return {"sent": False, "status": "failed", "retried": 0, "permanent": False}


def _send_business(row, message_key, text):
    uid = int(row["customer_user_id"])
    rows = [[{"text": "🏆 Play Today's Quiz", "url": f"https://t.me/{bot.BOT_USERNAME}?start=dailyquiz"}]]
    try:
        return safe_delivery.send_claimed_result(
            uid,
            "quiz_rewards",
            message_key,
            text,
            rows,
            chat_id=int(row["customer_chat_id"]),
            business_connection_id=str(row["connection_id"]),
            channel="business",
            detail="10am daily quiz opening reminder via recent direct Business DM",
        )
    except Exception:
        bot.logger.exception("DAILY_QUIZ_ALERT_BUSINESS_FAILED uid=%s key=%s", uid, message_key)
        return {"sent": False, "status": "failed", "retried": 0, "permanent": False}


def _send_channel_once(campaign, kind, text):
    delivery_kind = f"quiz_alert_{kind}"
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


def _message(kind):
    if kind == "afternoon":
        return (
            "🏆 <b>Can You Reach Today's Top 3?</b>\n\n"
            "The BETROXY Daily Quiz leaderboard is still moving — and you haven't completed today's challenge yet.\n\n"
            "🎁 <b>₹1,000 Amazon Pay Gift Voucher prize pool</b>\n"
            "🥇 1st — ₹500\n"
            "🥈 2nd — ₹300\n"
            "🥉 3rd — ₹200\n\n"
            "7 questions • 30 seconds each\n"
            "🎯 Accuracy comes first; hard-question accuracy and speed break ties\n"
            "💯 Free to participate — no deposit or wager required\n\n"
            "⏰ Entries close at <b>9:00 PM IST</b>.\n"
            "🏆 Play now and put your score on the leaderboard."
        )
    if kind == "last_chance":
        return (
            "⏰ <b>Only 2 Hours Left — Final Call</b>\n\n"
            "You still haven't completed today's BETROXY Daily Quiz. Entries close at <b>9:00 PM IST</b>.\n\n"
            "🎁 <b>₹1,000 Amazon Pay Gift Voucher prize pool</b>\n"
            "🥇 1st — ₹500\n"
            "🥈 2nd — ₹300\n"
            "🥉 3rd — ₹200\n\n"
            "7 questions • 30 seconds each • one attempt today\n"
            "💯 Free to participate — no deposit or wager required\n\n"
            "🔥 This is your last reminder for today's challenge.\n"
            "🏆 Play now before the leaderboard closes."
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


def _dispatch(kind, recovery_pass=0):
    local = _local_now()
    campaign = schedule._today_campaign_windowed(test_mode=False)
    incomplete_only = kind in {"afternoon", "last_chance"}
    key = _key(kind, local.date())
    text = _message(kind)

    # Channel first so slow DM pacing never delays the public 10/16/19 IST post.
    channel = _send_channel_once(campaign, kind, text)

    users = _eligible_users(campaign["id"], incomplete_only=incomplete_only)
    official_ids = set(users)
    business_rows = _eligible_business_chats() if kind == "open" else []
    # OfficialBot is always preferred when the same Telegram user exists in both
    # routes. This prevents cross-route duplicates and avoids using Business DM
    # as a workaround when a user has blocked the bot.
    business_rows = [r for r in business_rows if int(r["customer_user_id"]) not in official_ids]

    stats = {
        "sent": 0,
        "retried": 0,
        "failed": 0,
        "blocked": 0,
        "already_sent": 0,
        "in_progress": 0,
        "official_sent": 0,
        "business_sent": 0,
    }

    def count(result, route):
        status = str(result.get("status") or "failed")
        stats["retried"] += int(result.get("retried") or 0)
        if result.get("sent"):
            stats["sent"] += 1
            stats[f"{route}_sent"] += 1
        elif status == "already_sent":
            stats["already_sent"] += 1
        elif status == "in_progress":
            stats["in_progress"] += 1
        else:
            stats["failed"] += 1
            if result.get("permanent"):
                stats["blocked"] += 1

    for uid in users:
        count(_send_official(uid, key, text), "official")

    for row in business_rows:
        count(_send_business(row, key, text), "business")

    bot.logger.warning(
        "DAILY_QUIZ_ALERT kind=%s pass=%s official_eligible=%s business_eligible=%s sent=%s official_sent=%s business_sent=%s retried=%s failed=%s blocked=%s already_sent=%s in_progress=%s channel=%s tz=Asia/Kolkata prizes=amazonpay_500_300_200",
        kind,
        recovery_pass,
        len(users),
        len(business_rows),
        stats["sent"],
        stats["official_sent"],
        stats["business_sent"],
        stats["retried"],
        stats["failed"],
        stats["blocked"],
        stats["already_sent"],
        stats["in_progress"],
        channel,
    )
    return stats


def alert_worker():
    last_slot = None
    bot.logger.warning(
        "DAILY_QUIZ_ALERT_WORKER active=on open=10:00 afternoon=16:00 last_chance=19:00 "
        "recovery_window=30m recovery_passes=up_to_3 direct_business_10am=recent_23h_only "
        "safe_queue=on close=21:00 result=21:05 tz=Asia/Kolkata prizes=amazonpay_500_300_200"
    )
    while True:
        try:
            if schedule.SCHEDULE_ENABLED:
                now = _local_now()
                slot = None
                if now.hour == 10 and now.minute < 30:
                    slot = (now.date(), "open", now.minute // 10)
                elif now.hour == 16 and now.minute < 30:
                    slot = (now.date(), "afternoon", now.minute // 10)
                elif now.hour == 19 and now.minute < 30:
                    slot = (now.date(), "last_chance", now.minute // 10)
                if slot and slot != last_slot:
                    _dispatch(slot[1], recovery_pass=int(slot[2]))
                    last_slot = slot
        except Exception:
            bot.logger.exception("DAILY_QUIZ_ALERT_WORKER_FAILED")
        import time
        time.sleep(30)


# Fixed production media manager: exact approved Telegram file_ids only.
import channel_media_manager as channel_media_manager
channel_media_manager.install(v110, schedule)

# Admin-only private end-to-end media test. Never posts to the public channel.
import channel_media_private_test as channel_media_private_test
channel_media_private_test.install()
