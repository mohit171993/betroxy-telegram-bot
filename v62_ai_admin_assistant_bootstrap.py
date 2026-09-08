import re
import html

import bot
import v61_premium_checkout_bootstrap as v61
import v56_checkout_whatsapp_bootstrap as checkout

# ============================================================
# V62 - AI-STYLE ADMIN ASSISTANT
# ============================================================
# Lets the admin control common campaign/checkout tasks by typing normal text.
# Existing buttons and all existing workflows remain available as a fallback.
# Destructive deletion requires a confirmation message.
# ============================================================

_previous_chat_handler = bot.chat_handler


def _norm_slug(value):
    value = (value or "").strip()
    m = re.search(r"(?:https?://)?(?:www\.)?batraxy\.com/([^/?#\s]+)", value, flags=re.I)
    if m:
        return m.group(1).strip().lower()
    value = value.strip().lstrip("@").strip("/ ")
    return value.lower()


def _row_from_reference(value):
    ref = _norm_slug(value)
    if not ref:
        return None
    row = bot.campaign_link_by_slug(ref)
    if row:
        return row
    row = bot.campaign_link_by_code(ref)
    if row:
        return row
    try:
        found, _parsed = bot.find_campaign_link_from_input(value)
        return found
    except Exception:
        return None


def _find_target_in_text(text):
    # Prefer full Batraxy URL.
    m = re.search(r"https?://(?:www\.)?batraxy\.com/[^\s]+", text, flags=re.I)
    if m:
        return _row_from_reference(m.group(0))

    # Then explicit words after on/for/to/of.
    patterns = [
        r"(?:checkout|whatsapp|message|number|source|slug|link)\s+(?:on|for|of|to)\s+([A-Za-z0-9._-]+)",
        r"(?:enable|disable|delete|remove|show|open)\s+(?:checkout\s+)?(?:on\s+)?([A-Za-z0-9._-]+)",
        r"^(?:set|change|update)\s+([A-Za-z0-9._-]+)\s+",
    ]
    for p in patterns:
        m = re.search(p, text, flags=re.I)
        if m:
            row = _row_from_reference(m.group(1))
            if row:
                return row

    # Last fallback: scan tokens and try matching a campaign slug/code.
    for token in re.findall(r"[A-Za-z0-9._-]{2,80}", text):
        row = _row_from_reference(token)
        if row:
            return row
    return None


def _phone_from_text(text):
    # Accept +16084301011, 16084301011, spaced/dashed forms.
    matches = re.findall(r"\+?\d[\d\s().-]{6,}\d", text)
    if not matches:
        return None
    digits = re.sub(r"\D+", "", matches[-1])
    if 7 <= len(digits) <= 15:
        return digits
    return None


def _source_from_text(text):
    low = text.lower()
    if "meta" in low or "facebook" in low:
        return "meta_ads"
    if "google" in low:
        return "google_ads"
    if "telegram" in low or re.search(r"\btg\b", low):
        return "telegram"
    if "instagram" in low or "insta" in low:
        return "instagram"
    return None


def _help_text():
    return (
        "🤖 <b>BETROXY Admin Assistant</b>\n\n"
        "You can type normal instructions. Examples:\n\n"
        "• <code>Set meta-ch WhatsApp to +16084301011</code>\n"
        "• <code>Enable checkout on meta-ch</code>\n"
        "• <code>Disable checkout on meta-ch</code>\n"
        "• <code>Change meta-ch message to Hi, I want to join Betroxy</code>\n"
        "• <code>Change meta-ch source to Meta Ads</code>\n"
        "• <code>Create link for https://instagram.com/example</code>\n"
        "• <code>Show meta-ch checkout settings</code>\n"
        "• <code>Show today's campaign report</code>\n"
        "• <code>Run smart checker</code>\n"
        "• <code>Delete https://www.batraxy.com/example</code>\n\n"
        "Permanent delete always asks for confirmation. Buttons still work normally."
    )


async def _send_row_settings(msg, row):
    await msg.reply_text(
        checkout._checkout_settings_text(row),
        parse_mode=bot.ParseMode.HTML,
        reply_markup=checkout._checkout_settings_markup(row),
        disable_web_page_preview=True,
    )


async def ai_admin_chat_handler(update, context):
    user = update.effective_user
    msg = update.effective_message
    if not user or not msg:
        return await _previous_chat_handler(update, context)

    # Only the configured admin gets natural-language control.
    if not bot.is_admin(user.id):
        return await _previous_chat_handler(update, context)

    raw = (msg.text or "").strip()
    low = raw.lower().strip()
    if not raw:
        return await _previous_chat_handler(update, context)

    # Explicit help / capability prompt.
    if low in {"ai", "assistant", "help", "ai help", "what can you do", "commands"} or "what can you do" in low:
        await msg.reply_text(_help_text(), parse_mode=bot.ParseMode.HTML)
        return

    # Pending destructive confirmation.
    pending = context.user_data.get("ai_pending_delete")
    if pending:
        if low in {"confirm", "yes confirm", "confirm delete", "yes delete"}:
            row = bot.campaign_link_by_code(pending)
            context.user_data.pop("ai_pending_delete", None)
            if not row:
                await msg.reply_text("❌ That campaign link no longer exists.")
                return
            deleted = bot.delete_campaign_link(row["agent_code"])
            await msg.reply_text(
                "🗑 <b>Campaign link deleted</b>\n\n"
                f"Old URL: <code>{bot.PUBLIC_BASE_URL}/{html.escape(str(deleted['slug']))}</code>",
                parse_mode=bot.ParseMode.HTML,
                reply_markup=bot.campaign_menu(),
            )
            return
        if low in {"cancel", "no", "cancel delete"}:
            context.user_data.pop("ai_pending_delete", None)
            await msg.reply_text("✅ Delete cancelled.", reply_markup=bot.campaign_menu())
            return

    # Run Smart Checker.
    if ("smart" in low and "check" in low and any(x in low for x in ("run", "start", "do"))) or low in {"run checker", "start checker"}:
        control = bot.request_local_verifier_run()
        await msg.reply_text(
            "🟢 <b>Smart Check requested.</b>\n\n"
            "The browser checker will run first and Apify will be used only as fallback for unresolved fields.",
            parse_mode=bot.ParseMode.HTML,
            reply_markup=bot.campaign_menu(),
        )
        return

    # Today's campaign report.
    if ("today" in low and "report" in low) or low in {"today report", "show today", "campaign today"}:
        s = bot.tracker_today_totals()
        await msg.reply_text(
            "📅 <b>Today's Campaign</b>\n\n"
            f"Landing visits: <b>{int(s['landing_visits'] or 0)}</b>\n"
            f"Unique visitors: <b>{int(s['unique_visitors'] or 0)}</b>\n"
            f"BetroxyBot clicks: <b>{int(s['telegram_clicks'] or 0)}</b>\n"
            f"Website clicks: <b>{int(s['website_clicks'] or 0)}</b>\n"
            f"Bot starts: <b>{int(s['starts'] or 0)}</b>\n"
            f"Registrations: <b>{int(s['registrations'] or 0)}</b>\n"
            f"Deposits: <b>{int(s['deposits'] or 0)}</b>\n"
            f"Deposit amount: <b>{float(s['deposit_amount'] or 0):,.2f}</b>",
            parse_mode=bot.ParseMode.HTML,
            reply_markup=bot.campaign_menu(),
        )
        return

    # Create creator/campaign link from a supplied Instagram/Telegram URL.
    if any(k in low for k in ("create link", "create campaign", "add creator", "add link", "make link")):
        m = re.search(r"https?://[^\s]+", raw, flags=re.I)
        if not m:
            await msg.reply_text("Send the Instagram or Telegram profile/channel URL in the same message.")
            return
        try:
            row, created = bot.create_campaign_creator(m.group(0))
            landing, _tg = bot.creator_urls(row)
            await msg.reply_text(
                ("✅ <b>Created</b>" if created else "ℹ️ <b>Already existed</b>") +
                f"\n\nLanding: <code>{html.escape(landing)}</code>\n"
                f"Code: <code>{html.escape(str(row['agent_code']))}</code>",
                parse_mode=bot.ParseMode.HTML,
                reply_markup=bot.campaign_creator_menu(row["agent_code"]),
                disable_web_page_preview=True,
            )
        except Exception as exc:
            await msg.reply_text(f"❌ {html.escape(str(exc))}", parse_mode=bot.ParseMode.HTML)
        return

    row = _find_target_in_text(raw)

    # Change WhatsApp number.
    if "whatsapp" in low and any(k in low for k in ("set", "change", "update", "number", "no.")):
        phone = _phone_from_text(raw)
        if not row:
            await msg.reply_text("❌ I couldn't identify which Batraxy link you mean. Include the slug, for example <code>meta-ch</code>.", parse_mode=bot.ParseMode.HTML)
            return
        if not phone:
            await msg.reply_text("❌ I couldn't find a valid WhatsApp number. Include the country code, e.g. <code>+16084301011</code>.", parse_mode=bot.ParseMode.HTML)
            return
        updated = checkout._save_checkout_number(row["agent_code"], phone)
        await msg.reply_text(
            f"✅ WhatsApp number for <code>{updated['slug']}</code> set to <code>+{phone}</code>.",
            parse_mode=bot.ParseMode.HTML,
            reply_markup=checkout._checkout_settings_markup(updated),
        )
        return

    # Enable/disable checkout.
    if "checkout" in low and any(k in low for k in ("enable", "turn on", "switch on")):
        if not row:
            await msg.reply_text("❌ I couldn't identify the campaign link.")
            return
        updated = checkout._set_checkout_enabled(row["agent_code"], True)
        await msg.reply_text(
            f"✅ Checkout enabled for <code>{updated['slug']}</code>.",
            parse_mode=bot.ParseMode.HTML,
            reply_markup=checkout._checkout_settings_markup(updated),
        )
        return

    if "checkout" in low and any(k in low for k in ("disable", "turn off", "switch off")):
        if not row:
            await msg.reply_text("❌ I couldn't identify the campaign link.")
            return
        updated = checkout._set_checkout_enabled(row["agent_code"], False)
        await msg.reply_text(
            f"⛔ Checkout disabled for <code>{updated['slug']}</code>.",
            parse_mode=bot.ParseMode.HTML,
            reply_markup=checkout._checkout_settings_markup(updated),
        )
        return

    # Change pre-filled WhatsApp message.
    if any(k in low for k in ("message", "prefill", "pre-filled", "pre filled")) and any(k in low for k in ("change", "set", "update")):
        if not row:
            await msg.reply_text("❌ I couldn't identify the campaign link.")
            return
        m = re.search(r"(?:message|prefill(?:ed)?(?: message)?)\s+(?:to\s+)?(.+)$", raw, flags=re.I)
        if not m:
            m = re.search(r"\bto\b\s+(.+)$", raw, flags=re.I)
        new_message = (m.group(1).strip() if m else "")
        if not new_message or len(new_message) > 500:
            await msg.reply_text("❌ Send the new message in the same instruction (maximum 500 characters).")
            return
        updated = checkout._save_checkout_message(row["agent_code"], new_message)
        await msg.reply_text(
            f"✅ WhatsApp message updated for <code>{updated['slug']}</code>.\n\n"
            f"<code>{html.escape(new_message)}</code>",
            parse_mode=bot.ParseMode.HTML,
            reply_markup=checkout._checkout_settings_markup(updated),
        )
        return

    # Change traffic source.
    if "source" in low and any(k in low for k in ("change", "set", "update")):
        if not row:
            await msg.reply_text("❌ I couldn't identify the campaign link.")
            return
        source = _source_from_text(raw)
        if not source:
            await msg.reply_text("Choose one of: Instagram, Telegram, Meta Ads, or Google Ads.")
            return
        updated = bot.update_campaign_source(row["agent_code"], source)
        label = bot.CAMPAIGN_SOURCE_LABELS.get(updated["source_type"], updated["source_type"])
        await msg.reply_text(
            f"✅ Source for <code>{updated['slug']}</code> changed to <b>{html.escape(label)}</b>.",
            parse_mode=bot.ParseMode.HTML,
            reply_markup=bot.campaign_creator_menu(updated["agent_code"]),
        )
        return

    # Show checkout settings / open checkout.
    if row and "checkout" in low and any(k in low for k in ("show", "settings", "status", "open", "preview")):
        await _send_row_settings(msg, row)
        return

    # Delete with required confirmation.
    if any(k in low for k in ("delete", "remove permanently")):
        if not row:
            await msg.reply_text("❌ I couldn't identify which campaign link you want to delete.")
            return
        context.user_data["ai_pending_delete"] = row["agent_code"]
        await msg.reply_text(
            "⚠️ <b>Confirm permanent delete</b>\n\n"
            f"Campaign: <code>{bot.PUBLIC_BASE_URL}/{html.escape(str(row['slug']))}</code>\n\n"
            "Reply <code>confirm</code> to delete it, or <code>cancel</code>.",
            parse_mode=bot.ParseMode.HTML,
        )
        return

    # Friendly fallback for admin: explain AI mode instead of forcing button clicks.
    await msg.reply_text(
        "🤖 I can control most BETROXY admin tasks from normal text.\n\n"
        "Try something like:\n"
        "<code>Set meta-ch WhatsApp to +16084301011</code>\n"
        "<code>Enable checkout on meta-ch</code>\n"
        "<code>Run smart checker</code>\n\n"
        "Type <code>AI help</code> to see more examples.",
        parse_mode=bot.ParseMode.HTML,
        reply_markup=bot.admin_menu(),
    )


bot.chat_handler = ai_admin_chat_handler
bot.logger.warning("V62_AI_ADMIN_ASSISTANT active=on admin_only=on delete_confirmation=on")

if __name__ == "__main__":
    bot.main()
