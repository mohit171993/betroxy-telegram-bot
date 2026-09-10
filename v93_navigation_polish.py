import threading
import time

import bot
import v92_ai_report_router_hotfix as v92

v91 = v92.v91
v89 = v92.v89
v88 = v92.v88
v85 = v92.v85
v83 = v92.v83

_old_v91_callback = bot.callback_handler


def v93_admin_menu():
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
        [bot.InlineKeyboardButton("⬅️ Customer Menu", callback_data="home")],
    ])


def v93_category_markup(category):
    if category == "customers":
        return bot.InlineKeyboardMarkup([
            [bot.InlineKeyboardButton("💬 Telegram Business Inbox", callback_data="business_home")],
            [bot.InlineKeyboardButton("⬅️ Admin Home", callback_data="admin_home")],
        ])
    if category == "campaigns":
        return bot.InlineKeyboardMarkup([
            [bot.InlineKeyboardButton("📈 Campaign Control & Tracking", callback_data="campaign_home")],
            [bot.InlineKeyboardButton("⬅️ Admin Home", callback_data="admin_home")],
        ])
    if category == "rewards":
        return bot.InlineKeyboardMarkup([
            [bot.InlineKeyboardButton("🎁 Reward Center", callback_data="v89_reward_center")],
            [bot.InlineKeyboardButton("🤖 Autopilot Control", callback_data="autopilot_home")],
            [bot.InlineKeyboardButton("📊 Engagement & Reward Reports", callback_data="v91_reports:engage")],
            [bot.InlineKeyboardButton("⬅️ Admin Home", callback_data="admin_home")],
        ])
    if category == "affiliates":
        rows = []
        for b in v91._legacy_admin_buttons():
            if v91._button_category(b) == "affiliates":
                rows.append([b])
        rows.append([bot.InlineKeyboardButton("⬅️ Admin Home", callback_data="admin_home")])
        return bot.InlineKeyboardMarkup(rows)
    return bot.InlineKeyboardMarkup([
        [bot.InlineKeyboardButton("⬅️ Admin Home", callback_data="admin_home")]
    ])


def v93_autopilot_keyboard():
    s = v83._settings()
    label = lambda title, key: f"{title}: {'ON ✅' if s.get(key) else 'OFF'}"
    return bot.InlineKeyboardMarkup([
        [bot.InlineKeyboardButton(label("🤖 Master", "master_enabled"), callback_data="autopilot_toggle:master_enabled")],
        [
            bot.InlineKeyboardButton(label("⏰ Reminders", "reminders_enabled"), callback_data="autopilot_toggle:reminders_enabled"),
            bot.InlineKeyboardButton(label("🏏 Sports", "sports_enabled"), callback_data="autopilot_toggle:sports_enabled"),
        ],
        [
            bot.InlineKeyboardButton(label("🎁 Promotions", "promotions_enabled"), callback_data="autopilot_toggle:promotions_enabled"),
            bot.InlineKeyboardButton(label("🏆 Quiz", "quiz_enabled"), callback_data="autopilot_toggle:quiz_enabled"),
        ],
        [bot.InlineKeyboardButton(label("♻️ Reactivation", "reactivation_enabled"), callback_data="autopilot_toggle:reactivation_enabled")],
        [bot.InlineKeyboardButton("▶️ Run Safe Scan Now", callback_data="autopilot_run")],
        [bot.InlineKeyboardButton("⬅️ Rewards & Engagement", callback_data="v91_admincat:rewards")],
    ])


async def v93_callback_handler(update, context):
    q = update.callback_query
    data = str(q.data or "") if q else ""
    if q and bot.is_admin(q.from_user.id) and data in {"reports_home", "intel_home"}:
        await q.answer()
        try:
            await q.message.edit_text(
                "📊 <b>BETROXY REPORTING CENTER</b>\n\nReports are grouped by purpose. View a report instantly or download PDF/CSV where available.",
                parse_mode=bot.ParseMode.HTML,
                reply_markup=v91.reports_home_markup(),
                disable_web_page_preview=True,
            )
        except Exception:
            await q.message.reply_text(
                "📊 <b>BETROXY REPORTING CENTER</b>\n\nReports are grouped by purpose. View a report instantly or download PDF/CSV where available.",
                parse_mode=bot.ParseMode.HTML,
                reply_markup=v91.reports_home_markup(),
                disable_web_page_preview=True,
            )
        return
    return await _old_v91_callback(update, context)


def _startup_pdf_diagnostic():
    try:
        p1 = v91._text_pdf("overview")
        p2 = v91._url_pdf("url_today")
        bot.logger.warning(
            "V93_PDF_DIAGNOSTIC overview_pdf=ok:%s url_pdf=ok:%s",
            len(p1.getvalue()), len(p2.getvalue()),
        )
    except Exception:
        bot.logger.exception("V93_PDF_DIAGNOSTIC_FAILED")


bot.admin_menu = v93_admin_menu
v91.v91_admin_menu = v93_admin_menu
v91._category_markup = v93_category_markup
v83._autopilot_keyboard = v93_autopilot_keyboard
bot.callback_handler = v93_callback_handler

bot.logger.warning(
    "V93_NAVIGATION_POLISH active=on admin_categories=5 empty_system_category=removed "
    "autopilot_under_rewards=on report_back_routes=unified mobile_report_under_users=on"
)


if __name__ == "__main__":
    _startup_pdf_diagnostic()
    v88.v63.apply_signup_cta()
    v85._enable_smart_reply_without_reset()
    threading.Thread(target=v83._worker_loop, name="betroxy-engagement-worker", daemon=True).start()
    bot.logger.warning("V93 polling handover delay=12s")
    time.sleep(12)
    bot.main()
