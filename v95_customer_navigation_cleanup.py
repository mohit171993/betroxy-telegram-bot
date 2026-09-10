import threading
import time

import bot
import v94_admin_mode_cleanup as v94

v93 = v94.v93
v92 = v94.v92
v91 = v94.v91
v89 = v94.v89
v88 = v94.v88
v85 = v94.v85
v83 = v94.v83

_old_public_menu = bot.public_menu
_old_business_menu = v88.v88_business_menu


def _clone_button(btn, text):
    """Rebuild only the label while preserving the button action."""
    kwargs = {}
    for attr in (
        "url", "callback_data", "web_app", "login_url", "switch_inline_query",
        "switch_inline_query_current_chat", "callback_game", "pay",
        "switch_inline_query_chosen_chat", "copy_text",
    ):
        value = getattr(btn, attr, None)
        if value is not None:
            kwargs[attr] = value
    return bot.InlineKeyboardButton(text, **kwargs)


def _clean_labels(markup, *, customer=True):
    """Make reward/notification actions obvious and remove duplicated wording."""
    rows = []
    reward_btn = None
    mobile_btn = None

    for row in getattr(markup, "inline_keyboard", []) or []:
        out = []
        for btn in row:
            text = str(getattr(btn, "text", "") or "")
            low = text.lower()
            cb = str(getattr(btn, "callback_data", "") or "")

            if "updates & quiz" in low or cb == "eng_preferences":
                btn = _clone_button(btn, "🔔 Notification Preferences")
            elif text.strip() in {"📢 Updates", "Updates"}:
                btn = _clone_button(btn, "📢 News & Updates")
            elif "mobile for rewards" in low or cb == "v89_mobile":
                btn = _clone_button(btn, "📱 Verify Mobile")
            elif "my rewards" in low or cb == "v89_my_rewards":
                btn = _clone_button(btn, "🎁 My Rewards")

            if customer and str(getattr(btn, "callback_data", "") or "") == "v89_my_rewards":
                reward_btn = btn
                continue
            if customer and str(getattr(btn, "callback_data", "") or "") == "v89_mobile":
                mobile_btn = btn
                continue
            out.append(btn)
        if out:
            rows.append(out)

    # Put personal reward actions together directly below Free Quiz & Rewards.
    if customer and (reward_btn or mobile_btn):
        pair = [b for b in (reward_btn, mobile_btn) if b]
        quiz_index = None
        for i, row in enumerate(rows):
            if any("free quiz" in str(getattr(b, "text", "")).lower() for b in row):
                quiz_index = i
                break
        insert_at = (quiz_index + 1) if quiz_index is not None else min(2, len(rows))
        rows.insert(insert_at, pair)

    return bot.InlineKeyboardMarkup(rows)


def v95_public_menu(user_id=None):
    return _clean_labels(_old_public_menu(user_id), customer=True)


def v95_business_menu(styled=True):
    return _clean_labels(_old_business_menu(styled=styled), customer=False)


def v95_preferences_text(user_id):
    s = v83._subscription(user_id) or {}
    return (
        "🔔 <b>BETROXY Notification Preferences</b>\n\n"
        "Choose which optional BETROXY messages you want to receive. "
        "Your Free Quiz & Rewards entry stays separate from these settings.\n\n"
        f"🏏 Sports alerts: <b>{'ON ✅' if s.get('sports_updates') else 'OFF'}</b>\n"
        f"🎁 Promotion alerts: <b>{'ON ✅' if s.get('promotions') else 'OFF'}</b>\n"
        f"🏆 Quiz & reward alerts: <b>{'ON ✅' if s.get('quiz_rewards') else 'OFF'}</b>\n\n"
        "You can change these anytime or stop all optional notifications."
    )


def v95_preferences_keyboard(user_id):
    s = v83._subscription(user_id) or {}
    return bot.InlineKeyboardMarkup([
        [bot.InlineKeyboardButton(
            f"🏏 Sports Alerts {'✅' if s.get('sports_updates') else '➕'}",
            callback_data="eng_pref:sports_updates",
        )],
        [bot.InlineKeyboardButton(
            f"🎁 Promotion Alerts {'✅' if s.get('promotions') else '➕'}",
            callback_data="eng_pref:promotions",
        )],
        [bot.InlineKeyboardButton(
            f"🏆 Quiz & Reward Alerts {'✅' if s.get('quiz_rewards') else '➕'}",
            callback_data="eng_pref:quiz_rewards",
        )],
        [bot.InlineKeyboardButton("🔕 Stop All Optional Notifications", callback_data="eng_stop_all")],
        [bot.InlineKeyboardButton("⬅️ Main Menu", callback_data="home")],
    ])


def _startup_menu_diagnostic():
    try:
        markup = v95_public_menu(None)
        labels = [str(getattr(b, "text", "")) for row in markup.inline_keyboard for b in row]
        prefs = sum(1 for x in labels if "Notification Preferences" in x)
        old = sum(1 for x in labels if "Updates & Quiz" in x)
        rewards = sum(1 for x in labels if "My Rewards" in x)
        mobile = sum(1 for x in labels if "Verify Mobile" in x)
        bot.logger.warning(
            "V95_MENU_DIAGNOSTIC prefs=%s old_updates_quiz=%s my_rewards=%s verify_mobile=%s labels=%s",
            prefs, old, rewards, mobile, labels,
        )
    except Exception:
        bot.logger.exception("V95_MENU_DIAGNOSTIC_FAILED")


# Runtime patches. Keep existing callbacks/actions; this version only simplifies UX labels/layout.
bot.public_menu = v95_public_menu
v89.v89_public_menu = v95_public_menu
v88.v88_public_menu = v95_public_menu
v88.v88_business_menu = v95_business_menu
v83.v75._business_menu = v95_business_menu
v83.v78.business_main_menu = v95_business_menu
v83._preferences_text = v95_preferences_text
v83._preferences_keyboard = v95_preferences_keyboard

bot.logger.warning(
    "V95_CUSTOMER_NAVIGATION_CLEANUP active=on updates_quiz=notification_preferences "
    "news_updates=renamed rewards_actions=paired mobile_label=verify_mobile callbacks_preserved=on"
)


if __name__ == "__main__":
    _startup_menu_diagnostic()
    v93._startup_pdf_diagnostic()
    v88.v63.apply_signup_cta()
    v85._enable_smart_reply_without_reset()
    threading.Thread(target=v83._worker_loop, name="betroxy-engagement-worker", daemon=True).start()
    bot.logger.warning("V95 polling handover delay=12s")
    time.sleep(12)
    bot.main()
