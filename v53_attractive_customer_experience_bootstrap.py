import bot
import v52_business_smart_reply_legacy_reset_bootstrap as v52
import v51_telegram_business_auto_conversion_bootstrap as v51


# ============================================================
# V53 - ATTRACTIVE CUSTOMER EXPERIENCE
# ============================================================
# Presentation-only layer. Existing admin, referral, campaign, checker and
# Telegram Business inbox logic remain unchanged.

_original_public_menu = bot.public_menu


def v53_public_welcome_text():
    return (
        "✨ <b>WELCOME TO BETROXY</b> ✨\n\n"
        "🎰 Casino  •  ⚽ Sportsbook  •  🔄 Exchange\n\n"
        "⚡ <b>Fast access</b>\n"
        "🎁 <b>Offers & promotions</b>\n"
        "🎧 <b>Customer support</b>\n\n"
        "Choose an option below to continue 👇"
    )


def v53_public_menu(user_id=None):
    original = _original_public_menu(user_id)
    rows = [list(r) for r in original.inline_keyboard]

    # Strong single primary CTA, followed by all existing working buttons.
    premium_rows = [
        [bot.InlineKeyboardButton("🚀 PLAY NOW", web_app=bot.WebAppInfo(url=bot.APP_URL))],
        [
            bot.InlineKeyboardButton("🔥 Popular Games", web_app=bot.WebAppInfo(url=bot.APP_URL)),
            bot.InlineKeyboardButton("🎁 Offers", web_app=bot.WebAppInfo(url=bot.APP_URL)),
        ],
    ]
    return bot.InlineKeyboardMarkup(premium_rows + rows)


bot.public_welcome_text = v53_public_welcome_text
bot.public_menu = v53_public_menu


# ============================================================
# TELEGRAM BUSINESS SMART REPLIES - POLISHED UX
# ============================================================

def _premium_common_buttons():
    return bot.InlineKeyboardMarkup([
        [bot.InlineKeyboardButton("🚀 PLAY NOW", url=v51.BETROXY_PRODUCT_BOT)],
        [
            bot.InlineKeyboardButton("🎰 Casino", url=v51.BETROXY_CASINO),
            bot.InlineKeyboardButton("🏏 Sportsbook", url=v51.BETROXY_SPORTSBOOK),
        ],
        [
            bot.InlineKeyboardButton("🌐 Website", url=v51.BETROXY_WEBSITE),
            bot.InlineKeyboardButton("🎧 Support", url=bot.TELEGRAM_SUPPORT_URL),
        ],
    ])


def v53_reply_payload(intent, first_reply=False):
    kb = _premium_common_buttons()

    if first_reply or intent in {"greeting", "general"}:
        return (
            "✨ <b>Welcome to BETROXY</b> ✨\n\n"
            "Thanks for contacting us. Choose an option below for quick access.\n\n"
            "Need help? Just type one of these:\n"
            "💳 Deposit   •   💸 Withdrawal\n"
            "🎁 Bonus     •   🔐 Login\n"
            "🏏 Sportsbook • 🎰 Casino\n\n"
            "Our support team can also continue with you here.",
            kb,
            "engaged",
        )

    messages = {
        "register": (
            "✅ <b>NEW ACCOUNT / REGISTRATION</b>\n\n"
            "Tap <b>PLAY NOW</b> to open official BETROXY access. If you face any registration issue, send a screenshot or describe the problem here.",
            "registration_intent",
        ),
        "deposit": (
            "💳 <b>DEPOSIT SUPPORT</b>\n\n"
            "Open your BETROXY account and select Deposit. If a payment is pending, send the amount, time and payment reference here so support can review it.",
            "account_support",
        ),
        "withdrawal": (
            "💸 <b>WITHDRAWAL SUPPORT</b>\n\n"
            "Open your account and check the withdrawal status. If it is delayed, send the requested amount, request time and relevant reference here.",
            "account_support",
        ),
        "bonus": (
            "🎁 <b>OFFERS & PROMOTIONS</b>\n\n"
            "Tap below to view official BETROXY access and current offers available for your account.",
            "engaged",
        ),
        "account": (
            "🔐 <b>LOGIN / ACCOUNT HELP</b>\n\n"
            "Try opening your account through the official bot or website below. If the issue continues, send the error message or screenshot here.",
            "account_support",
        ),
        "sportsbook": (
            "🏏 <b>BETROXY SPORTSBOOK</b>\n\n"
            "Tap Sportsbook below for quick access. If you need account help, continue messaging us here.",
            "product_interest",
        ),
        "casino": (
            "🎰 <b>BETROXY CASINO</b>\n\n"
            "Tap Casino below for quick access. If you need account help, continue messaging us here.",
            "product_interest",
        ),
        "support": (
            "🎧 <b>SUPPORT REQUEST RECEIVED</b>\n\n"
            "Please describe the issue and include any useful screenshot/reference. Your message is visible to the support team.",
            "support_needed",
        ),
    }

    text, stage = messages.get(intent, messages["support"])
    return text, kb, stage


v51._reply_payload = v53_reply_payload

bot.logger.warning("V53_ATTRACTIVE_CUSTOMER_EXPERIENCE active=on")


if __name__ == "__main__":
    bot.main()
