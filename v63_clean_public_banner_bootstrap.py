import os
import html
import requests
from datetime import datetime, timezone

import bot
import v62_ai_admin_assistant_bootstrap as v62
import v51_telegram_business_auto_conversion_bootstrap as biz51

BANNER_URL = (
    "https://raw.githubusercontent.com/"
    "mohit171993/betroxy-telegram-bot/main/oldwelcome_banner.jpg"
)

BUSINESS_SUPPORT_URL = "https://t.me/betroxysports"


def apply_signup_cta():
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


def enable_business_smart_auto_reply():
    try:
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE telegram_business_settings
                    SET auto_ack_enabled=TRUE,
                        updated_at=NOW()
                    WHERE id=1
                    """
                )
                cur.execute(
                    """
                    UPDATE telegram_business_enquiries
                    SET auto_ack_sent_at=NULL,
                        auto_reply_count=0,
                        last_auto_reply_at=NULL
                    WHERE status='open'
                    """
                )
            conn.commit()
        bot.logger.warning("BUSINESS_SMART_AUTO_REPLY forced=ON stale_open_state_reset=on")
    except Exception as exc:
        bot.logger.exception("Could not enable BUSINESS_SMART_AUTO_REPLY: %s", exc)


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


biz51._original_reply_payload = biz51._reply_payload
_original_update_lead_state = biz51._update_lead_state


def _greeting_retry_update_lead_state(enquiry_id, intent=None, stage=None, auto_replied=False):
    row = _original_update_lead_state(enquiry_id, intent=intent, stage=stage, auto_replied=auto_replied)
    if row and intent == "greeting" and not auto_replied:
        count = int(row.get("auto_reply_count") or 0)
        last = row.get("last_auto_reply_at")
        allowed = count < 5
        if last:
            try:
                allowed = allowed and (datetime.now(timezone.utc) - last).total_seconds() >= 30
            except Exception:
                pass
        if allowed:
            row = dict(row)
            row["auto_ack_sent_at"] = None
    return row


biz51._update_lead_state = _greeting_retry_update_lead_state
biz51._reply_payload = _upgraded_business_reply_payload
biz51._send_smart_reply = _upgraded_send_smart_reply
bot.logger.warning("BUSINESS_ENQUIRY_UI_UPGRADE active=on greeting_retry=on")


def _bot_analytics_stats():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(DISTINCT telegram_user_id) AS n FROM referrals")
            total = int((cur.fetchone() or {}).get("n") or 0)
            cur.execute("SELECT COUNT(DISTINCT telegram_user_id) AS n FROM referrals WHERE joined_at >= DATE_TRUNC('day', NOW())")
            today = int((cur.fetchone() or {}).get("n") or 0)
            cur.execute("SELECT COUNT(DISTINCT telegram_user_id) AS n FROM referrals WHERE joined_at >= NOW() - INTERVAL '7 days'")
            week = int((cur.fetchone() or {}).get("n") or 0)
            cur.execute("SELECT COUNT(DISTINCT telegram_user_id) AS n FROM referrals WHERE joined_at >= NOW() - INTERVAL '30 days'")
            month = int((cur.fetchone() or {}).get("n") or 0)
            cur.execute("SELECT COUNT(DISTINCT telegram_user_id) AS n FROM referrals WHERE agent_id IS NULL")
            direct = int((cur.fetchone() or {}).get("n") or 0)
            cur.execute("SELECT COUNT(DISTINCT telegram_user_id) AS n FROM referrals WHERE agent_id IS NOT NULL")
            referred = int((cur.fetchone() or {}).get("n") or 0)
            cur.execute(
                """
                SELECT COALESCE(a.code, 'direct') AS source,
                       COUNT(DISTINCT r.telegram_user_id) AS n
                FROM referrals r
                LEFT JOIN agents a ON a.id=r.agent_id
                GROUP BY COALESCE(a.code, 'direct')
                ORDER BY n DESC
                LIMIT 8
                """
            )
            sources = cur.fetchall()
    return total, today, week, month, direct, referred, sources


def _bot_analytics_text():
    total, today, week, month, direct, referred, sources = _bot_analytics_stats()
    lines = [
        "📊 <b>BETROXY BOT ANALYTICS</b>",
        "",
        f"👥 Total unique users: <b>{total:,}</b>",
        f"🟢 Today: <b>{today:,}</b>",
        f"📅 Last 7 days: <b>{week:,}</b>",
        f"🗓 Last 30 days: <b>{month:,}</b>",
        "",
        f"🔗 Direct starts: <b>{direct:,}</b>",
        f"🤝 Referral starts: <b>{referred:,}</b>",
    ]
    if sources:
        lines.extend(["", "<b>Top sources</b>"])
        for row in sources:
            source = str(row.get("source") or "direct")
            label = "Direct / no referral" if source == "direct" else source
            lines.append(f"• {html.escape(label)} — <b>{int(row.get('n') or 0):,}</b>")
    lines.extend([
        "",
        "<i>Unique user = one Telegram user recorded when they first started/interacted with @BetroxyOfficialBot.</i>",
    ])
    return "\n".join(lines)


def _bot_analytics_menu():
    return bot.InlineKeyboardMarkup([
        [bot.InlineKeyboardButton("🔄 Refresh Analytics", callback_data="bot_analytics")],
        [bot.InlineKeyboardButton("⬅️ Admin Panel", callback_data="admin_home")],
    ])


_original_admin_menu = bot.admin_menu

def _admin_menu_with_bot_analytics():
    markup = _original_admin_menu()
    rows = [list(row) for row in markup.inline_keyboard]
    if not any(any(getattr(btn, "callback_data", None) == "bot_analytics" for btn in row) for row in rows):
        rows.insert(1, [bot.InlineKeyboardButton("📊 Bot Analytics", callback_data="bot_analytics")])
    return bot.InlineKeyboardMarkup(rows)


bot.admin_menu = _admin_menu_with_bot_analytics
_previous_callback_handler = bot.callback_handler


async def _callback_handler_with_bot_analytics(update, context):
    q = update.callback_query
    if q and (q.data or "") == "bot_analytics":
        await q.answer()
        if not bot.is_admin(q.from_user.id):
            return
        await q.message.reply_text(
            _bot_analytics_text(),
            parse_mode=bot.ParseMode.HTML,
            reply_markup=_bot_analytics_menu(),
        )
        return
    return await _previous_callback_handler(update, context)


bot.callback_handler = _callback_handler_with_bot_analytics
bot.logger.warning("BOT_ANALYTICS_LIVE active=on admin_menu=on callback=on")


def run_banner_self_test_once():
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
    enable_business_smart_auto_reply()
    run_banner_self_test_once()
    bot.main()
