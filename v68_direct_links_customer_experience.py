import bot
import v66_smart_customer_experience as v66

# V68 - BETROXY direct-link customer experience
# Adds exact BotFather direct links and smarter intent routing.

v63 = v66.v63
biz51 = v66.biz51

CRASH_URL = "https://t.me/BetroxyBot/crashgames"
LOTTERY_URL = "https://t.me/BetroxyBot/lotterygames"
CASINO_URL = "https://t.me/BetroxyBot/casino"
SPORTSBOOK_URL = "https://t.me/BetroxyBot/sportsbook"
EXCHANGE_URL = "https://t.me/BetroxyBot/exchange"
POPULAR_URL = "https://t.me/BetroxyBot/populargames"
SUPPORT_URL = "https://t.me/betroxysports"
WEBSITE_URL = "https://betroxy.com/"

_original_detect_intent = biz51._detect_intent


def _detect_intent(text):
    t = " ".join(str(text or "").lower().strip().split())
    if not t:
        return "general"

    if any(x in t for x in ("aviator", "crash game", "crash", "jetx", "spaceman")):
        return "crashgames"
    if any(x in t for x in ("lottery", "lotto", "jackpot", "lottery game")):
        return "lottery"
    if any(x in t for x in ("exchange", "bet exchange", "back lay", "back/lay", "lay bet")):
        return "exchange"
    if any(x in t for x in ("popular game", "popular games", "trending game", "top games")):
        return "popular"

    return _original_detect_intent(text)


def _main_keyboard():
    return bot.InlineKeyboardMarkup([
        [
            bot.InlineKeyboardButton("🟡 Sportsbook", url=SPORTSBOOK_URL),
            bot.InlineKeyboardButton("🔴 Casino", url=CASINO_URL),
        ],
        [
            bot.InlineKeyboardButton("🚀 Crash Games", url=CRASH_URL),
            bot.InlineKeyboardButton("🎟 Lottery", url=LOTTERY_URL),
        ],
        [
            bot.InlineKeyboardButton("🔄 Exchange", url=EXCHANGE_URL),
            bot.InlineKeyboardButton("🔥 Popular Games", url=POPULAR_URL),
        ],
        [bot.InlineKeyboardButton("🟢 Help & Support", url=SUPPORT_URL)],
        [bot.InlineKeyboardButton("🌐 SIGN UP ON WEBSITE", url=WEBSITE_URL)],
    ])


def _direct_keyboard(label, url):
    return bot.InlineKeyboardMarkup([
        [bot.InlineKeyboardButton(label, url=url)],
        [bot.InlineKeyboardButton("🟢 Help & Support", url=SUPPORT_URL)],
        [bot.InlineKeyboardButton("🌐 SIGN UP ON WEBSITE", url=WEBSITE_URL)],
    ])


def _reply_payload(intent, first_reply=False):
    if first_reply or intent in {"greeting", "general"}:
        return (
            "👋 <b>Welcome to BETROXY</b>\n\n"
            "What would you like to explore?\n"
            "Choose a section below, or simply type what you need.\n\n"
            "Examples: <i>aviator, cricket, casino, lottery, exchange, withdrawal pending</i>.",
            _main_keyboard(),
            "engaged",
        )

    if intent == "crashgames":
        return (
            "🚀 <b>BETROXY Crash Games</b>\n\n"
            "Open Crash Games directly below — ideal for Aviator and other crash-style games.",
            _direct_keyboard("🚀 OPEN CRASH GAMES", CRASH_URL),
            "product_interest",
        )

    if intent == "lottery":
        return (
            "🎟 <b>BETROXY Lottery Games</b>\n\n"
            "Open Lottery Games directly below.",
            _direct_keyboard("🎟 OPEN LOTTERY GAMES", LOTTERY_URL),
            "product_interest",
        )

    if intent == "exchange":
        return (
            "🔄 <b>BETROXY Exchange</b>\n\n"
            "Open the Exchange directly below.",
            _direct_keyboard("🔄 OPEN EXCHANGE", EXCHANGE_URL),
            "product_interest",
        )

    if intent == "popular":
        return (
            "🔥 <b>Popular Games</b>\n\n"
            "Open BETROXY's popular games directly below.",
            _direct_keyboard("🔥 OPEN POPULAR GAMES", POPULAR_URL),
            "product_interest",
        )

    if intent == "sportsbook":
        return (
            "🟡 <b>BETROXY Sportsbook</b>\n\n"
            "Open Sportsbook directly below for cricket, football and other sports.",
            _direct_keyboard("🟡 OPEN SPORTSBOOK", SPORTSBOOK_URL),
            "product_interest",
        )

    if intent == "casino":
        return (
            "🔴 <b>BETROXY Casino</b>\n\n"
            "Open Casino directly below.",
            _direct_keyboard("🔴 OPEN CASINO", CASINO_URL),
            "product_interest",
        )

    # Reuse the existing V66 support/account/deposit/withdrawal flows.
    return v66._reply_payload(intent, first_reply=False)


biz51._detect_intent = _detect_intent
biz51._reply_payload = _reply_payload
# V66's sender reads biz51._reply_payload dynamically, so it will use these routes.

# Small startup validation so bad direct links are caught before release.
_REQUIRED = {
    "crash": CRASH_URL,
    "lottery": LOTTERY_URL,
    "casino": CASINO_URL,
    "sportsbook": SPORTSBOOK_URL,
    "exchange": EXCHANGE_URL,
    "popular": POPULAR_URL,
}
assert all(url.startswith("https://t.me/BetroxyBot/") for url in _REQUIRED.values())
assert len(set(_REQUIRED.values())) == len(_REQUIRED)

bot.logger.warning(
    "V68_DIRECT_LINKS_CUSTOMER_EXPERIENCE active=on direct_routes=6 smart_intents=on"
)


if __name__ == "__main__":
    v63.apply_signup_cta()
    v63.enable_business_smart_auto_reply()
    v63.run_banner_self_test_once()
    bot.main()
