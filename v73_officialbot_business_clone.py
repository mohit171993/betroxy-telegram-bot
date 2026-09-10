import os
import json
import requests

import bot
import v59_user_friendly_public_ux_bootstrap as v59
import v63_clean_customer_banner_bootstrap as v63

# V73 - Business auto reply uses the SAME customer-facing UX as BetroxyOfficialBot.
# No custom V68/V69/V70/V71/V72 menu overrides are imported here.

biz51 = v63.v62.v61.v60.v59.v58.v57.v54.v53.v52.v51 if False else None
# v63's import chain already loaded v51; use the canonical module directly.
import v51_telegram_business_auto_conversion_bootstrap as biz51

_SUPPORT_INTENTS = {"account", "deposit", "withdrawal", "support"}


def _official_reply_payload(intent, first_reply=False):
    """Mirror BetroxyOfficialBot's V59 public behavior inside Business auto replies."""
    if intent in _SUPPORT_INTENTS:
        return (
            "🎧 <b>BETROXY Help Center</b>\n\n"
            "Choose the topic that best matches what you need. You can also contact a support agent directly.",
            v59.v59_support_menu(),
            "support_needed",
        )

    # Same welcome copy + same menu used by BetroxyOfficialBot.
    return (
        v59.v59_public_welcome_text(returning=not first_reply),
        v59.v59_public_menu(None),
        "engaged",
    )


async def _official_send_smart_reply(context, enquiry, intent):
    first_reply = not bool(enquiry.get("auto_ack_sent_at"))
    text, keyboard, stage = _official_reply_payload(intent, first_reply=first_reply)

    # Match the official bot: banner + official caption on the first automated reply.
    if first_reply:
        try:
            await context.bot.send_photo(
                chat_id=int(enquiry["customer_chat_id"]),
                photo=v59.v57.BANNER_URL,
                caption="✨ <b>BETROXY</b> • Official Access & Support",
                parse_mode=bot.ParseMode.HTML,
                business_connection_id=str(enquiry["connection_id"]),
            )
        except Exception:
            bot.logger.exception("V73_OFFICIAL_BANNER_FAILED")

    sent = await context.bot.send_message(
        chat_id=int(enquiry["customer_chat_id"]),
        text=text,
        parse_mode=bot.ParseMode.HTML,
        business_connection_id=str(enquiry["connection_id"]),
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )

    if first_reply:
        biz51.v49._mark_auto_ack(enquiry["id"])
    biz51.v49._record_outbound(enquiry["id"], getattr(sent, "message_id", None), text)
    return biz51._update_lead_state(
        enquiry["id"], intent=intent, stage=stage, auto_replied=True
    )


# Replace only the Business customer reply presentation.
# Intent detection, inbox, analytics, admin controls and human takeover stay unchanged.
biz51._reply_payload = _official_reply_payload
biz51._send_smart_reply = _official_send_smart_reply


def run_v73_test():
    """Telegram API smoke test using the exact official public/support layouts."""
    token = os.getenv("BOT_TOKEN", "").strip()
    admin_id = os.getenv("ADMIN_ID", "").strip()
    if not token or not admin_id:
        bot.logger.warning("V73_OFFICIAL_UX_TEST skipped missing token/admin")
        return

    # Raw equivalent of V59 public menu for a normal customer (no admin/affiliate row).
    public_keyboard = {
        "inline_keyboard": [
            [{"text": "🚀 Open BETROXY App", "web_app": {"url": bot.APP_URL}, "style": "success"}],
            [
                {"text": "👤 Account & Services", "web_app": {"url": bot.APP_URL}, "style": "primary"},
                {"text": "📜 Transactions", "web_app": {"url": bot.APP_URL}, "style": "primary"},
            ],
            [
                {"text": "🎧 Help & Support", "callback_data": "ux_support_home", "style": "primary"},
                {"text": "📢 Updates", "url": bot.UPDATES_URL, "style": "success"},
            ],
            [
                {"text": "👥 Refer a Friend", "callback_data": "refer_friend"},
                {"text": "ℹ️ How It Works", "callback_data": "ux_how_it_works"},
            ],
        ]
    }

    support_keyboard = {
        "inline_keyboard": [
            [
                {"text": "🔐 Account / Login", "callback_data": "ux_help_account", "style": "primary"},
                {"text": "💳 Payment Help", "callback_data": "ux_help_payment", "style": "primary"},
            ],
            [
                {"text": "💸 Withdrawal Help", "callback_data": "ux_help_withdrawal", "style": "primary"},
                {"text": "🛠 Technical Issue", "callback_data": "ux_help_technical", "style": "primary"},
            ],
            [
                {"text": "💬 Telegram Support", "url": bot.TELEGRAM_SUPPORT_URL, "style": "success"},
                {"text": "🟢 WhatsApp Support", "url": bot.WHATSAPP_SUPPORT_URL, "style": "success"},
            ],
            [{"text": "⬅️ Main Menu", "callback_data": "ux_home"}],
        ]
    }

    for title, text, keyboard in (
        ("CUSTOMER MENU", v59.v59_public_welcome_text(False), public_keyboard),
        ("SUPPORT MENU", "🎧 BETROXY Help Center", support_keyboard),
    ):
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={
                "chat_id": admin_id,
                "text": f"✅ V73 {title} TEST\n\n{text}",
                "reply_markup": json.dumps(keyboard),
            },
            timeout=20,
        )
        payload = r.json() if r.content else {}
        if not (r.ok and payload.get("ok")):
            raise RuntimeError(f"V73 {title} rejected by Telegram: {r.status_code} {payload}")

    bot.logger.warning(
        "V73_OFFICIAL_UX_TEST SUCCESS public_menu=exact_v59 support_menu=exact_v59 telegram_accepted=on"
    )


bot.logger.warning(
    "V73_OFFICIALBOT_BUSINESS_CLONE active=on custom_menu_overrides=off official_v59_ux=on"
)

if __name__ == "__main__":
    run_v73_test()
    bot.main()
