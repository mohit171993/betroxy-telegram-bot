import threading
import time

import bot
import v114_amazonpay_b2b_50_live_test as v114
import v87_single_optin_reminder as v87

v113 = v114.v113
v110 = v114.v110
v111 = v114.v111
v97 = v114.v97
v96 = v114.v96
v93 = v114.v93
v101 = v114.v101
v104 = v114.v104
v88 = v114.v88
v85 = v114.v85
v83 = v114.v83

TEST_USERNAME = "mohit_97saxena"
TEST_KEY = "optin_reminder_test_v115"


def _send_controlled_reminder_test():
    time.sleep(18)
    try:
        lead = v110._find_test_user()
        if not lead or not lead.get("reachable_bot"):
            bot.logger.warning("V115_REMINDER_TEST result=blocked reason=test_user_unavailable")
            return
        uid = int(lead["telegram_user_id"])
        keyboard = [
            [{"text": "🏆 Join Sports Challenge", "url": v83.PREFERENCES_DEEPLINK}],
            [{"text": "🔔 Choose My Updates", "url": v83.PREFERENCES_DEEPLINK}],
        ]
        text = (
            "🏆 <b>Want to join the BETROXY Sports Challenge?</b>\n\n"
            "Choose only the updates you want — Sports, Promotions or Quiz & Rewards. "
            "Quiz participants can earn points and appear on the weekly leaderboard.\n\n"
            "No selection means no recurring updates, and you can stop anytime."
        )
        sent = bool(v83._send_claimed(uid, "optin", TEST_KEY, text, keyboard))
        bot.logger.warning("V115_REMINDER_TEST sent=%s uid=%s", sent, uid)
    except Exception:
        bot.logger.exception("V115_REMINDER_TEST_FAILED")


bot.logger.warning(
    "V115_REMINDER_RESTORE active=on v87_worker=loaded delay=3d max_reminders=1 controlled_test=@mohit_97saxena"
)


if __name__ == "__main__":
    v110._ensure_schema()
    v110._selftest()
    v111._compatibility_selftest()
    v97._startup_diagnostic()
    v96._startup_diagnostic()
    v93._startup_pdf_diagnostic()
    v101._phonepe_probe()
    v104._startup_mobile_diagnostic()
    v88.v63.apply_signup_cta()
    v85._enable_smart_reply_without_reset()
    threading.Thread(target=v110._public_worker, name="betroxy-v110-public-worker", daemon=True).start()
    threading.Thread(target=v83._worker_loop, name="betroxy-engagement-worker", daemon=True).start()
    threading.Thread(target=_send_controlled_reminder_test, name="betroxy-v115-reminder-test", daemon=True).start()
    bot.logger.warning("V115 polling handover delay=12s")
    time.sleep(12)
    bot.main()
