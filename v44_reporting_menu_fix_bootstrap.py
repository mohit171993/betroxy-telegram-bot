import bot
import v42_reporting_center_bootstrap as v42
import v43_latest_reports_bootstrap as v43

# V42 owns the existing traffic/checkout/creator report callbacks. They resolve
# these menu helpers at runtime, so point them at the V43 date/latest-report UI
# to keep the Reports Center consistent after every report is opened.
v42.reports_center_keyboard = v43.reports_center_keyboard
v42.compliance_days_keyboard = v43.compliance_dates_keyboard

# Preserve the V43 top-level campaign/report callbacks and menu.
bot.campaign_menu = v43.v43_campaign_menu
bot.callback_handler = v43.v43_callback_handler

bot.logger.warning(
    'V44_REPORTING_MENU_ALIGNED v43_latest_reports=on report_return_menu=v43 '
    'compliance_buttons=calendar_dates'
)

if __name__ == '__main__':
    bot.main()
