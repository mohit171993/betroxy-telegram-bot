import os
import json
import requests

import bot
import v70_force_tg_miniapp_links as v70

# V71 - final BETROXY Business customer menu
# Adds PLAY NOW as a native Mini App deep link, complete menu and Bot API 9.6 button styles.

v68 = v70.v68
v63 = v70.v63
biz51 = v70.biz51

SUPPORT_URL = "https://t.me/betroxysports"
UPDATES_URL = "https://t.me/betroxycasino"
WEBSITE_URL = "https://betroxy.com/"

# PLAY NOW must open a Mini App via a native deep link only.
# Popular Games is the broadest current BotFather Direct Mini App route and acts as the lobby entry.
PLAY_NOW_URL = v68.POPULAR_URL


def _btn(text, url, style=None):
    """Create a styled button while remaining compatible with older PTB builds."""
    kwargs = {"url": url}
    if style:
        kwargs["api_kwargs"] = {"style": style}
    try:
        return bot.InlineKeyboardButton(text, **kwargs)
    except TypeError:
        kwargs.pop("api_kwargs", None)
        return bot.InlineKeyboardButton(text, **kwargs)


def _main_keyboard():
    return bot.InlineKeyboardMarkup([
        [
            _btn("🏏 Sportsbook", v68.SPORTSBOOK_URL, "primary"),
            _btn("🎰 Casino", v68.CASINO_URL, "danger"),
        ],
        [
            _btn("🚀 Crash Games", v68.CRASH_URL, "primary"),
            _btn("🎟 Lottery", v68.LOTTERY_URL, "primary"),
        ],
        [
            _btn("🔄 Exchange", v68.EXCHANGE_URL, "success"),
            _btn("🔥 Popular Games", v68.POPULAR_URL, "danger"),
        ],
        [_btn("▶️ PLAY NOW", PLAY_NOW_URL, "success")],
        [
            _btn("🎧 Help & Support", SUPPORT_URL, "primary"),
            _btn("📣 Updates", UPDATES_URL, "primary"),
        ],
        [_btn("🌐 SIGN UP ON WEBSITE", WEBSITE_URL, "primary")],
    ])


def _direct_keyboard(label, url):
    return bot.InlineKeyboardMarkup([
        [_btn(label, url, "primary")],
        [_btn("▶️ PLAY NOW", PLAY_NOW_URL, "success")],
        [
            _btn("🎧 Help & Support", SUPPORT_URL, "primary"),
            _btn("📣 Updates", UPDATES_URL, "primary"),
        ],
        [_btn("🌐 SIGN UP ON WEBSITE", WEBSITE_URL, "primary")],
    ])


# V68 reply payload looks up these module globals at reply time.
v68._main_keyboard = _main_keyboard
v68._direct_keyboard = _direct_keyboard
biz51._detect_intent = v68._detect_intent
biz51._reply_payload = v68._reply_payload


def _raw_button(text, url, style):
    return {"text": text, "url": url, "style": style}


def run_v71_test():
    """Send a real Telegram API smoke test so Mini App links and color styles are validated."""
    token = os.getenv("BOT_TOKEN", "").strip()
    admin_id = os.getenv("ADMIN_ID", "").strip()
    if not token or not admin_id:
        bot.logger.warning("V71_FINAL_MENU_TEST skipped missing token/admin")
        return

    keyboard = {
        "inline_keyboard": [
            [
                _raw_button("🏏 Sportsbook", v68.SPORTSBOOK_URL, "primary"),
                _raw_button("🎰 Casino", v68.CASINO_URL, "danger"),
            ],
            [
                _raw_button("🚀 Crash Games", v68.CRASH_URL, "primary"),
                _raw_button("🎟 Lottery", v68.LOTTERY_URL, "primary"),
            ],
            [
                _raw_button("🔄 Exchange", v68.EXCHANGE_URL, "success"),
                _raw_button("🔥 Popular Games", v68.POPULAR_URL, "danger"),
            ],
            [_raw_button("▶️ PLAY NOW", PLAY_NOW_URL, "success")],
            [
                _raw_button("🎧 Help & Support", SUPPORT_URL, "primary"),
                _raw_button("📣 Updates", UPDATES_URL, "primary"),
            ],
            [_raw_button("🌐 SIGN UP ON WEBSITE", WEBSITE_URL, "primary")],
        ]
    }

    # Local invariants before Telegram test.
    assert PLAY_NOW_URL.startswith("tg://resolve?domain=BetroxyBot&appname=")
    assert "startapp=" in PLAY_NOW_URL
    assert len(keyboard["inline_keyboard"]) == 6

    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={
                "chat_id": admin_id,
                "text": (
                    "✅ V71 FINAL CUSTOMER MENU TEST\n\n"
                    "PLAY NOW uses a native Mini App deep link.\n"
                    "Buttons use Telegram native primary/green/red styles.\n\n"
                    "Please tap PLAY NOW and 2-3 game buttons to verify the final customer experience."
                ),
                "reply_markup": json.dumps(keyboard),
            },
            timeout=20,
        )
        payload = r.json() if r.content else {}
        if r.ok and payload.get("ok"):
            bot.logger.warning(
                "V71_FINAL_MENU_TEST SUCCESS play_now=miniapp buttons=10 styled=on telegram_accepted=on"
            )
        else:
            bot.logger.error(
                "V71_FINAL_MENU_TEST FAILED status=%s body=%s", r.status_code, payload
            )
    except Exception as exc:
        bot.logger.exception("V71_FINAL_MENU_TEST ERROR: %s", exc)


bot.logger.warning(
    "V71_FINAL_CUSTOMER_MENU active=on play_now=miniapp complete_menu=on styled_buttons=on"
)

if __name__ == "__main__":
    v63.apply_signup_cta()
    v63.enable_business_smart_auto_reply()
    run_v71_test()
    bot.main()
