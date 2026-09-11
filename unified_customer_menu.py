"""Keep /start and Telegram Business first-reply menus aligned."""
import bot

MINIAPP_DEEPLINK = "https://t.me/BetroxyBot/sportsbook?startapp=sportsbook"
OFFICIAL_BOT = "BetroxyOfficialBot"


def _bot_deeplink(payload):
    return f"https://t.me/{OFFICIAL_BOT}?start={payload}"


def install(compact_menu, business_module):
    """Use the six-action customer navigation in both customer entry paths.

    Telegram Business replies use URL/deep-link equivalents, while every action
    resolves to the same working destination as the direct-bot customer menu.
    """
    v75 = business_module
    biz51 = v75.biz51
    previous_payload = v75._business_reply_payload

    def business_customer_menu(styled=True):
        return bot.InlineKeyboardMarkup([
            [bot.InlineKeyboardButton("🚀 Open BETROXY", url=MINIAPP_DEEPLINK)],
            [bot.InlineKeyboardButton("🏆 Daily Quiz", url=_bot_deeplink("dailyquiz"))],
            [bot.InlineKeyboardButton("🎁 My Rewards", url=_bot_deeplink("rewards"))],
            [bot.InlineKeyboardButton("👤 My Account", url=_bot_deeplink("account"))],
            [bot.InlineKeyboardButton("📢 Updates & Promotions", url=_bot_deeplink("updates"))],
            [bot.InlineKeyboardButton("🎧 Help & Support", url=_bot_deeplink("support"))],
        ])

    def unified_business_payload(intent, first_reply=False):
        text, keyboard, stage = previous_payload(intent, first_reply=first_reply)
        if first_reply or intent in {"greeting", "general"}:
            keyboard = business_customer_menu(styled=True)
        return text, keyboard, stage

    # Patch the functions resolved by the live Business sender.
    v75._business_menu = business_customer_menu
    v75._business_reply_payload = unified_business_payload
    biz51._reply_payload = unified_business_payload

    bot.logger.warning(
        "UNIFIED_CUSTOMER_MENU active=on start=compact6 business_dm=compact6 "
        "business_open=telegram_miniapp business_account=bot_deeplink "
        "business_updates=bot_deeplink business_support=bot_deeplink"
    )
    return business_customer_menu
