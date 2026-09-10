import asyncio
import html

import bot
import v65_admin_analytics_entrypoint as v65
import v63_clean_public_banner_bootstrap as v63

biz51 = v63.biz51

# V68 - route Telegram Business customers directly into the relevant BetroxyBot section.
CRASH_GAMES = "https://t.me/BetroxyBot/crashgames"
LOTTERY_GAMES = "https://t.me/BetroxyBot/lotterygames"
CASINO = "https://t.me/BetroxyBot/casino"
SPORTSBOOK = "https://t.me/BetroxyBot/sportsbook"
EXCHANGE = "https://t.me/BetroxyBot/exchange"
POPULAR_GAMES = "https://t.me/BetroxyBot/populargames"
WEBSITE = "https://betroxy.com/"
SUPPORT = "https://t.me/betroxysports"


def _direct_keyboard():
    return bot.InlineKeyboardMarkup([
        [
            bot.InlineKeyboardButton("🏏 Sportsbook", url=SPORTSBOOK),
            bot.InlineKeyboardButton("🎰 Casino", url=CASINO),
        ],
        [
            bot.InlineKeyboardButton("🚀 Crash Games", url=CRASH_GAMES),
            bot.InlineKeyboardButton("🎟 Lottery", url=LOTTERY_GAMES),
        ],
        [
            bot.InlineKeyboardButton("🔄 Exchange", url=EXCHANGE),
            bot.InlineKeyboardButton("🔥 Popular Games", url=POPULAR_GAMES),
        ],
        [bot.InlineKeyboardButton("🎧 Help & Support", url=SUPPORT)],
        [bot.InlineKeyboardButton("🌐 SIGN UP ON WEBSITE", url=WEBSITE)],
    ])


def _detect_intent_v68(text):
    t = " ".join(str(text or "").lower().strip().split())
    if any(x in t for x in ("aviator", "crash", "crash game", "jetx")):
        return "crashgames"
    if any(x in t for x in ("lottery", "lotto", "lottery game")):
        return "lottery"
    if any(x in t for x in ("exchange", "bet exchange")):
        return "exchange"
    if any(x in t for x in ("popular games", "popular game", "trending games")):
        return "populargames"
    return _original_detect_intent(text)


def _reply_payload_v68(intent, first_reply=False):
    if first_reply or intent in {"greeting", "general"}:
        return (
            "👋 <b>Welcome to BETROXY</b>\n\n"
            "What would you like to explore? Choose a section below, or simply type what you need.\n\n"
            "⚡ Examples: <i>cricket, casino, aviator, lottery, exchange, deposit, withdrawal</i>.",
            _direct_keyboard(),
            "engaged",
        )
    routes = {
        "sportsbook": ("🏏 <b>BETROXY Sportsbook</b>\n\nOpen Sportsbook directly below.", SPORTSBOOK, "🏏 OPEN SPORTSBOOK"),
        "casino": ("🎰 <b>BETROXY Casino</b>\n\nOpen Casino directly below.", CASINO, "🎰 OPEN CASINO"),
        "crashgames": ("🚀 <b>BETROXY Crash Games</b>\n\nOpen Crash Games directly below.", CRASH_GAMES, "🚀 OPEN CRASH GAMES"),
        "lottery": ("🎟 <b>BETROXY Lottery Games</b>\n\nOpen Lottery Games directly below.", LOTTERY_GAMES, "🎟 OPEN LOTTERY"),
        "exchange": ("🔄 <b>BETROXY Exchange</b>\n\nOpen Exchange directly below.", EXCHANGE, "🔄 OPEN EXCHANGE"),
        "populargames": ("🔥 <b>Popular Games</b>\n\nOpen BETROXY Popular Games directly below.", POPULAR_GAMES, "🔥 OPEN POPULAR GAMES"),
    }
    if intent in routes:
        text, url, label = routes[intent]
        return text, bot.InlineKeyboardMarkup([[bot.InlineKeyboardButton(label, url=url)]]), "engaged"
    return _original_reply_payload(intent, first_reply=False)


_original_detect_intent = biz51._detect_intent
_original_reply_payload = biz51._reply_payload
biz51._detect_intent = _detect_intent_v68
biz51._reply_payload = _reply_payload_v68

# Verify every configured direct destination at startup before serving customers.
_links = [CRASH_GAMES, LOTTERY_GAMES, CASINO, SPORTSBOOK, EXCHANGE, POPULAR_GAMES, WEBSITE, SUPPORT]
assert len(set(_links)) == 8
assert all(x.startswith("https://") for x in _links)
bot.logger.warning("V68_DIRECT_LINK_CUSTOMER_EXPERIENCE active=on routes=6 support=on website=on")


if __name__ == "__main__":
    v63.apply_signup_cta()
    v63.enable_business_smart_auto_reply()
    v63.run_banner_self_test_once()
    bot.main()
