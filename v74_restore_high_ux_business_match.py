import html

import bot
import v72_final_deeplink_fix as v72
import v59_user_friendly_public_ux_bootstrap as v59

# V74 - restore the higher-version public UX and make Telegram Business
# auto-reply use the SAME customer-facing welcome/menu design.
# This keeps the V72 deep-link fixes for product-specific replies.

v71 = v72.v71
v70 = v72.v70
v68 = v72.v68
v63 = v72.v63
biz51 = v72.biz51
v66 = v72.v66

# Preserve the existing V72 smart intent payload for non-greeting messages.
_v72_reply_payload = biz51._reply_payload


def _customer_public_menu():
    # user_id=None guarantees no Admin Panel / affiliate-only controls are shown
    # to Business customers. Otherwise this is the exact same V59 public UX menu
    # used by the higher-version @BetroxyOfficialBot chain.
    return v59.v59_public_menu(None)


def _business_reply_payload(intent, first_reply=False):
    if first_reply or intent in {"greeting", "general"}:
        return (
            v59.v59_public_welcome_text(returning=False),
            _customer_public_menu(),
            "engaged",
        )
    # Keep V72's verified deep-link routing for explicit product/support intents.
    return _v72_reply_payload(intent, first_reply=False)


async def _send_business_reply(context, enquiry, intent):
    first_reply = not bool(enquiry.get("auto_ack_sent_at"))
    text, keyboard, stage = _business_reply_payload(intent, first_reply=first_reply)

    # Match the higher-version public bot banner exactly.
    if first_reply:
        try:
            await context.bot.send_photo(
                chat_id=int(enquiry["customer_chat_id"]),
                photo=v63.BANNER_URL,
                caption="✨ <b>BETROXY</b> • Official Access & Support",
                parse_mode=bot.ParseMode.HTML,
                business_connection_id=str(enquiry["connection_id"]),
            )
        except Exception:
            bot.logger.exception("V74_BUSINESS_WELCOME_BANNER_FAILED")

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
        # Do not fall back to the old generic BetroxyBot URL.
        fallback = html.unescape(text.replace("<b>", "").replace("</b>", ""))
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


# Restore higher-version public /start chain from V72 and only patch Business replies.
biz51._reply_payload = _business_reply_payload
biz51._send_smart_reply = _send_business_reply
bot.logger.warning(
    "V74_RESTORE_HIGH_UX active=on public=v72 business_welcome=public_menu_exact admin_hidden=on"
)


if __name__ == "__main__":
    v63.apply_signup_cta()
    v63.enable_business_smart_auto_reply()
    bot.main()
