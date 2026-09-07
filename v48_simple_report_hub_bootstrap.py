from datetime import datetime, timezone

import bot
import v42_reporting_center_bootstrap as v42
import v43_latest_reports_bootstrap as v43
import v45_smartcheck_history_bootstrap as v45
import v47_pixel_manager_menu_bootstrap as v47


# ============================================================
# V48 - SIMPLE CAMPAIGN CONTROL + TWO SEPARATE REPORT AREAS
# ============================================================
# Instagram compliance/status and landing-page traffic are intentionally kept
# in separate menus so the admin never has to guess which report means what.

_previous_callback_handler = bot.callback_handler


def _active_creator_count():
    try:
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM campaign_links WHERE is_active=TRUE")
                return int((cur.fetchone() or {}).get('n') or 0)
    except Exception:
        return 0


def _active_instagram_count():
    try:
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*) AS n
                    FROM campaign_links
                    WHERE is_active=TRUE
                      AND LOWER(COALESCE(source_type,'instagram'))='instagram'
                    """
                )
                return int((cur.fetchone() or {}).get('n') or 0)
    except Exception:
        return 0


def _last_check_text():
    try:
        row = v45._latest_run_control() or {}
        completed = row.get('completed_at')
        if completed:
            return completed.strftime('%d %b %Y %H:%M UTC')
    except Exception:
        pass
    return 'Not run yet'


def v48_campaign_overview_text():
    return (
        "📣 <b>BETROXY CAMPAIGN CONTROL</b>\n\n"
        "This dashboard is split into two separate areas:\n\n"
        "1️⃣ <b>Instagram Checker</b> — Bio link, extra link, Story and Story-link compliance.\n"
        "2️⃣ <b>Landing Page Performance</b> — visits, unique visitors, checkout/website clicks and conversions.\n\n"
        f"Active creator URLs: <b>{_active_creator_count()}</b>\n"
        f"Active Instagram creators: <b>{_active_instagram_count()}</b>\n"
        f"Latest Instagram Smart Check: <b>{_last_check_text()}</b>\n\n"
        "Choose the task you want below."
    )


def v48_campaign_menu():
    return bot.InlineKeyboardMarkup([
        [
            bot.InlineKeyboardButton('☁️ Run Instagram Check', callback_data='verify_request_local_run'),
            bot.InlineKeyboardButton('✅ Latest Check PDF', callback_data='reports_latest_final'),
        ],
        [
            bot.InlineKeyboardButton('📋 INSTAGRAM CHECK REPORTS', callback_data='reports_instagram'),
        ],
        [
            bot.InlineKeyboardButton('📈 LANDING PAGE REPORTS', callback_data='reports_landing'),
        ],
        [
            bot.InlineKeyboardButton('🔗 Creator Links', callback_data='campaign_links'),
            bot.InlineKeyboardButton('➕ Add Creator', callback_data='campaign_add_single'),
        ],
        [
            bot.InlineKeyboardButton('🎯 Pixel Manager', callback_data='campaign_pixel_manager'),
            bot.InlineKeyboardButton('🎨 Landing Design', callback_data='theme_home'),
        ],
        [
            bot.InlineKeyboardButton('⚙️ Campaign Tools', callback_data='campaign_tools'),
            bot.InlineKeyboardButton('🔄 Refresh', callback_data='campaign_home'),
        ],
        [bot.InlineKeyboardButton('⬅️ Admin Panel', callback_data='admin_home')],
    ])


def reports_hub_keyboard():
    return bot.InlineKeyboardMarkup([
        [bot.InlineKeyboardButton('📋 Instagram Check Reports', callback_data='reports_instagram')],
        [bot.InlineKeyboardButton('📈 Landing Page Reports', callback_data='reports_landing')],
        [bot.InlineKeyboardButton('⬅️ Campaign Tracker', callback_data='campaign_home')],
    ])


def instagram_reports_keyboard():
    return bot.InlineKeyboardMarkup([
        [
            bot.InlineKeyboardButton('✅ Latest Full Check PDF', callback_data='reports_latest_final'),
            bot.InlineKeyboardButton('🔴 Negative Only PDF', callback_data='reports_latest_negative'),
        ],
        [
            bot.InlineKeyboardButton('🗓 Status History by Date', callback_data='reports_status_history'),
            bot.InlineKeyboardButton('📅 Compliance by Date', callback_data='reports_compliance_days'),
        ],
        [
            bot.InlineKeyboardButton('☁️ Apify Usage / Balance', callback_data='verify_hybrid_status'),
            bot.InlineKeyboardButton('✍️ Manual Verification', callback_data='verify_home'),
        ],
        [bot.InlineKeyboardButton('⬅️ Report Categories', callback_data='reports_home')],
        [bot.InlineKeyboardButton('🏠 Campaign Tracker', callback_data='campaign_home')],
    ])


def landing_reports_keyboard():
    return bot.InlineKeyboardMarkup([
        [
            bot.InlineKeyboardButton('📅 Today Performance', callback_data='reports_today'),
            bot.InlineKeyboardButton('📈 Day-wise Landing Visits', callback_data='reports_traffic'),
        ],
        [
            bot.InlineKeyboardButton('🎯 Checkout / Website Clicks', callback_data='reports_checkout'),
            bot.InlineKeyboardButton('👥 Creator-wise Performance', callback_data='reports_creators'),
        ],
        [
            bot.InlineKeyboardButton('📊 Overall Landing Summary', callback_data='reports_campaign'),
            bot.InlineKeyboardButton('🏆 Top Landing Pages', callback_data='reports_top'),
        ],
        [bot.InlineKeyboardButton('📥 Export Campaign CSV', callback_data='campaign_export')],
        [bot.InlineKeyboardButton('⬅️ Report Categories', callback_data='reports_home')],
        [bot.InlineKeyboardButton('🏠 Campaign Tracker', callback_data='campaign_home')],
    ])


def campaign_tools_keyboard():
    return bot.InlineKeyboardMarkup([
        [
            bot.InlineKeyboardButton('📚 Bulk Create Creators', callback_data='campaign_add_bulk'),
            bot.InlineKeyboardButton('✅ Sync Final Links', callback_data='campaign_sync_final'),
        ],
        [
            bot.InlineKeyboardButton('⛔ Disable Link', callback_data='campaign_disable_by_link'),
            bot.InlineKeyboardButton('🗑 Delete Link', callback_data='campaign_delete_by_link'),
        ],
        [
            bot.InlineKeyboardButton('🎯 Pixel Manager', callback_data='campaign_pixel_manager'),
            bot.InlineKeyboardButton('🎨 Landing Design', callback_data='theme_home'),
        ],
        [bot.InlineKeyboardButton('⬅️ Campaign Tracker', callback_data='campaign_home')],
    ])


async def v48_callback_handler(update, context):
    q = update.callback_query
    data = (q.data or '') if q else ''

    if data == 'reports_home':
        await q.answer()
        if not bot.is_admin(q.from_user.id):
            return
        await q.message.reply_text(
            '📊 <b>REPORT CATEGORIES</b>\n\n'
            'These are now completely separate:\n\n'
            '📋 <b>Instagram Check Reports</b> = whether promoters actually kept the required bio/story promotion live.\n\n'
            '📈 <b>Landing Page Reports</b> = what visitors did on your Batraxy landing URLs: visits, clicks and conversions.\n\n'
            'Choose one category:',
            parse_mode=bot.ParseMode.HTML,
            reply_markup=reports_hub_keyboard(),
        )
        return

    if data == 'reports_instagram':
        await q.answer()
        if not bot.is_admin(q.from_user.id):
            return
        await q.message.reply_text(
            '📋 <b>INSTAGRAM CHECK REPORTS</b>\n\n'
            'Only Instagram promotion/compliance is shown here.\n'
            'No landing-page visit or click statistics are mixed into this section.\n\n'
            '• Latest Full Check = complete latest Smart Check PDF\n'
            '• Negative Only = promoter-ready PDF containing only failed items\n'
            '• Status History = previous checks grouped by actual calendar date\n'
            '• Compliance by Date = campaign date PDFs',
            parse_mode=bot.ParseMode.HTML,
            reply_markup=instagram_reports_keyboard(),
        )
        return

    if data == 'reports_landing':
        await q.answer()
        if not bot.is_admin(q.from_user.id):
            return
        await q.message.reply_text(
            '📈 <b>LANDING PAGE PERFORMANCE REPORTS</b>\n\n'
            'Only Batraxy landing-page performance is shown here.\n'
            'Instagram bio/story checker results are not part of these reports.\n\n'
            'Landing visit = someone opened the creator Batraxy URL.\n'
            'Checkout / Website click = visitor clicked through from Batraxy to Betroxy.com.\n\n'
            '<b>Disabled and deleted links remain excluded from report counts.</b>',
            parse_mode=bot.ParseMode.HTML,
            reply_markup=landing_reports_keyboard(),
        )
        return

    if data == 'campaign_tools':
        await q.answer()
        if not bot.is_admin(q.from_user.id):
            return
        await q.message.reply_text(
            '⚙️ <b>CAMPAIGN TOOLS</b>\n\n'
            'Less-used setup and maintenance options are kept here so the main dashboard stays simple.',
            parse_mode=bot.ParseMode.HTML,
            reply_markup=campaign_tools_keyboard(),
        )
        return

    return await _previous_callback_handler(update, context)


# Replace the crowded mixed reporting dashboard with the simplified structure.
bot.campaign_menu = v48_campaign_menu
bot.campaign_overview_text = v48_campaign_overview_text
bot.callback_handler = v48_callback_handler

# Existing V42/V43 report functions resolve these menu helpers at runtime.
# Point them to the clean two-category hub so an opened report never returns the
# admin to the old mixed report menu.
v43.reports_center_keyboard = reports_hub_keyboard
v42.reports_center_keyboard = reports_hub_keyboard

bot.logger.warning(
    'V48_SIMPLE_REPORT_HUB_ACTIVE instagram_reports=separate landing_reports=separate main_menu=simplified'
)


if __name__ == '__main__':
    bot.main()
