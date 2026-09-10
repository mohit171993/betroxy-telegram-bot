import threading
import time

import bot
import v89_rewards_mobile_ai as v89

v88 = v89.v88
v85 = v89.v85
v83 = v89.v83
v82 = v83.v82

_old_intelligence_menu = v82.intelligence_menu


def v90_admin_menu():
    """Keep Reward Center in Admin Panel; move mobile report into Reporting."""
    markup = v89._old_admin_menu()
    rows = [list(r) for r in markup.inline_keyboard]
    if not any(any(getattr(b, "callback_data", None) == "v89_reward_center" for b in row) for row in rows):
        rows.insert(1, [bot.InlineKeyboardButton("🎁 Reward Center", callback_data="v89_reward_center")])
    # Intentionally do not add v89_mobile_report here.
    return bot.InlineKeyboardMarkup(rows)


def v90_intelligence_menu():
    markup = _old_intelligence_menu()
    rows = [list(r) for r in markup.inline_keyboard]
    if not any(any(getattr(b, "callback_data", None) == "v89_mobile_report" for b in row) for row in rows):
        # Put the contact/mobile export alongside lead/user reporting.
        insert_at = 2 if len(rows) >= 2 else 0
        rows.insert(insert_at, [
            bot.InlineKeyboardButton("📱 User Mobile Report (PDF)", callback_data="v89_mobile_report")
        ])
    return bot.InlineKeyboardMarkup(rows)


bot.admin_menu = v90_admin_menu
v82.intelligence_menu = v90_intelligence_menu

bot.logger.warning(
    "V90_REPORTING_MENU_CLEANUP active=on mobile_report_location=reporting_intelligence admin_direct_mobile_button=off"
)


if __name__ == "__main__":
    v88.v63.apply_signup_cta()
    v85._enable_smart_reply_without_reset()
    threading.Thread(target=v83._worker_loop, name="betroxy-engagement-worker", daemon=True).start()
    bot.logger.warning("V90 polling handover delay=12s")
    time.sleep(12)
    bot.main()
