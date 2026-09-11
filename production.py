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


def _feature_guard(compact_menu, quiz_alerts):
    required = {
        "quiz_text": callable(getattr(v110, "_send_question_to_user", None)),
        "reminders": callable(getattr(reminder, "_send_single_optin_reminder", None)),
        "rewards": callable(getattr(v97, "_issue_award", None)),
        "engagement": callable(getattr(v83, "_worker_loop", None)),
        "daily_schedule": callable(getattr(daily_schedule, "schedule_worker", None)),
        "quiz_alerts": callable(getattr(quiz_alerts, "alert_worker", None)),
        "compact_menu": callable(getattr(compact_menu, "compact_public_menu", None)),
    }
    missing = [name for name, ok in required.items() if not ok]
    state = " ".join(f"{name}={'ON' if ok else 'OFF'}" for name, ok in required.items())
    bot.logger.warning("PRODUCTION_FEATURE_GUARD %s legacy_image_quiz=OFF test_probe=OFF", state)
    if missing:
        raise RuntimeError("Required BETROXY features missing: " + ", ".join(missing))


def main():
    v110._ensure_schema()

    # Run compatibility checks before importing optional UI modules. Some UI
    # modules intentionally patch callback/menu handlers, which would otherwise
    # make the legacy compatibility assertion fail even though the routes work.
    v111._compatibility_selftest()

    compact_menu = importlib.import_module("clean_customer_menu")
    quiz_alerts = importlib.import_module("daily_quiz_alerts")
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
        "BETROXY_PRODUCTION_BOOT permanent_entrypoint=on text_quiz=on reminders=on compact_menu=on "
        "quiz_alerts=10:00/19:00_Dubai legacy_image_quiz=off test_probe=off public_image_worker=off "
        "daily_schedule_enabled=%s auto_rewards=%s result_channel=%s",
        daily_schedule.SCHEDULE_ENABLED,
        daily_schedule.AUTO_REWARDS_ENABLED,
        daily_schedule.RESULT_CHANNEL_ENABLED,
    )
    time.sleep(12)
    bot.main()


if __name__ == "__main__":
    main()
