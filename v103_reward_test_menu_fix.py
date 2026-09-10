import threading
import time

import bot
import v102_phonepe_b2b_admin_test as v102

v101 = v102.v101
v100 = v102.v100
v99 = v102.v99
v98 = v102.v98
v97 = v102.v97
v96 = v102.v96
v93 = v102.v93
v89 = v102.v89
v88 = v102.v88
v85 = v102.v85
v83 = v102.v83

# V97 renders the enhanced Giftport Reward Center by patching V89's callbacks.
# V102 patched only the local V97 symbol, so the existing v89_reward_center route
# still rendered the pre-test keyboard. Patch both live references.
v97._reward_center_keyboard = v102._v102_reward_center_keyboard
v89._reward_center_keyboard = v102._v102_reward_center_keyboard
bot.callback_handler = v102.v102_callback_handler

bot.logger.warning(
    "V103_REWARD_TEST_MENU_FIX active=on reward_center_route=v89 test_button=phonepe_b2b_inr30"
)


if __name__ == "__main__":
    v97._startup_diagnostic()
    v96._startup_diagnostic()
    v93._startup_pdf_diagnostic()
    v101._phonepe_probe()
    v88.v63.apply_signup_cta()
    v85._enable_smart_reply_without_reset()
    threading.Thread(target=v83._worker_loop, name="betroxy-engagement-worker", daemon=True).start()
    bot.logger.warning("V103 polling handover delay=12s")
    time.sleep(12)
    bot.main()
