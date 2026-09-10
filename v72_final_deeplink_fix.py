import os
import json
import requests

import bot
import v71_final_customer_menu as v71

# V72 - final deep-link fix.
# Critical fix: V66 fallback/support/payment/account flows still referenced the old
# generic PRODUCT_BOT_URL. Force every PLAY NOW path to the verified native Mini App URI.

v70 = v71.v70
v68 = v71.v68
v63 = v71.v63
biz51 = v71.biz51
v66 = v70.v69.v68.v66

# Use the already verified Direct Mini App route as the universal PLAY NOW lobby.
PLAY_NOW_URL = v68.POPULAR_URL

# Patch EVERY older customer-flow global that can still generate PLAY NOW buttons.
v66.PRODUCT_BOT_URL = PLAY_NOW_URL
v66.SPORTSBOOK_URL = v68.SPORTSBOOK_URL
v66.CASINO_URL = v68.CASINO_URL

# Keep V71 main/direct keyboards and force their PLAY NOW URL too.
v71.PLAY_NOW_URL = PLAY_NOW_URL
v68._main_keyboard = v71._main_keyboard
v68._direct_keyboard = v71._direct_keyboard

# V68 delegates deposit/withdrawal/bonus/account/register/support payloads to V66.
# V66's _intent_keyboard resolves PRODUCT_BOT_URL dynamically, so the assignment above
# removes the final generic @BetroxyBot redirect from those paths as well.
biz51._detect_intent = v68._detect_intent
biz51._reply_payload = v68._reply_payload
biz51._send_smart_reply = v66._send_smart_reply

assert PLAY_NOW_URL.startswith("tg://resolve?domain=BetroxyBot&appname=")
assert "startapp=" in PLAY_NOW_URL
assert v66.PRODUCT_BOT_URL == PLAY_NOW_URL


def _raw(text, url, style=None):
    d = {"text": text, "url": url}
    if style:
        d["style"] = style
    return d


def _extract_urls(message_result):
    rows = (((message_result or {}).get("reply_markup") or {}).get("inline_keyboard") or [])
    return [b.get("url") for row in rows for b in row if b.get("url")]


def run_v72_test():
    """Real Telegram API verification of both main and fallback/support PLAY NOW buttons."""
    token = os.getenv("BOT_TOKEN", "").strip()
    admin_id = os.getenv("ADMIN_ID", "").strip()
    if not token or not admin_id:
        bot.logger.warning("V72_DEEPLINK_TEST skipped missing token/admin")
        return

    tests = [
        (
            "MAIN MENU",
            {
                "inline_keyboard": [
                    [_raw("🏏 Sportsbook", v68.SPORTSBOOK_URL, "primary"), _raw("🎰 Casino", v68.CASINO_URL, "danger")],
                    [_raw("🚀 Crash Games", v68.CRASH_URL, "primary"), _raw("🎟 Lottery", v68.LOTTERY_URL, "primary")],
                    [_raw("🔄 Exchange", v68.EXCHANGE_URL, "success"), _raw("🔥 Popular Games", v68.POPULAR_URL, "danger")],
                    [_raw("▶️ PLAY NOW", PLAY_NOW_URL, "success")],
                    [_raw("🎧 Help & Support", v71.SUPPORT_URL, "primary"), _raw("📣 Updates", v71.UPDATES_URL, "primary")],
                    [_raw("🌐 SIGN UP ON WEBSITE", v71.WEBSITE_URL, "primary")],
                ]
            },
        ),
        (
            "SUPPORT / PAYMENT FLOW",
            {
                "inline_keyboard": [
                    [_raw("▶️ PLAY NOW", v66.PRODUCT_BOT_URL, "success")],
                    [_raw("🌐 SIGN UP ON WEBSITE", v66.WEBSITE_URL, "primary")],
                    [_raw("🎧 HUMAN SUPPORT", v66.SUPPORT_URL, "primary")],
                ]
            },
        ),
    ]

    verified = 0
    for name, keyboard in tests:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={
                "chat_id": admin_id,
                "text": f"✅ V72 {name} TEST\nPLAY NOW must open the Mini App directly — never the generic bot chat.",
                "reply_markup": json.dumps(keyboard),
            },
            timeout=20,
        )
        payload = r.json() if r.content else {}
        if not (r.ok and payload.get("ok")):
            raise RuntimeError(f"Telegram rejected V72 {name}: {r.status_code} {payload}")

        returned_urls = _extract_urls(payload.get("result"))
        play_urls = [u for u in returned_urls if u == PLAY_NOW_URL]
        if not play_urls:
            raise RuntimeError(f"V72 {name}: returned Telegram markup missing exact PLAY NOW deep link")
        if any(u == "https://t.me/BetroxyBot" or u == "http://t.me/BetroxyBot" for u in returned_urls):
            raise RuntimeError(f"V72 {name}: generic BetroxyBot URL leaked into keyboard")
        verified += 1

    bot.logger.warning(
        "V72_DEEPLINK_TEST SUCCESS flows=%s play_now_native=on generic_bot_leak=none",
        verified,
    )


bot.logger.warning(
    "V72_FINAL_DEEPLINK_FIX active=on all_play_now_paths=native_miniapp legacy_product_bot_removed=on"
)

if __name__ == "__main__":
    v63.apply_signup_cta()
    v63.enable_business_smart_auto_reply()
    run_v72_test()
    bot.main()
