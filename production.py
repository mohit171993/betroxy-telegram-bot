"""Permanent BETROXY production entrypoint."""
import html
import importlib
import threading
import time

import bot
import v113_text_quiz_ux as quiz
import v87_single_optin_reminder as reminder
import daily_quiz_schedule as daily_schedule

v110 = quiz.v110
v111 = quiz.v111
v97 = v110.v97
v96 = v110.v96
v93 = v110.v93
v101 = v110.v101
v104 = v110.v104
v88 = v110.v88
v85 = v110.v85
v83 = v110.v83

# Daily quiz prizes must remain staged until an admin explicitly approves them.
# Ignore any stale Railway environment value that might request automatic issuance.
daily_schedule.AUTO_REWARDS_ENABLED = False

# Current public BETROXY updates channel. Keep this authoritative in production
# so renamed Telegram usernames cannot leave scheduled posts pointing at an old channel.
v110.CHANNEL_CHAT = "@betroxyupdates"


def _result_rows(campaign):
    return [
        [{"text": "🏆 LIVE LEADERBOARD", "callback_data": f"v110_leaderboard:{campaign['id']}"}],
        [{"text": "🚀 EXPLORE BETROXY", "url": v110.OPEN_APP_URL}],
    ]


def _result_text(entry, rank, already=False):
    heading = "✅ <b>Today's challenge is already complete.</b>" if already else "🎉 <b>Challenge complete!</b>"
    return (
        f"{heading}\n\n"
        f"Score: <b>{int(entry.get('correct_count') or 0)}/7</b>\n"
        f"Rank: <b>#{rank}</b>\n\n"
        "Daily ranking: correct answers → hard-question accuracy → total answer time."
    )


def _leaderboard_text(campaign):
    rows = v110._leaderboard(campaign["id"], 5)
    lines = ["🏆 <b>LIVE BETROXY LEADERBOARD</b>", "", "Accuracy first; hard-question accuracy and speed break ties.", ""]
    if not rows:
        lines.append("No completed entries yet.")
    else:
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        for i, row in enumerate(rows[:5]):
            name = str(row.get("telegram_username") or f"Player {str(row.get('telegram_user_id') or '')[-4:]}")
            score = int(row.get("correct_count") or 0)
            lines.append(f"{medals[i]} <b>{html.escape(name)}</b> — {score}/7")
    return "\n".join(lines)


def _install_text_only_quiz_results():
    original_start_quiz = v110._start_quiz

    async def _finish_quiz_text(uid, campaign, entry):
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE v110_quiz_entries SET completed_at=COALESCE(completed_at,NOW()) WHERE id=%s RETURNING *",
                    (int(entry["id"]),),
                )
                final = cur.fetchone()
            conn.commit()
        rank = v110._rank(campaign["id"], final["id"])
        ok, _ = quiz._tg_send_text(uid, _result_text(final, rank), _result_rows(campaign))
        v110._set_session(uid, flow_state="complete", current_question_id=None, question_sent_at=None)
        bot.logger.warning("QUIZ_TEXT_RESULT uid=%s campaign=%s rank=%s sent=%s image=off", uid, campaign["id"], rank, ok)

    async def _start_quiz_text(uid, username, campaign, source="officialbot"):
        entry = v110._entry(campaign["id"], uid, username, source)
        if entry.get("completed_at"):
            rank = v110._rank(campaign["id"], entry["id"])
            ok, _ = quiz._tg_send_text(uid, _result_text(entry, rank, already=True), _result_rows(campaign))
            bot.logger.warning("QUIZ_TEXT_RESULT_REPLAY uid=%s campaign=%s rank=%s sent=%s image=off", uid, campaign["id"], rank, ok)
            return
        return await original_start_quiz(uid, username, campaign, source)

    v110._finish_quiz = _finish_quiz_text
    v110._start_quiz = _start_quiz_text


def _install_text_only_leaderboard():
    previous_callback = bot.callback_handler

    async def _production_callback_handler(update, context):
        q = getattr(update, "callback_query", None)
        data = str(getattr(q, "data", "") or "") if q else ""
        if q and data.startswith("v110_leaderboard"):
            parts = data.split(":", 1)
            campaign = None
            if len(parts) == 2 and parts[1].isdigit():
                campaign = v110._campaign(int(parts[1]))
            if not campaign:
                campaign = v110._today_campaign(test_mode=not v110.PUBLIC_ENABLED)
            await q.answer()
            await q.message.reply_text(
                _leaderboard_text(campaign),
                parse_mode=bot.ParseMode.HTML,
                reply_markup=bot.InlineKeyboardMarkup([
                    [bot.InlineKeyboardButton("🚀 OPEN BETROXY", url=v110.OPEN_APP_URL)]
                ]),
            )
            bot.logger.warning("QUIZ_TEXT_LEADERBOARD uid=%s campaign=%s image=off", q.from_user.id, campaign["id"])
            return
        return await previous_callback(update, context)

    bot.callback_handler = _production_callback_handler


def _feature_guard(compact_menu, quiz_alerts, business_menu=None, dm_reply_handler=None, admin_rewards_handler=None):
    menu = compact_menu.compact_public_menu(None)
    menu_buttons = [b for row in menu.inline_keyboard for b in row]
    quiz_buttons = [b for b in menu_buttons if "daily quiz" in str(getattr(b, "text", "")).lower()]
    quiz_route_ok = bool(quiz_buttons and getattr(quiz_buttons[0], "callback_data", None) == "compact_daily_quiz")
    timer_route_ok = (
        int(getattr(daily_schedule, "QUESTION_SECONDS", 0)) == 30
        and getattr(v110, "_send_question_to_user", None) is getattr(daily_schedule, "_send_question_30s", None)
    )
    reminder_patch_ok = getattr(v83, "_worker_cycle", None) is getattr(reminder, "_v87_worker_cycle", None)
    result_text_only_ok = (
        getattr(v110._finish_quiz, "__name__", "") == "_finish_quiz_text"
        and getattr(v110._start_quiz, "__name__", "") == "_start_quiz_text"
    )
    current_callback_name = getattr(bot.callback_handler, "__name__", "")
    leaderboard_text_only_ok = current_callback_name in {"_production_callback_handler", "daily_quiz_reward_callback"}
    business_menu_ok = True
    if business_menu:
        business_labels = [str(getattr(b, "text", "")) for row in business_menu(True).inline_keyboard for b in row]
        public_labels = [str(getattr(b, "text", "")) for b in menu_buttons]
        business_menu_ok = business_labels == public_labels
    dm_reply_ok = True
    if dm_reply_handler:
        dm_reply_ok = getattr(v85.v49, "_business_message_update", None) is dm_reply_handler
    admin_rewards_ok = True
    if admin_rewards_handler:
        admin_rewards_ok = bot.callback_handler is admin_rewards_handler and callable(getattr(v97, "_reward_center_keyboard", None))
    required = {
        "quiz_text": callable(getattr(v110, "_send_question_to_user", None)),
        "daily_quiz_route": quiz_route_ok,
        "quiz_30s_timer": timer_route_ok,
        "quiz_result_text_only": result_text_only_ok,
        "quiz_leaderboard_text_only": leaderboard_text_only_ok,
        "daily_quiz_rotation": bool(getattr(v110, "_daily_rotation_installed", False)),
        "daily_quiz_v2": bool(getattr(v110, "_daily_rotation_v2_installed", False)) and int(getattr(v110, "_daily_question_bank_size", 0)) == 280,
        "reminders": callable(getattr(reminder, "_send_single_optin_reminder", None)) and reminder_patch_ok,
        "rewards": callable(getattr(v97, "_issue_award", None)),
        "manual_quiz_rewards_only": getattr(daily_schedule, "AUTO_REWARDS_ENABLED", True) is False,
        "engagement": callable(getattr(v83, "_worker_loop", None)),
        "daily_schedule": callable(getattr(daily_schedule, "schedule_worker", None)),
        "quiz_alerts": callable(getattr(quiz_alerts, "alert_worker", None)),
        "compact_menu": callable(getattr(compact_menu, "compact_public_menu", None)),
        "business_menu_matches_start": business_menu_ok,
        "business_dm_reply": dm_reply_ok,
        "daily_quiz_admin_rewards": admin_rewards_ok,
    }
    missing = [name for name, ok in required.items() if not ok]
    state = " ".join(f"{name}={'ON' if ok else 'OFF'}" for name, ok in required.items())
    bot.logger.warning("PRODUCTION_FEATURE_GUARD %s legacy_image_quiz=OFF result_image=OFF leaderboard_image=OFF test_probe=OFF", state)
    if missing:
        raise RuntimeError("Required BETROXY features missing: " + ", ".join(missing))


def main():
    # Re-assert manual-only daily quiz rewards after all imports.
    daily_schedule.AUTO_REWARDS_ENABLED = False
    v110._ensure_schema()
    v111._compatibility_selftest()
    compact_menu = importlib.import_module("clean_customer_menu")
    quiz_alerts = importlib.import_module("daily_quiz_alerts")

    # Keep every historical Business DM classified, but only allow a recent,
    # reply-capable Business-DM-only user one successful automated quiz reminder
    # per rolling 7 days. OfficialBot remains the preferred route.
    weekly_business_policy = importlib.import_module("business_weekly_reminder_policy")
    weekly_business_policy.install(quiz_alerts)

    # Install the rotating V2 experience before result wrappers are captured.
    # In-progress campaigns are preserved for fairness; fresh campaigns use the
    # 280-question bank, IST weekday themes and 30-day repeat protection.
    quiz_experience = importlib.import_module("daily_quiz_experience_v2")
    quiz_experience.install(v110, quiz, daily_schedule, globals())
    quiz_completion_timeline = importlib.import_module("quiz_completion_timeline")
    quiz_completion_timeline.install(globals())
    daily_schedule._original_today_campaign = v110._ensure_campaign
    v110._today_campaign = daily_schedule._today_campaign_windowed

    # Re-assert all approved runtime behavior after legacy imports.
    reminder.v83._worker_cycle = reminder._v87_worker_cycle
    v83._worker_cycle = reminder._v87_worker_cycle
    _install_text_only_quiz_results()
    _install_text_only_leaderboard()

    # Make /start and Telegram Business first/general DM replies show the same six choices.
    unified_menu = importlib.import_module("unified_customer_menu")
    business_menu = unified_menu.install(compact_menu, v83.v75)

    # Returning Business customers must also get a reply to /start/hello; V85 used
    # to silently store those after the first auto acknowledgement.
    dm_reply_fix = importlib.import_module("business_dm_reply_fix")
    dm_reply_handler = dm_reply_fix.install()

    # Admin-only Daily Quiz Rewards screen. Opening/staging is non-issuing; the
    # admin explicitly taps Approve & Issue ₹1,000 to run the existing provider
    # issuance path with duplicate, mobile, balance and budget guards intact.
    admin_rewards = importlib.import_module("daily_quiz_admin_rewards")
    admin_rewards_handler = admin_rewards.install()

    # GiftPort GPAPGV mapping: card_no is the customer-facing redeem code while
    # redeem_code is a provider/reference number. Patch both My Rewards and future
    # delivery messages so the reference is never mislabeled as a redeem code.
    reward_display_fix = importlib.import_module("reward_code_display_fix")
    reward_display_fix.install(v97, v97.v89, v83)

    _feature_guard(compact_menu, quiz_alerts, business_menu, dm_reply_handler, admin_rewards_handler)

    # Private fixed-image approval test only. This is deliberately installed
    # after the production feature guard and does not publish to the channel.
    fixed_channel_test = importlib.import_module("fixed_channel_image_test")
    fixed_channel_test.install()

    v97._startup_diagnostic()
    v96._startup_diagnostic()
    v93._startup_pdf_diagnostic()
    v101._phonepe_probe()
    v104._startup_mobile_diagnostic()
    v88.v63.apply_signup_cta()
    v85._enable_smart_reply_without_reset()
    threading.Thread(target=v83._worker_loop, name="betroxy-engagement-worker", daemon=True).start()
    threading.Thread(target=daily_schedule.schedule_worker, name="betroxy-daily-quiz-schedule", daemon=True).start()
    threading.Thread(target=quiz_alerts.alert_worker, name="betroxy-daily-quiz-alerts", daemon=True).start()
    bot.logger.warning(
        "BETROXY_PRODUCTION_BOOT permanent_entrypoint=on text_quiz=on result_image=off result_replay_image=off leaderboard_image=off "
        "daily_quiz_route=compact_daily_quiz timer=30s countdown=20/10/5 reminders=on optin_reminder=3d "
        "quiz_alerts=10:00/16:00/19:00_IST quiz_rotation=v2 bank=280 theme_rotation=weekly no_repeat=30d mix=2easy/3medium/2hard "
        "quiz_answer_reactions=on quiz_q4_progress=on quiz_top3_result=on quiz_badges=on quiz_streaks=on quiz_completion_timeline=on "
        "customer_menu=start_and_business_same6 business_greeting_reply=on daily_quiz_admin_rewards=on "
        "reward_code_display_fix=on fixed_channel_test=private_only legacy_image_quiz=off test_probe=off public_image_worker=off "
        "daily_schedule_enabled=%s auto_rewards=%s result_channel=%s",
        daily_schedule.SCHEDULE_ENABLED, daily_schedule.AUTO_REWARDS_ENABLED, daily_schedule.RESULT_CHANNEL_ENABLED,
    )
    time.sleep(12)
    bot.main()


if __name__ == "__main__":
    main()
