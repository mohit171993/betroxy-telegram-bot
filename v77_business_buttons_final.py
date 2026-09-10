import time

import bot
import v75_business_buttons_compatible as v75

# V77 - final production entrypoint for Telegram Business auto-reply buttons.
# Keeps V75's Business-compatible high-UX menu and verified native Mini App links.
# Adds a short polling handover delay so Railway deploy overlap does not race
# the previous getUpdates connection.

v63 = v75.v63

bot.logger.warning(
    "V77_BUSINESS_BUTTONS_FINAL active=on v75_menu=on business_url_buttons=on miniapp_deeplink=on"
)

if __name__ == "__main__":
    v63.apply_signup_cta()
    v63.enable_business_smart_auto_reply()
    bot.logger.warning("V77 polling handover delay=12s")
    time.sleep(12)
    bot.main()
