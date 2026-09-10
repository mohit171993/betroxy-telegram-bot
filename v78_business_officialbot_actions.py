import time
from urllib.parse import quote

import bot
import v77_business_buttons_final as v77

v75 = v77.v75
v63 = v75.v63
biz51 = v75.biz51
v59 = v75.v59

# Use the already verified native Mini App deep link for every app/account action.
MINIAPP_URL = v75.PLAY_NOW_URL
UPDATES_URL = bot.UPDATES_URL
TELEGRAM_SUPPORT_URL = bot.TELEGRAM_SUPPORT_URL
WHATSAPP_SUPPORT_URL = bot.WHATSAPP_SUPPORT_URL
REFER_SHARE_TEXT = (
    "Check out BETROXY — official access, games, updates and support:\n"
    "https://t.me/BetroxyOfficialBot"
)
REFER_WHATSAPP_URL = "https://api.whatsapp.com/send?text=" + quote(REFER_SHARE_TEXT, safe="")

_previous_callback_handler = bot.callback_handler


def _btn(text, *, callback_data=None, url=None, style=None):
    kwargs = {"text": text, "callback_data": callback_data, "url": url}
    if style:
        kwargs["api_kwargs"] = {"style": style}
    try:
        return bot.InlineKeyboardButton(**kwargs)
    except TypeError:
        kwargs.pop("api_kwargs", None)
        return bot.InlineKeyboardButton(**kwargs)


def business_main_menu(styled=True):
    def b(text, *, callback_data=None, url=None, style=None):
        return _btn(
            text,
            callback_data=callback_data,
            url=url,
            style=style if styled else None,
        )

    return bot.InlineKeyboardMarkup([
        [b("🚀 Open BETROXY App", url=MINIAPP_URL, style="success")],
        [
            b("👤 Account & Services", url=MINIAPP_URL, style="primary"),
            b("📜 Transactions", url=MINIAPP_URL, style="primary"),
        ],
        [
            b("🎧 Help & Support", callback_data="bizux_support_home", style="primary"),
            b("📢 Updates", url=UPDATES_URL, style="success"),
        ],
        [
            b("👥 Refer a Friend", url=REFER_WHATSAPP_URL, style="success"),
            b("ℹ️ How It Works", callback_data="bizux_how_it_works", style="primary"),
        ],
    ])


def business_support_menu(styled=True):
    def b(text, *, callback_data=None, url=None, style=None):
        return _btn(text, callback_data=callback_data, url=url, style=style if styled else None)

    return bot.InlineKeyboardMarkup([
        [
            b("🔐 Account / Login", callback_data="bizux_help_account", style="primary"),
            b("💳 Payment Help", callback_data="bizux_help_payment", style="primary"),
        ],
        [
            b("💸 Withdrawal Help", callback_data="bizux_help_withdrawal", style="primary"),
            b("🛠 Technical Issue", callback_data="bizux_help_technical", style="primary"),
        ],
        [
            b("💬 Telegram Support", url=TELEGRAM_SUPPORT_URL, style="success"),
            b("🟢 WhatsApp Support", url=WHATSAPP_SUPPORT_URL, style="success"),
        ],
        [b("⬅️ Main Menu", callback_data="bizux_home")],
    ])


def human_support_menu(styled=True):
    def b(text, *, callback_data=None, url=None, style=None):
        return _btn(text, callback_data=callback_data, url=url, style=style if styled else None)

    return bot.InlineKeyboardMarkup([
        [
            b("💬 Telegram Support", url=TELEGRAM_SUPPORT_URL, style="primary"),
            b("🟢 WhatsApp Support", url=WHATSAPP_SUPPORT_URL, style="success"),
        ],
        [b("⬅️ Support Menu", callback_data="bizux_support_home")],
        [b("🏠 Main Menu", callback_data="bizux_home")],
    ])


def _business_reply_payload(intent, first_reply=False):
    if first_reply or intent in {"greeting", "general"}:
        return (
            v59.v59_public_welcome_text(returning=False),
            business_main_menu(styled=True),
            "engaged",
        )
    return v75.v74._v72_reply_payload(intent, first_reply=False)


# Patch V75 sender globals. V75's sender resolves this function dynamically.
v75._business_menu = business_main_menu
v75._business_reply_payload = _business_reply_payload
biz51._reply_payload = _business_reply_payload
biz51._send_smart_reply = v75._send_business_reply


async def _send_business_callback(context, q, text, markup):
    message = q.message
    chat_id = message.chat_id
    business_connection_id = getattr(message, "business_connection_id", None)
    kwargs = dict(
        chat_id=chat_id,
        text=text,
        parse_mode=bot.ParseMode.HTML,
        reply_markup=markup,
        disable_web_page_preview=True,
    )
    if business_connection_id:
        kwargs["business_connection_id"] = str(business_connection_id)
    return await context.bot.send_message(**kwargs)


async def _send_help(context, q, title, body):
    await _send_business_callback(
        context,
        q,
        f"{title}\n\n{body}\n\n"
        "If the issue continues, contact support below and include any relevant reference or screenshot.",
        human_support_menu(styled=True),
    )


async def v78_callback_handler(update, context):
    q = update.callback_query
    data = (q.data or "") if q else ""
    if not q:
        return await _previous_callback_handler(update, context)

    if data == "bizux_home":
        await q.answer()
        await _send_business_callback(
            context,
            q,
            v59.v59_public_welcome_text(returning=True),
            business_main_menu(styled=True),
        )
        return

    if data == "bizux_support_home":
        await q.answer()
        await _send_business_callback(
            context,
            q,
            "🎧 <b>BETROXY Help Center</b>\n\n"
            "Choose the topic that best matches what you need. You can also contact a support agent directly.",
            business_support_menu(styled=True),
        )
        return

    if data == "bizux_how_it_works":
        await q.answer()
        await _send_business_callback(
            context,
            q,
            "ℹ️ <b>Using BETROXY is simple</b>\n\n"
            "1️⃣ Tap <b>Open BETROXY App</b> for account and platform access.\n"
            "2️⃣ Use <b>Account & Services</b> or <b>Transactions</b> to open the Mini App.\n"
            "3️⃣ Use <b>Help & Support</b> for guided assistance.\n"
            "4️⃣ Follow <b>Updates</b> for official announcements.",
            bot.InlineKeyboardMarkup([
                [_btn("🚀 Open BETROXY App", url=MINIAPP_URL, style="success")],
                [_btn("🎧 Help & Support", callback_data="bizux_support_home", style="primary")],
                [_btn("⬅️ Main Menu", callback_data="bizux_home")],
            ]),
        )
        return

    if data == "bizux_help_account":
        await q.answer()
        await _send_help(
            context, q, "🔐 <b>Account / Login Help</b>",
            "Open the BETROXY Mini App and try signing in again. If you see an error, note the exact message and whether it involves login, password or verification.",
        )
        return

    if data == "bizux_help_payment":
        await q.answer()
        await _send_help(
            context, q, "💳 <b>Payment Help</b>",
            "For a pending payment, keep the amount, approximate time and transaction/reference details ready. Do not send passwords or OTPs.",
        )
        return

    if data == "bizux_help_withdrawal":
        await q.answer()
        await _send_help(
            context, q, "💸 <b>Withdrawal Help</b>",
            "Check the latest status in your account first. If it remains pending, keep the amount, request time and relevant reference available for support.",
        )
        return

    if data == "bizux_help_technical":
        await q.answer()
        await _send_help(
            context, q, "🛠 <b>Technical Help</b>",
            "Try reopening the Mini App once. If the issue continues, send the error message plus a screenshot and mention your device.",
        )
        return

    return await _previous_callback_handler(update, context)


bot.callback_handler = v78_callback_handler

bot.logger.warning(
    "V78_BUSINESS_OFFICIAL_ACTIONS active=on account=miniapp transactions=miniapp "
    "support=interactive whatsapp_referral=on"
)

if __name__ == "__main__":
    v63.apply_signup_cta()
    v63.enable_business_smart_auto_reply()
    bot.logger.warning("V78 polling handover delay=12s")
    time.sleep(12)
    bot.main()
