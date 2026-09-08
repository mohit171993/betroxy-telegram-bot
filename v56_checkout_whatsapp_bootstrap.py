import html
import re
from urllib.parse import urlencode

import bot
import v55_force_public_start_bootstrap as v55


# ============================================================
# V56 - PER-LINK CHECKOUT + WHATSAPP DESTINATION
# ============================================================
# Adds optional checkout pages to individual Batraxy campaign URLs.
# Each campaign can have its own WhatsApp number and pre-filled message.
# /meta-ch is enabled automatically as the first checkout link.
# ============================================================

DEFAULT_CHECKOUT_NUMBER = "447777352382"
DEFAULT_CHECKOUT_MESSAGE = "Hi, I am interested in Betroxy."


_original_init_db = bot.init_db


def v56_init_db():
    _original_init_db()
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                ALTER TABLE campaign_links
                ADD COLUMN IF NOT EXISTS checkout_enabled BOOLEAN DEFAULT FALSE
                """
            )
            cur.execute(
                """
                ALTER TABLE campaign_links
                ADD COLUMN IF NOT EXISTS whatsapp_number TEXT
                """
            )
            cur.execute(
                """
                ALTER TABLE campaign_links
                ADD COLUMN IF NOT EXISTS whatsapp_message TEXT
                """
            )

            # Make /meta-ch available immediately without disturbing an existing row.
            cur.execute(
                "SELECT id FROM campaign_links WHERE LOWER(slug)='meta-ch' LIMIT 1"
            )
            existing = cur.fetchone()
            if not existing:
                base_code = "meta_ch"
                code = base_code
                n = 2
                while True:
                    cur.execute(
                        "SELECT 1 FROM campaign_links WHERE LOWER(agent_code)=LOWER(%s) LIMIT 1",
                        (code,),
                    )
                    if not cur.fetchone():
                        break
                    code = f"{base_code}{n}"
                    n += 1
                cur.execute(
                    """
                    INSERT INTO campaign_links
                        (instagram_username, slug, agent_code, is_active, source_type,
                         checkout_enabled, whatsapp_number, whatsapp_message)
                    VALUES (%s,%s,%s,TRUE,'meta_ads',TRUE,%s,%s)
                    """,
                    ("meta-ch", "meta-ch", code, DEFAULT_CHECKOUT_NUMBER, DEFAULT_CHECKOUT_MESSAGE),
                )
            else:
                cur.execute(
                    """
                    UPDATE campaign_links
                    SET checkout_enabled=TRUE,
                        whatsapp_number=COALESCE(NULLIF(TRIM(whatsapp_number),''), %s),
                        whatsapp_message=COALESCE(NULLIF(TRIM(whatsapp_message),''), %s),
                        is_active=TRUE
                    WHERE LOWER(slug)='meta-ch'
                    """,
                    (DEFAULT_CHECKOUT_NUMBER, DEFAULT_CHECKOUT_MESSAGE),
                )
        conn.commit()


bot.init_db = v56_init_db


def _clean_phone(value):
    return re.sub(r"\D+", "", str(value or ""))


def _checkout_url(row):
    number = _clean_phone(row.get("whatsapp_number") or DEFAULT_CHECKOUT_NUMBER)
    message = str(row.get("whatsapp_message") or DEFAULT_CHECKOUT_MESSAGE).strip()
    params = {
        "phone": number,
        "text": message,
        "type": "phone_number",
        "app_absent": "0",
    }
    return "https://api.whatsapp.com/send/?" + urlencode(params)


def _record_whatsapp_click(slug, row):
    fingerprint, _ = bot._visitor_fingerprint()
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM outbound_events
                WHERE slug=%s AND visitor_hash=%s AND destination='whatsapp'
                  AND created_at >= NOW() - INTERVAL '5 seconds'
                LIMIT 1
                """,
                (slug, fingerprint),
            )
            if not cur.fetchone():
                cur.execute(
                    """
                    INSERT INTO outbound_events
                        (slug, agent_code, destination, visitor_hash)
                    VALUES (%s,%s,'whatsapp',%s)
                    """,
                    (slug, row["agent_code"], fingerprint),
                )
        conn.commit()


def _checkout_html(row):
    slug = str(row["slug"])
    message = html.escape(str(row.get("whatsapp_message") or DEFAULT_CHECKOUT_MESSAGE))
    whatsapp_go = f"{bot.PUBLIC_BASE_URL}/go/{html.escape(slug)}/whatsapp"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#111827">
<title>BETROXY | WhatsApp</title>
<style>
*{{box-sizing:border-box}}
html,body{{margin:0;min-height:100%;font-family:Arial,Helvetica,sans-serif;background:#f6f7f8;color:#1f2937}}
body{{min-height:100vh}}
.top{{height:78px;background:#fff;border-bottom:1px solid #e5e7eb;display:flex;align-items:center;justify-content:center;position:relative}}
.menu{{position:absolute;left:22px;font-size:36px;line-height:1;color:#374151}}
.wa-brand{{display:flex;align-items:center;gap:9px;font-size:28px;font-weight:800;color:#25d366}}
.wa-icon{{width:38px;height:38px;display:block}}
.wrap{{width:min(100%,680px);margin:0 auto;padding:54px 22px 64px;text-align:center}}
.logo-circle{{width:210px;height:210px;border-radius:50%;margin:0 auto;display:flex;align-items:center;justify-content:center;background:#090d1d;box-shadow:0 16px 45px rgba(17,24,39,.13)}}
.logo{{font-size:38px;font-weight:950;letter-spacing:-2px;color:#fff;font-style:italic}}
.logo span{{color:#d9ff27}}
.name{{font-size:30px;margin:34px 0 0;font-weight:500;color:#30343b}}
.btn{{display:flex;align-items:center;justify-content:center;width:min(100%,365px);min-height:70px;margin:54px auto 0;padding:16px 26px;border-radius:40px;background:#159b8d;color:#fff;text-decoration:none;font-size:24px;font-weight:500;box-shadow:0 9px 24px rgba(21,155,141,.18);transition:.16s ease}}
.btn:active{{transform:scale(.985)}}
.interested{{margin-top:86px;font-size:25px;color:#4b5563}}
.message{{width:min(100%,520px);margin:28px auto 0;padding:22px 20px;border-top:1px solid #cfd4da;color:#8b949e;font-size:14px;line-height:1.55}}
.footer{{background:#101c24;color:#fff;text-align:center;padding:31px 20px 38px}}
.footer .mini{{color:#25d366;font-weight:800;font-size:18px}}
.footer h3{{margin:31px 0 0;font-size:21px}}
.legal{{margin-top:25px;color:#8aa0ad;font-size:11px}}
@media(max-width:480px){{
 .top{{height:72px}}.menu{{left:18px;font-size:33px}}.wa-brand{{font-size:25px}}.wa-icon{{width:35px;height:35px}}
 .wrap{{padding:48px 18px 58px}}.logo-circle{{width:190px;height:190px}}.logo{{font-size:34px}}
 .name{{font-size:28px}}.btn{{margin-top:48px;min-height:66px;font-size:22px}}.interested{{margin-top:76px;font-size:23px}}
}}
</style>
</head>
<body>
<header class="top">
  <div class="menu">☰</div>
  <div class="wa-brand">
    <svg class="wa-icon" viewBox="0 0 32 32" aria-hidden="true"><path fill="#25D366" d="M16 3C9.38 3 4 8.16 4 14.52c0 2.2.65 4.25 1.77 5.99L4 27l6.76-1.72A12.3 12.3 0 0 0 16 26.45c6.62 0 12-5.16 12-11.93S22.62 3 16 3Z"/><path fill="#fff" d="M22.9 18.3c-.38-.19-2.25-1.1-2.6-1.23-.35-.12-.6-.19-.86.19-.25.38-.98 1.23-1.2 1.48-.22.25-.44.28-.82.09-.38-.19-1.6-.57-3.05-1.82-1.13-.98-1.89-2.18-2.11-2.56-.22-.38-.02-.58.17-.77.17-.17.38-.44.57-.66.19-.22.25-.38.38-.63.13-.25.06-.47-.03-.66-.09-.19-.86-2.03-1.17-2.78-.31-.74-.63-.64-.86-.65h-.73c-.25 0-.66.09-1.01.47-.35.38-1.33 1.29-1.33 3.15s1.36 3.66 1.55 3.91c.19.25 2.67 4.04 6.47 5.66.9.38 1.61.61 2.16.78.91.28 1.73.24 2.38.15.73-.11 2.25-.91 2.57-1.79.32-.88.32-1.64.22-1.79-.09-.16-.34-.25-.72-.44Z"/></svg>
    WhatsApp
  </div>
</header>
<main class="wrap">
  <div class="logo-circle"><div class="logo">BETR<span>O</span>XY</div></div>
  <div class="name">Betroxy</div>
  <a class="btn" href="{whatsapp_go}">Continue to WhatsApp</a>
  <div class="interested">I am interested</div>
  <div class="message">{message}</div>
</main>
<footer class="footer">
  <div class="mini">◉ WhatsApp</div>
  <h3>Connect with Betroxy</h3>
  <div class="legal">18+ • Please play responsibly.</div>
</footer>
</body>
</html>"""


_original_dynamic_creator_landing = bot.tracker_api.view_functions.get("dynamic_creator_landing")


def v56_dynamic_creator_landing(slug):
    clean_slug = str(slug or "").strip().lower()
    row = bot.campaign_link_by_slug(clean_slug)
    if row and bool(row.get("checkout_enabled")):
        bot.record_landing_visit(clean_slug)
        return bot.Response(_checkout_html(row), mimetype="text/html")
    return _original_dynamic_creator_landing(slug)


if _original_dynamic_creator_landing:
    bot.tracker_api.view_functions["dynamic_creator_landing"] = v56_dynamic_creator_landing


_original_outbound_redirect = bot.tracker_api.view_functions.get("outbound_redirect")


def v56_outbound_redirect(slug, destination):
    clean_slug = str(slug or "").strip().lower()
    clean_destination = str(destination or "").strip().lower()
    if clean_destination == "whatsapp":
        row = bot.campaign_link_by_slug(clean_slug)
        if not row or not bool(row.get("checkout_enabled")):
            return bot.Response("Not found", status=404)
        number = _clean_phone(row.get("whatsapp_number") or DEFAULT_CHECKOUT_NUMBER)
        if not (7 <= len(number) <= 15):
            return bot.Response("WhatsApp number is not configured", status=503)
        _record_whatsapp_click(clean_slug, row)
        return bot.redirect(_checkout_url(row), code=302)
    return _original_outbound_redirect(slug, destination)


if _original_outbound_redirect:
    bot.tracker_api.view_functions["outbound_redirect"] = v56_outbound_redirect


def _save_checkout_number(code, number):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE campaign_links
                SET whatsapp_number=%s
                WHERE LOWER(agent_code)=LOWER(%s)
                RETURNING *
                """,
                (number, code),
            )
            row = cur.fetchone()
        conn.commit()
    return row


def _save_checkout_message(code, message):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE campaign_links
                SET whatsapp_message=%s
                WHERE LOWER(agent_code)=LOWER(%s)
                RETURNING *
                """,
                (message, code),
            )
            row = cur.fetchone()
        conn.commit()
    return row


def _set_checkout_enabled(code, enabled):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE campaign_links
                SET checkout_enabled=%s,
                    whatsapp_number=COALESCE(NULLIF(TRIM(whatsapp_number),''), %s),
                    whatsapp_message=COALESCE(NULLIF(TRIM(whatsapp_message),''), %s)
                WHERE LOWER(agent_code)=LOWER(%s)
                RETURNING *
                """,
                (bool(enabled), DEFAULT_CHECKOUT_NUMBER, DEFAULT_CHECKOUT_MESSAGE, code),
            )
            row = cur.fetchone()
        conn.commit()
    return row


def _checkout_settings_markup(row):
    code = row["agent_code"]
    enabled = bool(row.get("checkout_enabled"))
    toggle_text = "⛔ Disable Checkout" if enabled else "✅ Enable Checkout"
    toggle_action = "off" if enabled else "on"
    return bot.InlineKeyboardMarkup([
        [bot.InlineKeyboardButton(toggle_text, callback_data=f"checkout_toggle:{code}:{toggle_action}")],
        [
            bot.InlineKeyboardButton("📱 Change WhatsApp No.", callback_data=f"checkout_number:{code}"),
            bot.InlineKeyboardButton("💬 Change Message", callback_data=f"checkout_message:{code}"),
        ],
        [bot.InlineKeyboardButton("👁 Open Checkout Page", url=f"{bot.PUBLIC_BASE_URL}/{row['slug']}")],
        [bot.InlineKeyboardButton("⬅️ Back", callback_data=f"campaign_creator:{code}")],
    ])


def _checkout_settings_text(row):
    enabled = bool(row.get("checkout_enabled"))
    number = row.get("whatsapp_number") or DEFAULT_CHECKOUT_NUMBER
    message = row.get("whatsapp_message") or DEFAULT_CHECKOUT_MESSAGE
    return (
        "🛒 <b>Checkout / WhatsApp Settings</b>\n\n"
        f"Landing URL: <code>{bot.PUBLIC_BASE_URL}/{html.escape(str(row['slug']))}</code>\n"
        f"Checkout: <b>{'✅ ON' if enabled else '⛔ OFF'}</b>\n"
        f"WhatsApp: <code>+{html.escape(str(number))}</code>\n"
        f"Message: <code>{html.escape(str(message))}</code>\n\n"
        "These settings apply only to this individual campaign URL."
    )


_original_campaign_edit_menu = bot.campaign_edit_menu


def v56_campaign_edit_menu(code):
    markup = _original_campaign_edit_menu(code)
    rows = [list(r) for r in markup.inline_keyboard]
    rows.insert(
        max(0, len(rows) - 1),
        [bot.InlineKeyboardButton("🛒 Checkout / WhatsApp", callback_data=f"checkout_settings:{code}")],
    )
    return bot.InlineKeyboardMarkup(rows)


bot.campaign_edit_menu = v56_campaign_edit_menu


_original_campaign_creator_menu = bot.campaign_creator_menu


def v56_campaign_creator_menu(code):
    markup = _original_campaign_creator_menu(code)
    rows = [list(r) for r in markup.inline_keyboard]
    rows.insert(
        1,
        [bot.InlineKeyboardButton("🛒 Checkout / WhatsApp", callback_data=f"checkout_settings:{code}")],
    )
    return bot.InlineKeyboardMarkup(rows)


bot.campaign_creator_menu = v56_campaign_creator_menu


_original_callback_handler = bot.callback_handler


async def v56_callback_handler(update, context):
    q = update.callback_query
    data = q.data if q else ""

    if data.startswith("checkout_settings:"):
        await q.answer()
        if not bot.is_admin(q.from_user.id):
            return
        code = data.split(":", 1)[1]
        row = bot.campaign_link_by_code(code)
        if not row:
            await q.message.reply_text("❌ Campaign link not found.")
            return
        await q.message.reply_text(
            _checkout_settings_text(row),
            parse_mode=bot.ParseMode.HTML,
            reply_markup=_checkout_settings_markup(row),
            disable_web_page_preview=True,
        )
        return

    if data.startswith("checkout_toggle:"):
        await q.answer()
        if not bot.is_admin(q.from_user.id):
            return
        _, code, action = data.split(":", 2)
        row = _set_checkout_enabled(code, action == "on")
        if not row:
            await q.message.reply_text("❌ Campaign link not found.")
            return
        await q.message.reply_text(
            _checkout_settings_text(row),
            parse_mode=bot.ParseMode.HTML,
            reply_markup=_checkout_settings_markup(row),
            disable_web_page_preview=True,
        )
        return

    if data.startswith("checkout_number:"):
        await q.answer()
        if not bot.is_admin(q.from_user.id):
            return
        code = data.split(":", 1)[1]
        row = bot.campaign_link_by_code(code)
        if not row:
            await q.message.reply_text("❌ Campaign link not found.")
            return
        context.user_data["v56_checkout_number_code"] = code
        context.user_data.pop("v56_checkout_message_code", None)
        await q.message.reply_text(
            "📱 <b>Change WhatsApp Number</b>\n\n"
            f"Current: <code>+{html.escape(str(row.get('whatsapp_number') or DEFAULT_CHECKOUT_NUMBER))}</code>\n\n"
            "Send the new number with country code.\n"
            "Example: <code>971501234567</code>\n\n"
            "Do not include a leading 00.",
            parse_mode=bot.ParseMode.HTML,
        )
        return

    if data.startswith("checkout_message:"):
        await q.answer()
        if not bot.is_admin(q.from_user.id):
            return
        code = data.split(":", 1)[1]
        row = bot.campaign_link_by_code(code)
        if not row:
            await q.message.reply_text("❌ Campaign link not found.")
            return
        context.user_data["v56_checkout_message_code"] = code
        context.user_data.pop("v56_checkout_number_code", None)
        await q.message.reply_text(
            "💬 <b>Change WhatsApp Message</b>\n\n"
            f"Current: <code>{html.escape(str(row.get('whatsapp_message') or DEFAULT_CHECKOUT_MESSAGE))}</code>\n\n"
            "Send the new pre-filled WhatsApp message (max 250 characters).",
            parse_mode=bot.ParseMode.HTML,
        )
        return

    return await _original_callback_handler(update, context)


bot.callback_handler = v56_callback_handler


_original_chat_handler = bot.chat_handler


async def v56_chat_handler(update, context):
    if update.effective_user and bot.is_admin(update.effective_user.id):
        code = context.user_data.get("v56_checkout_number_code")
        if code:
            raw = (update.message.text or "").strip()
            number = _clean_phone(raw)
            if raw.startswith("00"):
                number = _clean_phone(raw[2:])
            if not (7 <= len(number) <= 15):
                await update.message.reply_text(
                    "❌ Please send a valid WhatsApp number with country code (7–15 digits)."
                )
                return
            row = _save_checkout_number(code, number)
            context.user_data.pop("v56_checkout_number_code", None)
            await update.message.reply_text(
                "✅ <b>WhatsApp number updated.</b>\n\n" + _checkout_settings_text(row),
                parse_mode=bot.ParseMode.HTML,
                reply_markup=_checkout_settings_markup(row),
                disable_web_page_preview=True,
            )
            return

        code = context.user_data.get("v56_checkout_message_code")
        if code:
            message = (update.message.text or "").strip()
            if not message:
                await update.message.reply_text("❌ Message cannot be empty.")
                return
            if len(message) > 250:
                await update.message.reply_text("❌ Message is too long. Maximum 250 characters.")
                return
            row = _save_checkout_message(code, message)
            context.user_data.pop("v56_checkout_message_code", None)
            await update.message.reply_text(
                "✅ <b>WhatsApp message updated.</b>\n\n" + _checkout_settings_text(row),
                parse_mode=bot.ParseMode.HTML,
                reply_markup=_checkout_settings_markup(row),
                disable_web_page_preview=True,
            )
            return

    return await _original_chat_handler(update, context)


bot.chat_handler = v56_chat_handler
bot.logger.warning("V56_CHECKOUT_WHATSAPP active=on meta_ch=enabled per_link_settings=enabled")


if __name__ == "__main__":
    bot.main()
