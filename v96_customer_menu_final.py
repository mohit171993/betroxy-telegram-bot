import threading
import time

import bot
import v95_customer_navigation_cleanup as v95

v94 = v95.v94
v93 = v94.v93
v91 = v94.v91
v89 = v94.v89
v88 = v94.v88
v85 = v94.v85
v83 = v94.v83

# Use the pre-V95 menu as the clean source so the Free Quiz CTA is not confused
# with the Notification Preferences callback (both previously used eng_preferences).
_base_public_menu = v95._old_public_menu
_old_callback_handler = bot.callback_handler


def _clone(btn, text=None, callback_data=None):
    label = text if text is not None else str(getattr(btn, "text", "") or "")
    if callback_data is not None:
        return bot.InlineKeyboardButton(label, callback_data=callback_data)
    kwargs = {}
    for attr in (
        "url", "callback_data", "web_app", "login_url", "switch_inline_query",
        "switch_inline_query_current_chat", "callback_game", "pay",
        "switch_inline_query_chosen_chat", "copy_text",
    ):
        value = getattr(btn, attr, None)
        if value is not None:
            kwargs[attr] = value
    return bot.InlineKeyboardButton(label, **kwargs)


def _base_markup(user_id=None):
    try:
        return _base_public_menu(user_id)
    except TypeError:
        return _base_public_menu()


def v96_public_menu(user_id=None):
    markup = _base_markup(user_id)
    rows = []
    reward_btn = None
    mobile_btn = None
    pref_seen = False

    for row in getattr(markup, "inline_keyboard", []) or []:
        out = []
        for btn in row:
            text = str(getattr(btn, "text", "") or "")
            low = text.lower()
            cb = str(getattr(btn, "callback_data", "") or "")

            # Keep the Free Quiz entry as a real join action, not merely preferences.
            if "free quiz" in low and "reward" in low:
                btn = _clone(btn, "🏆 Free Quiz & Rewards", callback_data="v96_join_free_quiz")
            elif "updates & quiz" in low:
                if pref_seen:
                    continue
                pref_seen = True
                btn = _clone(btn, "🔔 Notification Preferences")
            elif text.strip() in {"📢 Updates", "Updates"}:
                btn = _clone(btn, "📢 News & Updates")
            elif "mobile for rewards" in low or cb == "v89_mobile":
                btn = _clone(btn, "📱 Verify Mobile")
            elif "my rewards" in low or cb == "v89_my_rewards":
                btn = _clone(btn, "🎁 My Rewards")

            # Pair the two personal reward-management actions to reduce scrolling.
            new_cb = str(getattr(btn, "callback_data", "") or "")
            if new_cb == "v89_my_rewards":
                reward_btn = btn
                continue
            if new_cb == "v89_mobile":
                mobile_btn = btn
                continue

            out.append(btn)
        if out:
            rows.append(out)

    if reward_btn or mobile_btn:
        pair = [b for b in (reward_btn, mobile_btn) if b]
        quiz_idx = next(
            (i for i, r in enumerate(rows) if any("free quiz" in str(getattr(b, "text", "")).lower() for b in r)),
            None,
        )
        rows.insert((quiz_idx + 1) if quiz_idx is not None else min(2, len(rows)), pair)

    return bot.InlineKeyboardMarkup(rows)


def v96_admin_customer_view_menu():
    """Same customer menu for admin preview, plus one route back to Admin Center."""
    markup = v96_public_menu(None)
    rows = [list(r) for r in markup.inline_keyboard]
    rows = [
        [b for b in r if str(getattr(b, "callback_data", "") or "") not in {"admin_home", "analytics_home", "intel_home", "v91_reports_home"}
         and not any(k in str(getattr(b, "text", "") or "").lower() for k in ("admin panel", "bot analytics", "reporting center", "intelligence"))]
        for r in rows
    ]
    rows = [r for r in rows if r]
    rows.append([bot.InlineKeyboardButton("🛠 Back to Admin Control Center", callback_data="v94_admin_home")])
    return bot.InlineKeyboardMarkup(rows)


async def v96_callback_handler(update, context):
    q = update.callback_query
    data = str(q.data or "") if q else ""
    if q and data == "v96_join_free_quiz":
        uid = q.from_user.id
        await q.answer("Quiz & Rewards enabled")
        try:
            v83._set_subscription(uid, "quiz_rewards", True, source="free_quiz_menu")
            await q.message.reply_text(
                "🏆 <b>Free Quiz & Rewards is ON ✅</b>\n\n"
                "You can now receive free sports quizzes and live prediction challenges, collect points and compete on the leaderboard.\n\n"
                "No deposit or wager is required. Notification Preferences can be changed anytime.",
                parse_mode=bot.ParseMode.HTML,
                reply_markup=bot.InlineKeyboardMarkup([
                    [
                        bot.InlineKeyboardButton("🎁 My Rewards", callback_data="v89_my_rewards"),
                        bot.InlineKeyboardButton("🔔 Preferences", callback_data="eng_preferences"),
                    ],
                ]),
            )
            mode = v88._send_first_quiz(uid)
            bot.logger.warning("V96_FREE_QUIZ_JOIN uid=%s first_quiz=%s", uid, mode)
        except Exception:
            bot.logger.exception("V96_FREE_QUIZ_JOIN_FAILED uid=%s", uid)
            await q.message.reply_text("⚠️ Could not start the quiz right now. Please try again.")
        return
    return await _old_callback_handler(update, context)


def _startup_diagnostic():
    try:
        labels = [str(getattr(b, "text", "")) for r in v96_public_menu(None).inline_keyboard for b in r]
        bot.logger.warning(
            "V96_MENU_DIAGNOSTIC free_quiz=%s preferences=%s old_updates_quiz=%s rewards=%s mobile=%s labels=%s",
            sum("Free Quiz & Rewards" in x for x in labels),
            sum("Notification Preferences" in x for x in labels),
            sum("Updates & Quiz" in x for x in labels),
            sum("My Rewards" in x for x in labels),
            sum("Verify Mobile" in x for x in labels),
            labels,
        )
    except Exception:
        bot.logger.exception("V96_MENU_DIAGNOSTIC_FAILED")


bot.public_menu = v96_public_menu
v89.v89_public_menu = v96_public_menu
v88.v88_public_menu = v96_public_menu
# v94 customer preview resolves this function dynamically.
v94._clean_customer_menu = v96_admin_customer_view_menu
bot.callback_handler = v96_callback_handler

bot.logger.warning(
    "V96_CUSTOMER_MENU_FINAL active=on free_quiz=direct_join notification_preferences=separate "
    "my_rewards_verify_mobile=paired news_updates=clear admin_preview=same_customer_menu"
)


if __name__ == "__main__":
    _startup_diagnostic()
    v93._startup_pdf_diagnostic()
    v88.v63.apply_signup_cta()
    v85._enable_smart_reply_without_reset()
    threading.Thread(target=v83._worker_loop, name="betroxy-engagement-worker", daemon=True).start()
    bot.logger.warning("V96 polling handover delay=12s")
    time.sleep(12)
    bot.main()
