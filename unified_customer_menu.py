"""Keep /start and Telegram Business first-reply menus aligned."""
import bot


def install(compact_menu, business_module):
    """Use the six-action customer navigation in both customer entry paths.

    Telegram Business messages cannot reliably use callback/WebApp buttons, so
    the Business version uses URL/deep-link equivalents while keeping the same
    six visible customer choices.
    """
    v75 = business_module
    biz51 = v75.biz51
    previous_payload = v75._business_reply_payload

    quiz_url = "https://t.me/BetroxyOfficialBot?start=freequiz"
    rewards_url = "https://t.me/BetroxyOfficialBot?start=rewards"
    account_url = "https://betroxy.com/"
    updates_url = "https://t.me/betroxycasino"
    support_url = "https://t.me/betroxysports"

    def business_customer_menu(styled=True):
        # Same six visible actions as /start; URL-only for Telegram Business.
        return bot.InlineKeyboardMarkup([
            [bot.InlineKeyboardButton("🚀 Open BETROXY", url=bot.APP_URL)],
            [bot.InlineKeyboardButton("🏆 Daily Quiz", url=quiz_url)],
            [bot.InlineKeyboardButton("🎁 My Rewards", url=rewards_url)],
            [bot.InlineKeyboardButton("👤 My Account", url=account_url)],
            [bot.InlineKeyboardButton("📢 Updates & Promotions", url=updates_url)],
            [bot.InlineKeyboardButton("🎧 Help & Support", url=support_url)],
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
        "business_buttons=url_compatible smart_intent_replies=preserved"
    )
    return business_customer_menu
