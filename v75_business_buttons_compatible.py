import html
import os
import json
import requests

import bot
import v74_restore_high_ux_business_match as v74

# V75 - Telegram Business-compatible version of the higher BETROXY UX.
# Root cause fixed: WebAppInfo buttons are not allowed in messages sent on behalf
# of a Telegram Business account. Use URL/deep-link buttons only in Business replies.

v72 = v74.v72
v71 = v74.v71
v70 = v74.v70
v68 = v74.v68
v63 = v74.v63
biz51 = v74.biz51
v66 = v74.v66
v59 = v74.v59

PLAY_NOW_URL = v72.PLAY_NOW_URL
WEBSITE_URL = "https://betroxy.com/"
SUPPORT_URL = "https://t.me/betroxysports"
UPDATES_URL = "https://t.me/betroxyupdates"
REFER_URL = "https://t.me/BetroxyOfficialBot?start=refer"
HOW_URL = WEBSITE_URL


def _btn(text, url, style=None):
    kwargs = {"url": url}
    if style:
        kwargs["api_kwargs"] = {"style": style}
    try:
        return bot.InlineKeyboardButton(text, **kwargs)
    except TypeError:
        kwargs.pop("api_kwargs", None)
        return bot.InlineKeyboardButton(text, **kwargs)


def _business_menu(styled=True):
    def b(text, url, style=None):
        return _btn(text, url, style if styled else None)

    return bot.InlineKeyboardMarkup([
        [b("🚀 Open BETROXY App", PLAY_NOW_URL, "success")],
        [
            b("👤 Account & Services", WEBSITE_URL, "primary"),
            b("📜 Transactions", WEBSITE_URL, "primary"),
        ],
        [
            b("🎧 Help & Support", SUPPORT_URL, "primary"),
            b("📢 Updates", UPDATES_URL, "success"),
        ],
        [
            b("👥 Refer a Friend", REFER_URL, "primary"),
            b("ℹ️ How It Works", HOW_URL, "primary"),
        ],
    ])


def _business_reply_payload(intent, first_reply=False):
    if first_reply or intent in {"greeting", "general"}:
        return (
            v59.v59_public_welcome_text(returning=False),
            _business_menu(styled=True),
            "engaged",
        )
    # Keep all V72 intent routing/direct Mini App deep links for product intents.
    return v74._v72_reply_payload(intent, first_reply=False)


async def _send_business_reply(context, enquiry, intent):
    first_reply = not bool(enquiry.get("auto_ack_sent_at"))
    text, keyboard, stage = _business_reply_payload(intent, first_reply=first_reply)

    if first_reply:
        try:
            await context.bot.send_photo(
                chat_id=int(enquiry["customer_chat_id"]),
                photo=v63.BANNER_URL,
                caption="✨ <b>BETROXY</b> • Daily Quiz & Rewards",
                parse_mode=bot.ParseMode.HTML,
                business_connection_id=str(enquiry["connection_id"]),
            )
        except Exception as exc:
            bot.logger.exception("V75_BUSINESS_BANNER_FAILED: %s", exc)

    # First try the full styled version. If Telegram rejects styling in a
    # particular client/account combination, retry immediately without styles.
    try:
        sent = await context.bot.send_message(
            chat_id=int(enquiry["customer_chat_id"]),
            text=text,
            parse_mode=bot.ParseMode.HTML,
            business_connection_id=str(enquiry["connection_id"]),
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
        bot.logger.warning("V75_BUSINESS_BUTTONS_SENT styled=on")
    except Exception as first_exc:
        bot.logger.warning("V75 styled Business keyboard rejected; retrying unstyled: %s", first_exc)
        try:
            sent = await context.bot.send_message(
                chat_id=int(enquiry["customer_chat_id"]),
                text=text,
                parse_mode=bot.ParseMode.HTML,
                business_connection_id=str(enquiry["connection_id"]),
                reply_markup=_business_menu(styled=False) if (first_reply or intent in {"greeting", "general"}) else keyboard,
                disable_web_page_preview=True,
            )
            bot.logger.warning("V75_BUSINESS_BUTTONS_SENT styled=off fallback=success")
        except Exception as second_exc:
            bot.logger.exception("V75_BUSINESS_BUTTONS_FAILED styled_and_unstyled: %s", second_exc)
            # Final fallback retains visible destinations instead of silently
            # dropping all CTAs.
            fallback = (
                html.unescape(text.replace("<b>", "").replace("</b>", ""))
                + "\n\n🚀 Open BETROXY App\n" + PLAY_NOW_URL
                + "\n\n🎧 Help & Support\n" + SUPPORT_URL
                + "\n\n📢 Updates\n" + UPDATES_URL
                + "\n\n🌐 Website\n" + WEBSITE_URL
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


biz51._reply_payload = _business_reply_payload
biz51._send_smart_reply = _send_business_reply


def run_v75_smoke_test():
    token = os.getenv("BOT_TOKEN", "").strip()
    admin_id = os.getenv("ADMIN_ID", "").strip()
    if not token or not admin_id:
        return

    keyboard = {
        "inline_keyboard": [
            [{"text": "🚀 Open BETROXY App", "url": PLAY_NOW_URL, "style": "success"}],
            [
                {"text": "👤 Account & Services", "url": WEBSITE_URL, "style": "primary"},
                {"text": "📜 Transactions", "url": WEBSITE_URL, "style": "primary"},
            ],
            [
                {"text": "🎧 Help & Support", "url": SUPPORT_URL, "style": "primary"},
                {"text": "📢 Updates", "url": UPDATES_URL, "style": "success"},
            ],
            [
                {"text": "👥 Refer a Friend", "url": REFER_URL, "style": "primary"},
                {"text": "ℹ️ How It Works", "url": HOW_URL, "style": "primary"},
            ],
        ]
    }
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={
            "chat_id": admin_id,
            "text": "✅ V75 BUSINESS BUTTON MENU TEST\nSame customer layout, Business-compatible URL/deep-link buttons only.",
            "reply_markup": json.dumps(keyboard),
        },
        timeout=20,
    )
    p = r.json() if r.content else {}
    if r.ok and p.get("ok"):
        bot.logger.warning("V75_BUSINESS_MENU_SMOKE_TEST SUCCESS buttons=7 no_web_app=on play_now_native=on")
    else:
        bot.logger.error("V75_BUSINESS_MENU_SMOKE_TEST FAILED status=%s body=%s", r.status_code, p)


bot.logger.warning(
    "V75_BUSINESS_COMPATIBLE_BUTTONS active=on no_web_app_buttons=on high_ux_labels=on"
)

if __name__ == "__main__":
    v63.apply_signup_cta()
    v63.enable_business_smart_auto_reply()
    run_v75_smoke_test()
    bot.main()
