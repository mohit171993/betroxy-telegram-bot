"""BETROXY Telegram Business follow-up policy.

Send one text-only follow-up for open/unresolved Business enquiries after 2 hours,
while preserving the existing 22-hour recent-chat ceiling, dedupe key and global
engagement quiet-hours guard.

The follow-up prioritises the free BETROXY Daily Quiz and real prize details,
with Promotions and Support kept as secondary actions.
"""
import time
from datetime import datetime, timedelta, timezone

import bot
import v83_autopilot_engagement as v83

IST_OFFSET_HOURS = 5.5
DAILY_QUIZ_URL = "https://t.me/BetroxyOfficialBot?start=dailyquiz"


def _quiz_is_open_now():
    now_ist = datetime.now(timezone.utc) + timedelta(hours=IST_OFFSET_HOURS)
    minutes = now_ist.hour * 60 + now_ist.minute
    return 10 * 60 <= minutes < 21 * 60


def _followup_content():
    quiz_open = _quiz_is_open_now()
    kb = [
        [{"text": "🏆 Play Today's Quiz" if quiz_open else "🏆 Daily Quiz", "url": DAILY_QUIZ_URL}],
        [{"text": "🎁 Promotions", "url": "https://t.me/BetroxyBot/promotions"}],
        [{"text": "🎧 Support", "url": v83.SUPPORT_URL}],
    ]

    if quiz_open:
        text = (
            "🏆 <b>Today's BETROXY Daily Quiz is LIVE</b>\n\n"
            "🎁 <b>₹1,000 DAILY PRIZE POOL</b>\n"
            "🥇 ₹500  •  🥈 ₹300  •  🥉 ₹200\n\n"
            "🧠 7 questions  •  ⏱ 30 seconds each  •  one attempt\n"
            "⏰ Open until <b>9:00 PM IST</b>\n"
            "✅ Free to participate — no deposit or wager required.\n\n"
            "Tap below to play today's quiz."
        )
    else:
        text = (
            "🏆 <b>BETROXY Daily Quiz</b>\n\n"
            "🎁 <b>₹1,000 DAILY PRIZE POOL</b>\n"
            "🥇 ₹500  •  🥈 ₹300  •  🥉 ₹200\n\n"
            "🧠 7 questions  •  ⏱ 30 seconds each  •  one attempt\n"
            "📅 <b>Next quiz starts at 10:00 AM IST</b>\n"
            "✅ Free to participate — no deposit or wager required.\n\n"
            "Tap below to open the Daily Quiz."
        )
    return text, kb, quiz_open


def _send_business_recent_followups_2h(limit=8):
    """Send one follow-up to open Business enquiries that are 2-22 hours old."""
    try:
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.telegram_business_enquiries') AS t")
                if not (cur.fetchone() or {}).get("t"):
                    return
                cur.execute(
                    """
                    SELECT e.*
                    FROM telegram_business_enquiries e
                    WHERE e.customer_user_id IS NOT NULL
                      AND COALESCE(e.status,'open')='open'
                      AND e.last_message_at BETWEEN NOW()-INTERVAL '22 hours' AND NOW()-INTERVAL '2 hours'
                      AND NOT EXISTS (
                        SELECT 1
                        FROM engagement_log g
                        WHERE g.telegram_user_id=e.customer_user_id
                          AND g.message_key=('business_followup:'||e.id::text)
                      )
                    ORDER BY e.last_message_at DESC
                    LIMIT %s
                    """,
                    (int(limit),),
                )
                rows = cur.fetchall()

        text, kb, quiz_open = _followup_content()

        sent = 0
        for enquiry in rows:
            uid = int(enquiry["customer_user_id"])
            key = f"business_followup:{int(enquiry['id'])}"
            job_id = v83._claim_job(uid, "business_followup", key)
            if not job_id:
                continue
            ok, data = v83._tg_send(
                int(enquiry["customer_chat_id"]),
                text,
                kb,
                business_connection_id=str(enquiry["connection_id"]),
            )
            v83._finish_job(job_id, ok, data)
            if ok:
                sent += 1
            time.sleep(0.08)

        if rows:
            bot.logger.warning(
                "BUSINESS_FOLLOWUP_2H_SCAN eligible=%s sent=%s open_only=on media=text_only quiz_focus=on quiz_open=%s prize_pool=1000",
                len(rows), sent, quiz_open,
            )
        return sent
    except Exception:
        bot.logger.exception("BUSINESS_FOLLOWUP_2H_FAILED")
        return 0


def install():
    v83._send_business_recent_followups = _send_business_recent_followups_2h
    bot.logger.warning(
        "BUSINESS_FOLLOWUP_POLICY active=on delay=2h max_age=22h open_only=on media=text_only quiet_hours=global "
        "quiz_focus=on prize_pool=1000 direct_quiz_link=on"
    )
    return _send_business_recent_followups_2h
