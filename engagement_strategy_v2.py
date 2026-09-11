"""BETROXY production engagement strategy.

Policy:
- public channel remains the high-frequency surface
- at most one proactive private engagement message per user per IST day
- Daily Quiz subscribers get priority for one quiz-opening DM while today's quiz is open
- old points-based Sports Challenge / weekly points leaderboard is disabled
- promotions: at most about once per 7 days
- sports: at most about once per 2 days
- reactivation: after about 7 days, quiz-first
- generic re-engagement: only after about 3 days of inactivity/contact gap
- Business one-time follow-up and opt-in flows remain separate

Transactional messages (quiz questions/results, winner notices, support replies) are
not part of this proactive engagement cap.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bot

_INSTALLED = False
IST = timezone(timedelta(hours=5, minutes=30))
DAILY_QUIZ_URL = "https://t.me/BetroxyOfficialBot?start=dailyquiz"
PROACTIVE_ACTIONS = ("quiz", "promotion", "sports", "reminder", "reactivation", "leaderboard")


def _india_now():
    return datetime.now(IST)


def _daily_proactive_sent(uid):
    """Count proactive private engagement messages sent during the current IST day."""
    now = _india_now()
    start_local = datetime(now.year, now.month, now.day, tzinfo=IST)
    start_utc = start_local.astimezone(timezone.utc)
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS n
                FROM engagement_log
                WHERE telegram_user_id=%s
                  AND status='sent'
                  AND sent_at >= %s
                  AND action_type = ANY(%s)
                """,
                (int(uid), start_utc, list(PROACTIVE_ACTIONS)),
            )
            return int((cur.fetchone() or {}).get("n") or 0)


def _completed_today(v110, campaign_id, uid):
    try:
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 1
                    FROM v110_quiz_entries
                    WHERE campaign_id=%s AND telegram_user_id=%s AND completed_at IS NOT NULL
                    LIMIT 1
                    """,
                    (int(campaign_id), int(uid)),
                )
                return bool(cur.fetchone())
    except Exception:
        bot.logger.exception("ENGAGEMENT_V2_COMPLETION_CHECK_FAILED uid=%s", uid)
        return False


def install(v83, daily_schedule):
    global _INSTALLED
    if _INSTALLED:
        return

    original_prepare = v83._prepare_for_contact
    v110 = daily_schedule.v110

    # Keep ignore/suppression/unreachable protection from the existing engine,
    # but replace its old 3/week cap with a strict 1 proactive DM per IST day.
    def _prepare_for_contact_v2(lead, _max_weekly):
        if not original_prepare(lead, 999999):
            return False
        uid = int(lead["telegram_user_id"])
        return _daily_proactive_sent(uid) < 1

    def _send_daily_quiz_open(lead):
        uid = int(lead["telegram_user_id"])
        local = _india_now()
        if not (10 <= local.hour < 21):
            return False
        try:
            campaign = daily_schedule._today_campaign_windowed(test_mode=False)
        except Exception:
            bot.logger.exception("ENGAGEMENT_V2_CAMPAIGN_LOOKUP_FAILED uid=%s", uid)
            return False
        if not campaign or str(campaign.get("status") or "open") == "closed":
            return False
        if _completed_today(v110, campaign["id"], uid):
            return False

        key = f"daily_quiz_open:{local.strftime('%Y-%m-%d')}"
        text = (
            "🏆 <b>BETROXY DAILY QUIZ IS OPEN</b>\n\n"
            "🎁 <b>₹1,000 DAILY PRIZE POOL</b>\n"
            "🥇 ₹500   •   🥈 ₹300   •   🥉 ₹200\n\n"
            "7 questions • 30 seconds each • one attempt\n"
            "💯 Free to participate — no deposit or wager required.\n\n"
            "⏰ <b>Open until 9:00 PM IST</b>"
        )
        kb = [
            [{"text": "🏆 Play Today's Quiz", "url": DAILY_QUIZ_URL}],
            [{"text": "🔕 Preferences", "url": v83.PREFERENCES_DEEPLINK}],
        ]
        return v83._send_claimed(uid, "quiz", key, text, kb)

    # Defensive replacement: if any legacy path still calls _send_quiz, it now
    # routes to the real Daily Quiz rather than the old points trivia system.
    def _send_quiz_v2(lead):
        return _send_daily_quiz_open(lead)

    def _send_next_best_actions_v2(settings):
        now_utc = datetime.now(timezone.utc)
        for lead in v83._subscribed_rows():
            uid = int(lead["telegram_user_id"])
            if not _prepare_for_contact_v2(lead, 7):
                continue

            last_seen = lead.get("last_seen_at") or now_utc
            inactive = now_utc - last_seen
            last_contact = lead.get("last_contact_at")
            since_contact = now_utc - last_contact if last_contact else timedelta(days=999)
            local = _india_now()

            # 1) Seven-day reactivation: lead with the real Daily Quiz and prize pool.
            if settings.get("reactivation_enabled") and inactive >= timedelta(days=7) and since_contact >= timedelta(days=7):
                key = f"reactivation_quiz:{local.strftime('%Y-%m-%d')}"
                text = (
                    "🏆 <b>BETROXY DAILY QUIZ</b>\n\n"
                    "Come back and take today's free 7-question challenge.\n"
                    "🎁 <b>₹1,000 daily prize pool</b> — ₹500 / ₹300 / ₹200.\n\n"
                    "⏰ Quiz runs daily from 10:00 AM to 9:00 PM IST."
                )
                kb = [
                    [{"text": "🏆 Play Today's Quiz", "url": DAILY_QUIZ_URL}],
                    [{"text": "🎧 Support", "url": v83.SUPPORT_URL}],
                ]
                if v83._send_claimed(uid, "reactivation", key, text, kb):
                    continue

            # 2) Daily Quiz subscribers: one opening DM per day, if not completed.
            if settings.get("quiz_enabled") and lead.get("quiz_rewards"):
                if _send_daily_quiz_open(lead):
                    continue

            # 3) Promotions: purpose-specific, no more than about once per week.
            last_promo = v83._last_action_at(uid, "promotion")
            if (
                settings.get("promotions_enabled")
                and lead.get("promotions")
                and (not last_promo or now_utc - last_promo >= timedelta(days=7))
                and since_contact >= timedelta(hours=24)
            ):
                key = f"promotion:{local.strftime('%Y-%m-%d')}"
                kb = [
                    [{"text": "🎁 View Promotions", "url": v83.PROMOTIONS_URL}],
                    [{"text": "🔕 Preferences", "url": v83.PREFERENCES_DEEPLINK}],
                ]
                if v83._send_claimed(
                    uid,
                    "promotion",
                    key,
                    "🎁 <b>BETROXY Promotions</b>\n\nOpen Promotions to see the offers currently available to you.",
                    kb,
                ):
                    continue

            # 4) Sports: purpose-specific, no more than about once every 2 days.
            last_sports = v83._last_action_at(uid, "sports")
            if (
                settings.get("sports_enabled")
                and lead.get("sports_updates")
                and (not last_sports or now_utc - last_sports >= timedelta(days=2))
                and since_contact >= timedelta(hours=24)
            ):
                key = f"sports:{local.strftime('%Y-%m-%d')}"
                kb = [
                    [{"text": "🏏 Open Sportsbook", "url": v83.SPORTSBOOK_URL}],
                    [{"text": "🏆 Play Daily Quiz", "url": DAILY_QUIZ_URL}],
                ]
                if v83._send_claimed(
                    uid,
                    "sports",
                    key,
                    "🏏 <b>BETROXY Sports Update</b>\n\nLive fixtures and sports markets are available in Sportsbook. The free Daily Quiz is also available every day.",
                    kb,
                ):
                    continue

            # 5) Low-frequency fallback re-engagement, not a daily nag.
            if settings.get("reminders_enabled") and inactive >= timedelta(days=3) and since_contact >= timedelta(days=3):
                key = f"reminder_quiz:{local.strftime('%Y-%m-%d')}"
                kb = [
                    [{"text": "🏆 Play Daily Quiz", "url": DAILY_QUIZ_URL}],
                    [{"text": "🎧 Help & Support", "url": v83.SUPPORT_URL}],
                ]
                v83._send_claimed(
                    uid,
                    "reminder",
                    key,
                    "🏆 <b>Ready for today's BETROXY challenge?</b>\n\nThe Daily Quiz has a ₹1,000 prize pool and is free to enter. If you need help, support is one tap away.",
                    kb,
                )

    def _weekly_leaderboard_disabled(_settings):
        # The old points/trivia leaderboard belonged to the retired engagement quiz.
        # Final Daily Quiz rankings/winners are handled by the production quiz flow.
        return None

    # Align the admin dashboard's old weekly safety cap with the new one-per-day policy.
    try:
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE engagement_engine_settings SET max_weekly_messages=7, updated_at=NOW() WHERE id=1")
            conn.commit()
    except Exception:
        bot.logger.exception("ENGAGEMENT_V2_SETTINGS_UPDATE_FAILED")

    v83._prepare_for_contact = _prepare_for_contact_v2
    v83._send_quiz = _send_quiz_v2
    v83._send_next_best_actions = _send_next_best_actions_v2
    v83._send_weekly_leaderboard = _weekly_leaderboard_disabled

    _INSTALLED = True
    bot.logger.warning(
        "ENGAGEMENT_STRATEGY_V2 active=on private_daily_cap=1 quiz_priority=on daily_quiz_prize=1000 "
        "old_points_quiz=off old_weekly_points_leaderboard=off promotions=7d sports=2d reactivation=7d generic_reminder=3d "
        "business_followup=separate optin=separate transactional_excluded=on"
    )
