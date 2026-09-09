import os
import html
import requests

import bot
import v62_ai_admin_assistant_bootstrap as v62
import v51_telegram_business_auto_conversion_bootstrap as biz51

# Use the original high-resolution BETROXY banner already stored in the repo.
BANNER_URL = (
    "https://raw.githubusercontent.com/"
    "mohit171993/betroxy-telegram-bot/main/oldwelcome_banner.jpg"
)

BUSINESS_SUPPORT_URL = "https://t.me/betroxysports"


def apply_signup_cta():
    """Change the live Batraxy primary CTA label to SIGN UP without changing its destination."""
    try:
        bot.DEFAULT_LANDING_HTML = (
            bot.DEFAULT_LANDING_HTML
            .replace("🚀 PLAY ON WEBSITE", "👤 SIGN UP")
            .replace("PLAY ON WEBSITE", "SIGN UP")
        )

        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE landing_themes
                    SET index_html = REPLACE(
                        REPLACE(index_html, '🚀 PLAY ON WEBSITE', '👤 SIGN UP'),
                        'PLAY ON WEBSITE', 'SIGN UP'
                    )
                    WHERE is_active=TRUE
                    """
                )
            conn.commit()
        bot.logger.warning("BATraxy CTA updated: SIGN UP")
    except Exception as exc:
        bot.logger.exception("Could not update Batraxy SIGN UP CTA: %s", exc)


def _business_welcome_keyboard():
    return bot.InlineKeyboardMarkup([
        [bot.InlineKeyboardButton("🚀 PLAY NOW", url=biz51.BETROXY_PRODUCT_BOT)],
        [
            bot.InlineKeyboardButton("🎰 Casino", url=biz51.BETROXY_CASINO),
            bot.InlineKeyboardButton("🏏 Sportsbook", url=biz51.BETROXY_SPORTSBOOK),
        ],
        [
            bot.InlineKeyboardButton("🌐 Website", url=biz51.BETROXY_WEBSITE),
            bot.InlineKeyboardButton("🎧 Support", url=BUSINESS_SUPPORT_URL),
        ],
    ])


def _upgraded_business_reply_payload(intent, first_reply=False):
    if first_reply or intent in {"greeting", "general"}:
        return (
            "👋 <b>Welcome to BETROXY!</b> ✨\n\n"
            "Thanks for contacting us. Choose an option below for quick access.\n\n"
            "⚡ <b>Need help? Just type one of these:</b>\n"
            "💳 Deposit   •   💸 Withdrawal\n"
            "🎁 Bonus     •   🔐 Login\n"
            "🏏 Sportsbook   •   🎰 Casino\n\n"
            "Our support team can also continue with you here.",
            _business_welcome_keyboard(),
            "engaged",
        )
    return biz51._original_reply_payload(intent, first_reply=False)


async def _upgraded_send_smart_reply(context, enquiry, intent):
    first_reply = not bool(enquiry.get("auto_ack_sent_at"))
    text, keyboard, stage = biz51._reply_payload(intent, first_reply=first_reply)

    # Give new enquiries a branded visual header. If Telegram Business rejects
    # the photo for any reason, the text/buttons still continue normally.
    if first_reply:
        try:
            await context.bot.send_photo(
                chat_id=int(enquiry["customer_chat_id"]),
                photo=BANNER_URL,
                caption="✨ <b>BETROXY</b> • Casino • Sportsbook • Exchange",
                parse_mode=bot.ParseMode.HTML,
                business_connection_id=str(enquiry["connection_id"]),
            )
        except Exception:
            bot.logger.exception("BUSINESS_WELCOME_BANNER_FAILED")

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
            + f"\n\nPlay Now: {biz51.BETROXY_PRODUCT_BOT}"
            + f"\nWebsite: {biz51.BETROXY_WEBSITE}"
            + f"\nSupport: {BUSINESS_SUPPORT_URL}"
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


# Preserve the original V51 intent-specific answers, while replacing only the
# first/generic enquiry experience with the upgraded branded layout.
biz51._original_reply_payload = biz51._reply_payload
biz51._reply_payload = _upgraded_business_reply_payload
biz51._send_smart_reply = _upgraded_send_smart_reply
bot.logger.warning("BUSINESS_ENQUIRY_UI_UPGRADE active=on")


def run_banner_self_test_once():
    """Send one deployment-time HQ banner test to ADMIN_ID and log the result."""
    try:
        token = os.getenv("BOT_TOKEN", "").strip()
        admin_id = os.getenv("ADMIN_ID", "").strip()
        if not token or not admin_id:
            bot.logger.warning("V63 HQ banner self-test skipped: BOT_TOKEN/ADMIN_ID missing")
            return

        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            data={
                "chat_id": admin_id,
                "photo": BANNER_URL,
                "caption": "✅ BETROXY HQ banner deployment test",
            },
            timeout=30,
        )
        payload = response.json() if response.content else {}
        if response.ok and payload.get("ok"):
            bot.logger.warning("V63_HQ_BANNER_SELF_TEST SUCCESS")
        else:
            bot.logger.error(
                "V63_HQ_BANNER_SELF_TEST FAILED status=%s response=%s",
                response.status_code,
                payload,
            )
    except Exception as exc:
        bot.logger.exception("V63_HQ_BANNER_SELF_TEST ERROR: %s", exc)


async def v63_start(update, context):
    if getattr(context, "args", None):
        return await v62.v61.v60.v59.v59_start(update, context)

    msg = update.effective_message
    if not msg:
        return

    try:
        await msg.reply_photo(
            photo=BANNER_URL,
            caption="✨ <b>BETROXY</b> • Official Access & Support",
            parse_mode=bot.ParseMode.HTML,
        )
    except Exception as exc:
        bot.logger.exception("V63 HQ public banner failed: %s", exc)

    await msg.reply_text(
        v62.v61.v60.v59.v53.v53_public_welcome_text(),
        parse_mode=bot.ParseMode.HTML,
        reply_markup=v62.v61.v60.v59.v53.v53_public_menu(
            update.effective_user.id if update.effective_user else None
        ),
        disable_web_page_preview=True,
    )


bot.start = v63_start
bot.logger.warning("V63_PUBLIC_BANNER_FIX active=on source=oldwelcome_banner_hq")

if __name__ == "__main__":
    apply_signup_cta()
    run_banner_self_test_once()
    bot.main()
