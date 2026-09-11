import threading
import time

import bot
import v86_live_sports_autopilot as v86

v83 = v86.v83
v85 = v86.v85
v63 = v86.v63
_previous_worker_cycle = v83._worker_cycle

REMINDER_KEY = "optin_reminder_1"


def _send_single_optin_reminder(limit=12):
    """Send one gentle preference reminder 3+ days after an ignored first invite."""
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT l.*
                FROM intelligence_leads l
                WHERE l.reachable_bot=TRUE
                  AND l.opt_out=FALSE
                  AND COALESCE(l.lifecycle_stage,'') NOT IN ('suppressed','unreachable','opted_out')
                  AND l.last_seen_at <= NOW()-INTERVAL '24 hours'
                  AND NOT EXISTS (
                      SELECT 1 FROM engagement_subscriptions s
                      WHERE s.telegram_user_id=l.telegram_user_id
                        AND s.consent_at IS NOT NULL
                  )
                  AND EXISTS (
                      SELECT 1 FROM engagement_log e
                      WHERE e.telegram_user_id=l.telegram_user_id
                        AND e.message_key='optin_invite'
                        AND e.status='sent'
                        AND e.sent_at <= NOW()-INTERVAL '3 days'
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM engagement_log e2
                      WHERE e2.telegram_user_id=l.telegram_user_id
                        AND e2.message_key=%s
                  )
                ORDER BY l.last_seen_at DESC
                LIMIT %s
                """,
                (REMINDER_KEY, int(limit)),
            )
            rows = cur.fetchall()

    keyboard = [
        [{"text": "🏆 Join Daily Quiz", "url": "https://t.me/BetroxyOfficialBot?start=dailyquiz"}],
        [{"text": "🔔 Choose My Updates", "url": v83.PREFERENCES_DEEPLINK}],
    ]
    text = (
        "🏆 <b>Want BETROXY quiz & reward updates?</b>\n\n"
        "The Daily Quiz has a <b>₹1,000 prize pool</b> — ₹500 / ₹300 / ₹200 — "
        "and is free to participate. Choose only the updates you want: Sports, Promotions or Quiz & Rewards.\n\n"
        "No selection means no recurring updates, and you can stop anytime."
    )

    sent = 0
    for lead in rows:
        if v83._send_claimed(int(lead["telegram_user_id"]), "optin", REMINDER_KEY, text, keyboard):
            sent += 1
        time.sleep(0.08)
    if sent:
        bot.logger.warning("V87_OPTIN_REMINDER sent=%s daily_quiz_focus=on", sent)
    return sent


def _v87_worker_cycle(force=False):
    _previous_worker_cycle(force=force)
    try:
        settings = v83._settings()
        if not settings.get("master_enabled"):
            return
        if not force and not v83._within_send_hours(settings):
            return
        _send_single_optin_reminder(limit=25 if force else 12)
    except Exception:
        bot.logger.exception("V87_OPTIN_REMINDER_CYCLE_FAILED")


v83._worker_cycle = _v87_worker_cycle

bot.logger.warning(
    "V87_SINGLE_OPTIN_REMINDER active=on delay=3d max_reminders=1 consent_required_after_reminder=on daily_quiz_focus=on"
)


if __name__ == "__main__":
    v63.apply_signup_cta()
    v85._enable_smart_reply_without_reset()
    threading.Thread(target=v83._worker_loop, name="betroxy-engagement-worker", daemon=True).start()
    bot.logger.warning("V87 polling handover delay=12s")
    time.sleep(12)
    bot.main()
