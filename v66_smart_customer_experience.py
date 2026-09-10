import asyncio
import html

import bot
import v65_admin_analytics_entrypoint as v65

# V66 - Smart Telegram Business customer experience.
# Preserves V63 business logic + V65 admin analytics visibility.

v63 = v65.v63
biz51 = v63.biz51

SUPPORT_URL = "https://t.me/betroxysports"
PRODUCT_BOT_URL = biz51.BETROXY_PRODUCT_BOT
WEBSITE_URL = biz51.BETROXY_WEBSITE
SPORTSBOOK_URL = biz51.BETROXY_SPORTSBOOK
CASINO_URL = biz51.BETROXY_CASINO


def _main_keyboard():
    return bot.InlineKeyboardMarkup([
        [
            bot.InlineKeyboardButton("🔵 Deposit", url=PRODUCT_BOT_URL),
            bot.InlineKeyboardButton("🟠 Withdrawal", url=PRODUCT_BOT_URL),
        ],
        [
            bot.InlineKeyboardButton("🟣 Bonus", url=PRODUCT_BOT_URL),
            bot.InlineKeyboardButton("🟢 Support", url=SUPPORT_URL),
        ],
        [
            bot.InlineKeyboardButton("🟡 Sports", url=SPORTSBOOK_URL),
            bot.InlineKeyboardButton("🔴 Casino", url=CASINO_URL),
        ],
        [bot.InlineKeyboardButton("🟢 PLAY NOW", url=PRODUCT_BOT_URL)],
        [bot.InlineKeyboardButton("🌐 SIGN UP ON WEBSITE", url=WEBSITE_URL)],
    ])


def _intent_keyboard(intent):
    if intent == "sportsbook":
        return bot.InlineKeyboardMarkup([
            [bot.InlineKeyboardButton("🟡 OPEN SPORTSBOOK", url=SPORTSBOOK_URL)],
            [bot.InlineKeyboardButton("🟢 PLAY NOW", url=PRODUCT_BOT_URL)],
            [bot.InlineKeyboardButton("🟢 HUMAN SUPPORT", url=SUPPORT_URL)],
        ])
    if intent == "casino":
        return bot.InlineKeyboardMarkup([
            [bot.InlineKeyboardButton("🔴 OPEN CASINO", url=CASINO_URL)],
            [bot.InlineKeyboardButton("🟢 PLAY NOW", url=PRODUCT_BOT_URL)],
            [bot.InlineKeyboardButton("🟢 HUMAN SUPPORT", url=SUPPORT_URL)],
        ])
    if intent in {"deposit", "withdrawal", "bonus", "account", "register", "support"}:
        return bot.InlineKeyboardMarkup([
            [bot.InlineKeyboardButton("🟢 PLAY NOW", url=PRODUCT_BOT_URL)],
            [bot.InlineKeyboardButton("🌐 SIGN UP ON WEBSITE", url=WEBSITE_URL)],
            [bot.InlineKeyboardButton("🟢 HUMAN SUPPORT", url=SUPPORT_URL)],
        ])
    return _main_keyboard()


def _reply_payload(intent, first_reply=False):
    if first_reply or intent in {"greeting", "general"}:
        return (
            "👋 <b>Welcome to BETROXY</b>\n\n"
            "How can we help you today?\n"
            "Choose an option below, or simply type your question.\n\n"
            "You can type things like:\n"
            "• deposit not received\n"
            "• withdrawal pending\n"
            "• bonus\n"
            "• login problem\n\n"
            "⚡ We’ll guide you to the right place quickly.",
            _main_keyboard(),
            "engaged",
        )

    if intent == "deposit":
        return (
            "🔵 <b>Deposit Help</b>\n\n"
            "If your deposit is pending or not credited, send the amount, payment method and approximate time here.\n\n"
            "For a new deposit, use PLAY NOW below.",
            _intent_keyboard(intent),
            "account_support",
        )

    if intent == "withdrawal":
        return (
            "🟠 <b>Withdrawal Help</b>\n\n"
            "If your withdrawal is pending, send the amount and approximate request time here.\n\n"
            "Our support team can review it while you continue in this chat.",
            _intent_keyboard(intent),
            "account_support",
        )

    if intent == "bonus":
        return (
            "🟣 <b>Bonus & Offers</b>\n\n"
            "Open BETROXY to view current promotions for your account. If you have a specific bonus issue, type it here.",
            _intent_keyboard(intent),
            "engaged",
        )

    if intent == "account":
        return (
            "🔐 <b>Login / Account Help</b>\n\n"
            "Tell us what is happening — OTP issue, password problem, login error or account access — and support can assist.",
            _intent_keyboard(intent),
            "account_support",
        )

    if intent == "register":
        return (
            "🌐 <b>Create Your BETROXY Account</b>\n\n"
            "Use PLAY NOW for the Telegram experience, or SIGN UP ON WEBSITE if you prefer the web.",
            _intent_keyboard(intent),
            "registration_intent",
        )

    if intent == "sportsbook":
        return (
            "🟡 <b>BETROXY Sportsbook</b>\n\n"
            "Open the sportsbook below. If you need help with a market, account or payment, type your question here.",
            _intent_keyboard(intent),
            "product_interest",
        )

    if intent == "casino":
        return (
            "🔴 <b>BETROXY Casino</b>\n\n"
            "Open the casino below. If you need help with a game, account or payment, type your question here.",
            _intent_keyboard(intent),
            "product_interest",
        )

    return (
        "🟢 <b>Support</b>\n\n"
        "Your message is visible to our support team. Please describe the issue and continue here.\n\n"
        "You can also open Human Support below.",
        _intent_keyboard("support"),
        "support_needed",
    )


async def _send_smart_reply(context, enquiry, intent):
    first_reply = not bool(enquiry.get("auto_ack_sent_at"))
    text, keyboard, stage = _reply_payload(intent, first_reply=first_reply)

    # A short typing effect makes the response feel less mechanical.
    try:
        await context.bot.send_chat_action(
            chat_id=int(enquiry["customer_chat_id"]),
            action="typing",
            business_connection_id=str(enquiry["connection_id"]),
        )
        await asyncio.sleep(0.8)
    except Exception:
        pass

    # Keep the existing banner only on the first ever automated reply.
    if first_reply:
        try:
            await context.bot.send_photo(
                chat_id=int(enquiry["customer_chat_id"]),
                photo=v63.BANNER_URL,
                caption="✨ <b>BETROXY</b> • Sports • Casino • Exchange",
                parse_mode=bot.ParseMode.HTML,
                business_connection_id=str(enquiry["connection_id"]),
            )
        except Exception:
            bot.logger.exception("V66_BUSINESS_WELCOME_BANNER_FAILED")

    try:
        sent = await context.bot.send_message(
            chat_id=int(enquiry["customer_chat_id"]),
            text=text,
            parse_mode=bot.ParseMode.HTML,
            business_connection_id=str(enquiry["connection_id"]),
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
    except Exception:
        fallback = (
            html.unescape(text.replace("<b>", "").replace("</b>", ""))
            + f"\n\nPLAY NOW: {PRODUCT_BOT_URL}"
            + f"\nSIGN UP: {WEBSITE_URL}"
            + f"\nSUPPORT: {SUPPORT_URL}"
        )
        sent = await context.bot.send_message(
            chat_id=int(enquiry["customer_chat_id"]),
            text=fallback,
            business_connection_id=str(enquiry["connection_id"]),
            disable_web_page_preview=True,
        )
        text = fallback

    if first_reply:
        biz51.v49._mark_auto_ack(enquiry["id"])
    biz51.v49._record_outbound(enquiry["id"], getattr(sent, "message_id", None), text)
    return biz51._update_lead_state(
        enquiry["id"], intent=intent, stage=stage, auto_replied=True
    )


# Patch only the customer-facing Business reply engine.
biz51._reply_payload = _reply_payload
biz51._send_smart_reply = _send_smart_reply
bot.logger.warning("V66_SMART_CUSTOMER_EXPERIENCE active=on colorful_emoji_buttons=on")


if __name__ == "__main__":
    v63.apply_signup_cta()
    v63.enable_business_smart_auto_reply()
    v63.run_banner_self_test_once()
    bot.main()
