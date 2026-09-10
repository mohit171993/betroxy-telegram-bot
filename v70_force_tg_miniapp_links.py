import os
import json
import requests

import bot
import v69_force_miniapp_direct_links as v69

# V70 - use Telegram's native tg:// Direct Mini App URI.
# This avoids t.me URL handling that may fall back to simply opening @BetroxyBot.

v68 = v69.v68
v63 = v69.v63
biz51 = v69.biz51

v68.CRASH_URL = "tg://resolve?domain=BetroxyBot&appname=crashgames&startapp=crashgames"
v68.LOTTERY_URL = "tg://resolve?domain=BetroxyBot&appname=lotterygames&startapp=lotterygames"
v68.CASINO_URL = "tg://resolve?domain=BetroxyBot&appname=casino&startapp=casino"
v68.SPORTSBOOK_URL = "tg://resolve?domain=BetroxyBot&appname=sportsbook&startapp=sportsbook"
v68.EXCHANGE_URL = "tg://resolve?domain=BetroxyBot&appname=exchange&startapp=exchange"
v68.POPULAR_URL = "tg://resolve?domain=BetroxyBot&appname=populargames&startapp=populargames"

# Ensure the customer engine resolves the updated module globals at click/reply time.
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
    assert url.startswith("tg://resolve?domain=BetroxyBot&appname=")
    assert f"appname={short_name}" in url
    assert f"startapp={short_name}" in url


def run_v70_test():
    token = os.getenv("BOT_TOKEN", "").strip()
    admin_id = os.getenv("ADMIN_ID", "").strip()
    if not token or not admin_id:
        bot.logger.warning("V70_DIRECT_MINIAPP_TEST skipped missing token/admin")
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
                "text": "✅ V70 NATIVE MINI APP TEST\nThese use tg:// appname deep links. Tap them to verify they open the selected Mini App directly.",
                "reply_markup": json.dumps(keyboard),
            },
            timeout=20,
        )
        p = r.json() if r.content else {}
        if r.ok and p.get("ok"):
            bot.logger.warning("V70_DIRECT_MINIAPP_TEST SUCCESS telegram_accepted_native_links=6")
        else:
            bot.logger.error("V70_DIRECT_MINIAPP_TEST FAILED status=%s body=%s", r.status_code, p)
    except Exception as exc:
        bot.logger.exception("V70_DIRECT_MINIAPP_TEST ERROR: %s", exc)


bot.logger.warning("V70_FORCE_TG_MINIAPP_LINKS active=on appname_routes=6")

if __name__ == "__main__":
    v63.apply_signup_cta()
    v63.enable_business_smart_auto_reply()
    run_v70_test()
    bot.main()
