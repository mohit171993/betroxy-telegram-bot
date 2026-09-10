import os
import requests

import bot
import v68_direct_links_customer_experience as v68

# V69 - force Telegram Direct Mini App links.
# Telegram's documented direct Mini App syntax is:
# https://t.me/<bot_username>/<short_name>?startapp=<start_parameter>
# The previous V68 links omitted startapp, which can fall back to simply opening the bot.

v63 = v68.v63
biz51 = v68.biz51

v68.CRASH_URL = "https://t.me/BetroxyBot/crashgames?startapp=crashgames"
v68.LOTTERY_URL = "https://t.me/BetroxyBot/lotterygames?startapp=lotterygames"
v68.CASINO_URL = "https://t.me/BetroxyBot/casino?startapp=casino"
v68.SPORTSBOOK_URL = "https://t.me/BetroxyBot/sportsbook?startapp=sportsbook"
v68.EXCHANGE_URL = "https://t.me/BetroxyBot/exchange?startapp=exchange"
v68.POPULAR_URL = "https://t.me/BetroxyBot/populargames?startapp=populargames"

# Re-apply the V68 reply engine after replacing its module globals.
biz51._detect_intent = v68._detect_intent
biz51._reply_payload = v68._reply_payload

_REQUIRED = {
    "crashgames": v68.CRASH_URL,
    "lotterygames": v68.LOTTERY_URL,
    "casino": v68.CASINO_URL,
    "sportsbook": v68.SPORTSBOOK_URL,
    "exchange": v68.EXCHANGE_URL,
    "populargames": v68.POPULAR_URL,
}
for short_name, url in _REQUIRED.items():
    assert f"/BetroxyBot/{short_name}" in url
    assert f"?startapp={short_name}" in url


def run_direct_link_test():
    """Send the admin a real Telegram inline-keyboard smoke test."""
    token = os.getenv("BOT_TOKEN", "").strip()
    admin_id = os.getenv("ADMIN_ID", "").strip()
    if not token or not admin_id:
        bot.logger.warning("V69_DIRECT_LINK_TEST skipped missing token/admin")
        return

    keyboard = {
        "inline_keyboard": [
            [
                {"text": "🏏 Sportsbook", "url": v68.SPORTSBOOK_URL},
                {"text": "🎰 Casino", "url": v68.CASINO_URL},
            ],
            [
                {"text": "🚀 Crash Games", "url": v68.CRASH_URL},
                {"text": "🎟 Lottery", "url": v68.LOTTERY_URL},
            ],
            [
                {"text": "🔄 Exchange", "url": v68.EXCHANGE_URL},
                {"text": "🔥 Popular Games", "url": v68.POPULAR_URL},
            ],
        ]
    }
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={
                "chat_id": admin_id,
                "text": "✅ V69 DIRECT MINI APP LINK TEST\nTap each button below. It should open that Mini App section, not the bot home.",
                "reply_markup": __import__("json").dumps(keyboard),
            },
            timeout=20,
        )
        p = r.json() if r.content else {}
        if r.ok and p.get("ok"):
            bot.logger.warning("V69_DIRECT_LINK_TEST SUCCESS telegram_buttons_accepted=6")
        else:
            bot.logger.error("V69_DIRECT_LINK_TEST FAILED status=%s body=%s", r.status_code, p)
    except Exception as exc:
        bot.logger.exception("V69_DIRECT_LINK_TEST ERROR: %s", exc)


bot.logger.warning("V69_FORCE_MINIAPP_DIRECT_LINKS active=on startapp=on routes=6")

if __name__ == "__main__":
    v63.apply_signup_cta()
    v63.enable_business_smart_auto_reply()
    run_direct_link_test()
    bot.main()
