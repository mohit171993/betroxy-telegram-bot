"""Permanent BETROXY production entrypoint.

Do not replace this file with versioned launchers. New features should be modules
loaded here so previously approved workers cannot silently disappear.
"""
import threading
import time

import bot
import v113_text_quiz_ux as quiz
import v87_single_optin_reminder as reminder

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

REQUIRED_FEATURES = {
    "quiz_text": callable(getattr(v110, "_send_question_to_user", None)),
    "reminders": callable(getattr(reminder, "_send_single_optin_reminder", None)),
    "rewards": callable(getattr(v97, "_issue_award", None)),
    "engagement": callable(getattr(v83, "_worker_loop", None)),
    "sports": callable(getattr(v110, "_public_worker", None)),
}


def _feature_guard():
    missing = [name for name, ok in REQUIRED_FEATURES.items() if not ok]
    state = " ".join(f"{name}={'ON' if ok else 'OFF'}" for name, ok in REQUIRED_FEATURES.items())
    bot.logger.warning("PRODUCTION_FEATURE_GUARD %s", state)
    if missing:
        raise RuntimeError("Required BETROXY features missing: " + ", ".join(missing))


def main():
    v110._ensure_schema()
    v110._selftest()
    v111._compatibility_selftest()
    _feature_guard()

    # Preserve established startup hooks/diagnostics.
    v97._startup_diagnostic()
    v96._startup_diagnostic()
    v93._startup_pdf_diagnostic()
    v101._phonepe_probe()
    v104._startup_mobile_diagnostic()
    v88.v63.apply_signup_cta()
    v85._enable_smart_reply_without_reset()

    # v87 patches v83._worker_cycle at import time; therefore this single
    # engagement worker includes the one-time opt-in reminder logic too.
    threading.Thread(target=v110._public_worker, name="betroxy-public-worker", daemon=True).start()
    threading.Thread(target=v83._worker_loop, name="betroxy-engagement-worker", daemon=True).start()

    bot.logger.warning("BETROXY_PRODUCTION_BOOT permanent_entrypoint=on versioned_launcher=off")
    time.sleep(12)
    bot.main()


if __name__ == "__main__":
    main()
