import time

import bot
import v79_how_it_works_popup as v79

v78 = v79.v78
v75 = v78.v75
v63 = v78.v63
v59 = v75.v59
biz51 = v75.biz51

# Direct Mini App route supplied for Promotions:
# https://t.me/BetroxyBot/promotions
# Use Telegram's native Mini App deep-link form, consistent with the existing
# Crash/Casino/Sportsbook/Lottery/Exchange/Popular routes.
PROMOTIONS_URL = "tg://resolve?domain=BetroxyBot&appname=promotions&startapp=promotions"
assert PROMOTIONS_URL.startswith("tg://resolve?domain=BetroxyBot&appname=promotions")


def _business_menu_with_promotions(styled=True):
    b = v78._btn
    style = (lambda s: s if styled else None)
    return bot.InlineKeyboardMarkup([
        [b("🚀 Open BETROXY App", url=v78.MINIAPP_URL, style=style("success"))],
        [
            b("👤 Account & Services", url=v78.MINIAPP_URL, style=style("primary")),
            b("📜 Transactions", url=v78.MINIAPP_URL, style=style("primary")),
        ],
        [b("🎁 Promotions", url=PROMOTIONS_URL, style=style("success"))],
        [
            b("🎧 Help & Support", callback_data="bizux_support_home", style=style("primary")),
            b("📢 Updates", url=v78.UPDATES_URL, style=style("success")),
        ],
        [
            b("👥 Refer a Friend", url=v78.REFER_WHATSAPP_URL, style=style("success")),
            b("ℹ️ How It Works", callback_data="bizux_how_it_works", style=style("primary")),
        ],
    ])


# Keep the higher-version OfficialBot public menu intact and insert Promotions
# immediately after Account & Services / Transactions.
_original_public_menu = v59.v59_public_menu


def _official_public_menu_with_promotions(user_id=None):
    original = _original_public_menu(user_id)
    rows = list(original.inline_keyboard)
    promo_button = v59._btn("🎁 Promotions", url=PROMOTIONS_URL, style="success")

    # Public menu normally has privileged row (admin/affiliate) optionally,
    # followed by Open App and Account/Transactions. Insert after that account row.
    insert_at = 3 if user_id == bot.ADMIN_ID or (user_id and bot.find_agent_by_telegram_user_id(user_id)) else 2
    insert_at = min(insert_at, len(rows))
    rows.insert(insert_at, [promo_button])
    return bot.InlineKeyboardMarkup(rows)


# Patch Business menu everywhere it is resolved dynamically.
v78.business_main_menu = _business_menu_with_promotions
v75._business_menu = _business_menu_with_promotions

# Patch OfficialBot public menu everywhere it is resolved dynamically.
v59.v59_public_menu = _official_public_menu_with_promotions
bot.public_menu = _official_public_menu_with_promotions

bot.logger.warning(
    "V80_PROMOTIONS_DEEPLINK_BUTTON active=on business=on officialbot=on "
    "appname=promotions startapp=promotions"
)

if __name__ == "__main__":
    v63.apply_signup_cta()
    v63.enable_business_smart_auto_reply()
    bot.logger.warning("V80 polling handover delay=12s")
    time.sleep(12)
    bot.main()
