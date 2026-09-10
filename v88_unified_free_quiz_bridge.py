import threading
import time

import bot
import v87_single_optin_reminder as v87

v86 = v87.v86
v83 = v87.v83
v85 = v87.v85
v63 = v87.v63
v75 = v83.v75
biz51 = v83.biz51

QUIZ_DEEPLINK = f"https://t.me/{v83.OFFICIAL_BOT}?start=freequiz"
TEST_USERNAME = "mohit_97saxena"

_old_start = bot.start
_old_public_menu = bot.public_menu
_old_business_menu = v75._business_menu
_old_business_reply_payload = v75._business_reply_payload
_old_optin_invites = v83._send_optin_invites
_old_business_followups = v83._send_business_recent_followups


def _insert_button(markup, text, *, url=None, callback_data=None, position=1):
    rows = [list(r) for r in markup.inline_keyboard]
    if any(any(text.lower() in str(getattr(b, "text", "")).lower() for b in row) for row in rows):
        return markup
    btn = bot.InlineKeyboardButton(text, url=url, callback_data=callback_data)
    rows.insert(min(max(0, position), len(rows)), [btn])
    return bot.InlineKeyboardMarkup(rows)


def v88_public_menu(user_id=None):
    markup = _old_public_menu(user_id)
    return _insert_button(markup, "🏆 Free Quiz & Rewards", callback_data="eng_preferences", position=1)


def v88_business_menu(styled=True):
    markup = _old_business_menu(styled=styled)
    return _insert_button(markup, "🏆 FREE Quiz & Rewards", url=QUIZ_DEEPLINK, position=1)


def v88_business_reply_payload(intent, first_reply=False):
    text, keyboard, stage = _old_business_reply_payload(intent, first_reply=first_reply)
    if first_reply or intent in {"greeting", "general"}:
        text = (
            text
            + "\n\n🏆 <b>FREE Sports Quiz & Rewards</b>\n"
              "Answer free sports questions, collect points and compete on the weekly leaderboard. "
              "No deposit or wager is required to join."
        )
        keyboard = v88_business_menu(styled=True)
    return text, keyboard, stage


def _lead(uid):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM intelligence_leads WHERE telegram_user_id=%s", (int(uid),))
            return cur.fetchone() or {"telegram_user_id": int(uid), "reachable_bot": True, "opt_out": False}


def _send_first_quiz(uid):
    lead = _lead(uid)
    try:
        settings = v83._settings()
        if settings.get("live_predictions_enabled", True):
            event = v86._next_live_event()
            if event and v86._send_live_prediction(lead, event):
                return "live"
    except Exception:
        bot.logger.exception("V88_INITIAL_LIVE_QUIZ_FAILED")
    try:
        if v83._send_quiz(lead):
            return "trivia"
    except Exception:
        bot.logger.exception("V88_INITIAL_TRIVIA_FAILED")
    return "none"


async def v88_start(update, context):
    user = update.effective_user
    uid = getattr(user, "id", None)
    args = list(getattr(context, "args", []) or [])
    payload = str(args[0]).lower() if args else ""

    result = await _old_start(update, context)

    if uid and payload == "freequiz":
        # Pressing Start on the clearly-labelled Free Quiz & Rewards deep link is
        # the user's explicit opt-in to Quiz & Rewards. Other categories remain off.
        v83._set_subscription(uid, "quiz_rewards", True, source="free_quiz_cta")
        try:
            await update.effective_message.reply_text(
                "🏆 <b>You’re in the BETROXY Free Sports Challenge!</b>\n\n"
                "Quiz & Rewards is now <b>ON ✅</b>. Correct answers and live predictions earn points toward the weekly leaderboard.\n\n"
                "No deposit or wager is required. You can turn Quiz & Rewards off anytime from your preferences.",
                parse_mode=bot.ParseMode.HTML,
                reply_markup=bot.InlineKeyboardMarkup([
                    [bot.InlineKeyboardButton("🔔 My Preferences", callback_data="eng_preferences")],
                    [bot.InlineKeyboardButton("🚀 Open BETROXY App", url=v83.OPEN_APP_URL)],
                ]),
            )
        except Exception:
            bot.logger.exception("V88_FREEQUIZ_CONFIRMATION_FAILED")
        mode = _send_first_quiz(uid)
        bot.logger.warning("V88_FREEQUIZ_JOIN uid=%s first_quiz=%s", uid, mode)
    return result


def v88_send_optin_invites(limit=12):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT l.* FROM intelligence_leads l
                WHERE l.reachable_bot=TRUE AND l.opt_out=FALSE
                  AND NOT EXISTS (SELECT 1 FROM engagement_subscriptions s WHERE s.telegram_user_id=l.telegram_user_id AND s.consent_at IS NOT NULL)
                  AND NOT EXISTS (SELECT 1 FROM engagement_log e WHERE e.telegram_user_id=l.telegram_user_id AND e.message_key='optin_invite')
                  AND l.last_seen_at <= NOW()-INTERVAL '30 minutes'
                ORDER BY l.last_seen_at DESC LIMIT %s
                """,
                (int(limit),),
            )
            rows = cur.fetchall()
    kb = [
        [{"text": "🏆 Join FREE Quiz & Rewards", "url": QUIZ_DEEPLINK}],
        [{"text": "🔔 Choose Other Updates", "url": v83.PREFERENCES_DEEPLINK}],
        [{"text": "🚀 Open BETROXY App", "url": v83.OPEN_APP_URL}],
    ]
    for lead in rows:
        v83._send_claimed(
            int(lead["telegram_user_id"]), "optin", "optin_invite",
            "🏆 <b>Join the BETROXY Free Sports Challenge</b>\n\n"
            "Answer sports quizzes and live predictions, collect points and compete on the weekly leaderboard.\n\n"
            "No deposit or wager is required. Tap below to join; you can stop anytime.",
            kb,
        )
        time.sleep(0.08)


def v88_business_recent_followups(limit=8):
    try:
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.telegram_business_enquiries') AS t")
                if not (cur.fetchone() or {}).get("t"):
                    return
                cur.execute(
                    """
                    SELECT e.* FROM telegram_business_enquiries e
                    WHERE e.customer_user_id IS NOT NULL
                      AND e.last_message_at BETWEEN NOW()-INTERVAL '22 hours' AND NOW()-INTERVAL '6 hours'
                      AND NOT EXISTS (
                        SELECT 1 FROM engagement_log g
                        WHERE g.telegram_user_id=e.customer_user_id
                          AND g.message_key=('business_followup:'||e.id::text)
                      )
                    ORDER BY e.last_message_at DESC LIMIT %s
                    """,
                    (int(limit),),
                )
                rows = cur.fetchall()
        kb = [
            [{"text": "🏆 Join FREE Quiz & Rewards", "url": QUIZ_DEEPLINK}],
            [{"text": "🎁 Promotions", "url": "https://t.me/BetroxyBot/promotions"}],
            [{"text": "🎧 Support", "url": v83.SUPPORT_URL}],
        ]
        for e in rows:
            uid = int(e["customer_user_id"])
            key = f"business_followup:{int(e['id'])}"
            job_id = v83._claim_job(uid, "business_followup", key)
            if not job_id:
                continue
            ok, data = v83._tg_send(
                int(e["customer_chat_id"]),
                "👋 <b>Before you go… join our FREE Sports Challenge</b>\n\n"
                "Answer quizzes and live predictions, collect points and compete on the weekly leaderboard. No deposit or wager required.",
                kb,
                business_connection_id=str(e["connection_id"]),
            )
            v83._finish_job(job_id, ok, data)
            time.sleep(0.08)
    except Exception:
        bot.logger.exception("V88_BUSINESS_FOLLOWUP_FAILED")


def v88_single_optin_reminder(limit=12):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT l.* FROM intelligence_leads l
                WHERE l.reachable_bot=TRUE
                  AND l.opt_out=FALSE
                  AND COALESCE(l.lifecycle_stage,'') NOT IN ('suppressed','unreachable','opted_out')
                  AND l.last_seen_at <= NOW()-INTERVAL '24 hours'
                  AND NOT EXISTS (
                      SELECT 1 FROM engagement_subscriptions s
                      WHERE s.telegram_user_id=l.telegram_user_id AND s.consent_at IS NOT NULL
                  )
                  AND EXISTS (
                      SELECT 1 FROM engagement_log e
                      WHERE e.telegram_user_id=l.telegram_user_id
                        AND e.message_key='optin_invite' AND e.status='sent'
                        AND e.sent_at <= NOW()-INTERVAL '3 days'
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM engagement_log e2
                      WHERE e2.telegram_user_id=l.telegram_user_id AND e2.message_key=%s
                  )
                ORDER BY l.last_seen_at DESC LIMIT %s
                """,
                (v87.REMINDER_KEY, int(limit)),
            )
            rows = cur.fetchall()
    kb = [[{"text": "🏆 Join FREE Quiz & Rewards", "url": QUIZ_DEEPLINK}]]
    sent = 0
    for lead in rows:
        if v83._send_claimed(
            int(lead["telegram_user_id"]), "optin", v87.REMINDER_KEY,
            "🏆 <b>Your Free Sports Challenge is waiting</b>\n\n"
            "Join once to receive sports quizzes and live predictions, collect points and compete on the weekly leaderboard. No deposit or wager required.\n\n"
            "This is the final invitation unless you choose to join.",
            kb,
        ):
            sent += 1
        time.sleep(0.08)
    if sent:
        bot.logger.warning("V88_OPTIN_REMINDER sent=%s", sent)
    return sent


def _send_designated_test_probe_once():
    time.sleep(28)
    try:
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT telegram_user_id FROM intelligence_leads
                    WHERE LOWER(COALESCE(telegram_username,''))=%s
                    ORDER BY last_seen_at DESC NULLS LAST LIMIT 1
                    """,
                    (TEST_USERNAME.lower(),),
                )
                row = cur.fetchone()
                if not row:
                    cur.execute(
                        """
                        SELECT telegram_user_id FROM referrals
                        WHERE LOWER(COALESCE(telegram_username,''))=%s
                        ORDER BY joined_at DESC NULLS LAST LIMIT 1
                        """,
                        (TEST_USERNAME.lower(),),
                    )
                    row = cur.fetchone()
        if not row:
            bot.logger.warning("V88_TEST_PROBE user_not_found username=@%s", TEST_USERNAME)
            return
        uid = int(row["telegram_user_id"])
        key = "v88_test_freequiz_cta"
        job_id = v83._claim_job(uid, "test", key)
        if not job_id:
            bot.logger.warning("V88_TEST_PROBE already_claimed username=@%s", TEST_USERNAME)
            return
        ok, data = v83._tg_send(
            uid,
            "🧪 <b>BETROXY V88 TEST</b>\n\nThe new Business → Free Quiz & Rewards bridge is deployed. Tap below to test the exact customer join flow.",
            [[{"text": "🏆 Join FREE Quiz & Rewards", "url": QUIZ_DEEPLINK}]],
        )
        v83._finish_job(job_id, ok, data)
        bot.logger.warning("V88_TEST_PROBE username=@%s sent=%s", TEST_USERNAME, ok)
    except Exception:
        bot.logger.exception("V88_TEST_PROBE_FAILED")


# Apply runtime patches.
bot.public_menu = v88_public_menu
v83.v53.v53_public_menu = v88_public_menu
v75._business_menu = v88_business_menu
v83.v78.business_main_menu = v88_business_menu
v75._business_reply_payload = v88_business_reply_payload
biz51._reply_payload = v88_business_reply_payload
v83._send_optin_invites = v88_send_optin_invites
v83._send_business_recent_followups = v88_business_recent_followups
v87._send_single_optin_reminder = v88_single_optin_reminder
bot.start = v88_start

bot.logger.warning(
    "V88_UNIFIED_FREE_QUIZ_BRIDGE active=on business_cta=freequiz officialbot_cta=freequiz "
    "freequiz_start_auto_optin=quiz_only first_quiz=immediate reminder_3d=one test_account=@mohit_97saxena"
)


if __name__ == "__main__":
    v63.apply_signup_cta()
    v85._enable_smart_reply_without_reset()
    threading.Thread(target=v83._worker_loop, name="betroxy-engagement-worker", daemon=True).start()
    threading.Thread(target=_send_designated_test_probe_once, name="betroxy-v88-test-probe", daemon=True).start()
    bot.logger.warning("V88 polling handover delay=12s")
    time.sleep(12)
    bot.main()
