"""Permanent BETROXY production entrypoint."""
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


def _install_text_only_quiz_results():
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
        rows = [
            [{"text": "🏆 LIVE LEADERBOARD", "callback_data": f"v110_leaderboard:{campaign['id']}"}],
            [{"text": "🚀 EXPLORE BETROXY", "url": v110.OPEN_APP_URL}],
        ]
        text = (
            f"🎉 <b>Challenge complete!</b>\n\n"
            f"Score: <b>{final['correct_count']}/7</b>\n"
            f"Rank: <b>#{rank}</b>\n\n"
            "Daily ranking: correct answers → hard-question accuracy → total answer time."
        )
        ok, data = quiz._tg_send_text(uid, text, rows)
        v110._set_session(uid, flow_state="complete", current_question_id=None, question_sent_at=None)
        bot.logger.warning(
            "QUIZ_TEXT_RESULT uid=%s campaign=%s entry=%s correct=%s hard=%s ms=%s rank=%s sent=%s image=off",
            uid, campaign["id"], final["id"], final["correct_count"], final["hard_correct"], final["total_answer_ms"], rank, ok,
        )

    v110._finish_quiz = _finish_quiz_text


def _feature_guard(compact_menu, quiz_alerts):
    menu = compact_menu.compact_public_menu(None)
    menu_buttons = [b for row in menu.inline_keyboard for b in row]
    quiz_buttons = [b for b in menu_buttons if "daily quiz" in str(getattr(b, "text", "")).lower()]
    quiz_route_ok = bool(quiz_buttons and getattr(quiz_buttons[0], "callback_data", None) == "compact_daily_quiz")
    timer_route_ok = (
        int(getattr(daily_schedule, "QUESTION_SECONDS", 0)) == 30
        and getattr(v110, "_send_question_to_user", None) is getattr(daily_schedule, "_send_question_30s", None)
    )
    reminder_patch_ok = getattr(v83, "_worker_cycle", None) is getattr(reminder, "_v87_worker_cycle", None)
    result_text_only_ok = getattr(v110._finish_quiz, "__name__", "") == "_finish_quiz_text"

    required = {
        "quiz_text": callable(getattr(v110, "_send_question_to_user", None)),
        "daily_quiz_route": quiz_route_ok,
        "quiz_30s_timer": timer_route_ok,
        "quiz_result_text_only": result_text_only_ok,
        "reminders": callable(getattr(reminder, "_send_single_optin_reminder", None)) and reminder_patch_ok,
        "rewards": callable(getattr(v97, "_issue_award", None)),
        "engagement": callable(getattr(v83, "_worker_loop", None)),
        "daily_schedule": callable(getattr(daily_schedule, "schedule_worker", None)),
        "quiz_alerts": callable(getattr(quiz_alerts, "alert_worker", None)),
        "compact_menu": callable(getattr(compact_menu, "compact_public_menu", None)),
    }
    missing = [name for name, ok in required.items() if not ok]
    state = " ".join(f"{name}={'ON' if ok else 'OFF'}" for name, ok in required.items())
    bot.logger.warning("PRODUCTION_FEATURE_GUARD %s legacy_image_quiz=OFF result_image=OFF test_probe=OFF", state)
    if missing:
        raise RuntimeError("Required BETROXY features missing: " + ", ".join(missing))


def main():
    v110._ensure_schema()
    v111._compatibility_selftest()

    compact_menu = importlib.import_module("clean_customer_menu")
    quiz_alerts = importlib.import_module("daily_quiz_alerts")

    # Re-assert approved runtime patches after all optional imports.
    reminder.v83._worker_cycle = reminder._v87_worker_cycle
    v83._worker_cycle = reminder._v87_worker_cycle
    _install_text_only_quiz_results()

    _feature_guard(compact_menu, quiz_alerts)

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
        "BETROXY_PRODUCTION_BOOT permanent_entrypoint=on text_quiz=on result_image=off daily_quiz_route=compact_daily_quiz "
        "timer=30s countdown=20/10/5 reminders=on optin_reminder=3d quiz_alerts=10:00/19:00_Dubai "
        "legacy_image_quiz=off test_probe=off public_image_worker=off daily_schedule_enabled=%s "
        "auto_rewards=%s result_channel=%s",
        daily_schedule.SCHEDULE_ENABLED,
        daily_schedule.AUTO_REWARDS_ENABLED,
        daily_schedule.RESULT_CHANNEL_ENABLED,
    )
    time.sleep(12)
    bot.main()


if __name__ == "__main__":
    main()
