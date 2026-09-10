import threading
import time

import bot
import v93_navigation_polish as v93

v92 = v93.v92
v91 = v93.v91
v89 = v93.v89
v88 = v93.v88
v85 = v93.v85
v83 = v93.v83

_old_start = bot.start
_old_callback_handler = bot.callback_handler
_old_public_menu = bot.public_menu


def _admin_home_text():
    return (
        "🛠 <b>BETROXY ADMIN CONTROL CENTER</b>\n\n"
        "Everything is now grouped by purpose so you do not have to scroll through customer functions.\n\n"
        "Choose one section below."
    )


def _clean_customer_menu():
    """Build a customer-only view even when opened by the admin account."""
    try:
        markup = _old_public_menu(None)
    except TypeError:
        markup = _old_public_menu()
    rows = []
    for row in getattr(markup, "inline_keyboard", []) or []:
        kept = []
        for b in row:
            text = str(getattr(b, "text", "") or "").lower()
            cb = str(getattr(b, "callback_data", "") or "").lower()
            # Admin/reporting entry points belong in Admin Control Center only.
            if any(k in text for k in ("bot analytics", "admin panel", "reporting center", "intelligence")):
                continue
            if cb in {"admin_home", "analytics_home", "intel_home", "v91_reports_home"}:
                continue
            kept.append(b)
        if kept:
            rows.append(kept)
    rows.append([bot.InlineKeyboardButton("🛠 Back to Admin Control Center", callback_data="v94_admin_home")])
    return bot.InlineKeyboardMarkup(rows)


def v94_public_menu(user_id=None):
    if user_id is not None and bot.is_admin(user_id):
        return _clean_customer_menu()
    return _old_public_menu(user_id)


def v94_admin_menu():
    return bot.InlineKeyboardMarkup([
        [bot.InlineKeyboardButton("📊 Reporting Center", callback_data="v91_reports_home")],
        [
            bot.InlineKeyboardButton("💬 Customers & Business", callback_data="v91_admincat:customers"),
            bot.InlineKeyboardButton("📣 Campaigns & Tracking", callback_data="v91_admincat:campaigns"),
        ],
        [
            bot.InlineKeyboardButton("🎁 Rewards & Engagement", callback_data="v91_admincat:rewards"),
            bot.InlineKeyboardButton("👥 Affiliates & Referrals", callback_data="v91_admincat:affiliates"),
        ],
        [bot.InlineKeyboardButton("👤 Customer View", callback_data="v94_customer_view")],
    ])


async def v94_start(update, context):
    user = update.effective_user
    msg = update.effective_message
    args = list(getattr(context, "args", []) or [])
    payload = str(args[0]).lower().strip() if args else ""

    # Normal /start on the admin account opens the clean admin dashboard directly.
    # Deep links (freequiz/mobile/etc.) still execute their existing customer flows.
    if user and msg and bot.is_admin(user.id) and not payload:
        await msg.reply_text(
            _admin_home_text(),
            parse_mode=bot.ParseMode.HTML,
            reply_markup=v94_admin_menu(),
        )
        bot.logger.warning("V94_ADMIN_START uid=%s mode=admin_dashboard", user.id)
        return
    return await _old_start(update, context)


async def v94_callback_handler(update, context):
    q = update.callback_query
    data = str(q.data or "") if q else ""
    if not q:
        return await _old_callback_handler(update, context)

    if bot.is_admin(q.from_user.id) and data in {"v94_admin_home", "admin_home"}:
        await q.answer()
        try:
            await q.message.edit_text(
                _admin_home_text(),
                parse_mode=bot.ParseMode.HTML,
                reply_markup=v94_admin_menu(),
                disable_web_page_preview=True,
            )
        except Exception:
            await q.message.reply_text(
                _admin_home_text(),
                parse_mode=bot.ParseMode.HTML,
                reply_markup=v94_admin_menu(),
            )
        return

    if bot.is_admin(q.from_user.id) and data in {"v94_customer_view", "home"}:
        await q.answer()
        text = (
            "👤 <b>CUSTOMER VIEW</b>\n\n"
            "This is exactly the customer-facing BETROXY menu. Admin-only tools are hidden here."
        )
        try:
            await q.message.edit_text(
                text,
                parse_mode=bot.ParseMode.HTML,
                reply_markup=_clean_customer_menu(),
                disable_web_page_preview=True,
            )
        except Exception:
            await q.message.reply_text(
                text,
                parse_mode=bot.ParseMode.HTML,
                reply_markup=_clean_customer_menu(),
            )
        return

    return await _old_callback_handler(update, context)


bot.public_menu = v94_public_menu
bot.admin_menu = v94_admin_menu
v91.v91_admin_menu = v94_admin_menu
bot.start = v94_start
bot.callback_handler = v94_callback_handler

bot.logger.warning(
    "V94_ADMIN_MODE_CLEANUP active=on admin_start=category_dashboard "
    "customer_view=separate bot_analytics_direct=hidden admin_tools_from_customer_view=hidden"
)


if __name__ == "__main__":
    v93._startup_pdf_diagnostic()
    v88.v63.apply_signup_cta()
    v85._enable_smart_reply_without_reset()
    threading.Thread(target=v83._worker_loop, name="betroxy-engagement-worker", daemon=True).start()
    bot.logger.warning("V94 polling handover delay=12s")
    time.sleep(12)
    bot.main()
