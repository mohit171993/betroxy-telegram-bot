"""Stable BETROXY customer navigation.

The six primary buttons are bound explicitly so later menu-label changes cannot
silently replace Mini App/callback actions with website URLs or dead routes.
"""
import html

import bot
import v96_customer_menu_final as v96
import v53_attractive_customer_experience_bootstrap as v53
import daily_quiz_schedule as daily_schedule

v110 = daily_schedule.v110
v83 = v110.v83

_old_callback = bot.callback_handler
_old_start = bot.start

# Always launch the actual @BetroxyBot Telegram Mini App, never the public website.
MINIAPP_DEEPLINK = "https://t.me/BetroxyBot/sportsbook?startapp=sportsbook"


def _menu_button(text, *, style=None, **kwargs):
    """Create a button with Bot API styling while staying compatible with PTB 21.x."""
    api_kwargs = {"style": style} if style else None
    return bot.InlineKeyboardButton(text, api_kwargs=api_kwargs, **kwargs)


def compact_public_menu(user_id=None):
    """Keep BETROXY as the dominant CTA; quiz/rewards remain secondary actions."""
    return bot.InlineKeyboardMarkup([
        [_menu_button("🚀 Open BETROXY", url=MINIAPP_DEEPLINK, style="success")],
        [_menu_button("🏆 Daily Quiz", callback_data="compact_daily_quiz", style="primary")],
        [_menu_button("🎁 My Rewards", callback_data="v89_my_rewards", style="primary")],
        [
            _menu_button("👤 My Account", callback_data="compact_account"),
            _menu_button("📢 Updates & Promotions", callback_data="compact_updates"),
        ],
        [_menu_button("🎧 Help & Support", callback_data="compact_support")],
    ])


def _account_text(uid):
    try:
        mobile = v110._masked_mobile(uid)
    except Exception:
        mobile = "Not registered"
    try:
        consent = "ON ✅" if v110._has_consent(uid) else "Not completed"
    except Exception:
        consent = "Not completed"
    return (
        "👤 <b>My Account</b>\n\n"
        f"📱 Mobile: <b>{html.escape(str(mobile))}</b>\n"
        f"📣 Quiz communication consent: <b>{consent}</b>\n\n"
        "Choose an option below."
    )


def _account_markup():
    return bot.InlineKeyboardMarkup([
        [_menu_button("🎁 My Rewards", callback_data="v89_my_rewards", style="primary")],
        [_menu_button("📱 Mobile for Rewards", callback_data="v89_mobile")],
        [_menu_button("🔔 Notification Preferences", callback_data="eng_preferences")],
        [_menu_button("🚀 Open BETROXY", url=MINIAPP_DEEPLINK, style="success")],
        [_menu_button("⬅️ Back to Main Menu", callback_data="compact_home")],
    ])


def _updates_markup():
    promo_url = str(getattr(v83, "PROMOTIONS_URL", "") or bot.UPDATES_URL)
    return bot.InlineKeyboardMarkup([
        [_menu_button("🎁 Promotions", url=promo_url, style="primary")],
        [_menu_button("🔔 Notification Preferences", callback_data="eng_preferences")],
        [_menu_button("📢 BETROXY Updates Channel", url=bot.UPDATES_URL, style="primary")],
        [_menu_button("⬅️ Back to Main Menu", callback_data="compact_home")],
    ])


def _support_markup():
    return bot.InlineKeyboardMarkup([
        [_menu_button("🎧 Telegram Support", url=bot.TELEGRAM_SUPPORT_URL)],
        [_menu_button("💬 WhatsApp Support", url=bot.WHATSAPP_SUPPORT_URL)],
        [_menu_button("⬅️ Back to Main Menu", callback_data="compact_home")],
    ])


async def _open_daily_quiz(q):
    """Open the approved V110 daily challenge using the 30-second schedule patch."""
    uid = int(q.from_user.id)
    username = q.from_user.username
    campaign = daily_schedule._today_campaign_windowed(test_mode=False)
    await q.answer()
    if not campaign or str(campaign.get("status") or "") != "open":
        await q.message.reply_text("⏰ Today's quiz is closed. The next daily quiz will open tomorrow.")
        return
    if not v110._mobile(uid):
        await v110._registration_prompt(q.message, uid, int(campaign["id"]))
        return
    if not v110._has_consent(uid):
        await v110._consent_prompt(q.message, uid, int(campaign["id"]))
        return
    await v110._start_quiz(uid, username, campaign, source="compact_daily_quiz")


async def _show(q, text, markup):
    try:
        await q.edit_message_text(text, parse_mode=bot.ParseMode.HTML, reply_markup=markup)
    except Exception:
        await q.message.reply_text(text, parse_mode=bot.ParseMode.HTML, reply_markup=markup)


async def compact_callback_handler(update, context):
    q = update.callback_query
    data = str(q.data or "") if q else ""
    if q and data == "compact_daily_quiz":
        return await _open_daily_quiz(q)
    if q and data in {"compact_account", "compact_updates", "compact_support", "compact_home"}:
        await q.answer()
        if data == "compact_account":
            return await _show(q, _account_text(q.from_user.id), _account_markup())
        if data == "compact_updates":
            return await _show(
                q,
                "📢 <b>Updates & Promotions</b>\n\nChoose what you want to open or manage.",
                _updates_markup(),
            )
        if data == "compact_support":
            return await _show(
                q,
                "🎧 <b>Help & Support</b>\n\nChoose how you would like to contact BETROXY support.",
                _support_markup(),
            )
        return await _show(
            q,
            "🚀 <b>BETROXY</b>\n\nOpen BETROXY for the full experience. Daily Quiz, rewards and updates are available below.\n\nChoose what you want to do 👇",
            compact_public_menu(q.from_user.id),
        )
    return await _old_callback(update, context)


async def compact_start(update, context):
    """Preserve the complete existing /start chain, then honor Business deep links."""
    result = await _old_start(update, context)
    args = list(getattr(context, "args", []) or [])
    payload = str(args[0]).strip().lower() if args else ""
    msg = getattr(update, "effective_message", None)
    user = getattr(update, "effective_user", None)
    if not msg or not user:
        return result
    try:
        if payload == "account":
            await msg.reply_text(_account_text(user.id), parse_mode=bot.ParseMode.HTML, reply_markup=_account_markup())
        elif payload == "updates":
            await msg.reply_text(
                "📢 <b>Updates & Promotions</b>\n\nChoose what you want to open or manage.",
                parse_mode=bot.ParseMode.HTML,
                reply_markup=_updates_markup(),
            )
        elif payload == "support":
            await msg.reply_text(
                "🎧 <b>Help & Support</b>\n\nChoose how you would like to contact BETROXY support.",
                parse_mode=bot.ParseMode.HTML,
                reply_markup=_support_markup(),
            )
    except Exception:
        bot.logger.exception("COMPACT_START_DEEPLINK_FAILED payload=%s uid=%s", payload, user.id)
    return result


def _diagnostic():
    menu = compact_public_menu(None)
    buttons = [b for row in menu.inline_keyboard for b in row]
    actions = {str(b.text): (getattr(b, "callback_data", None), getattr(b, "url", None)) for b in buttons}
    required = {
        "🚀 Open BETROXY": (None, MINIAPP_DEEPLINK),
        "🏆 Daily Quiz": ("compact_daily_quiz", None),
        "🎁 My Rewards": ("v89_my_rewards", None),
        "👤 My Account": ("compact_account", None),
        "📢 Updates & Promotions": ("compact_updates", None),
        "🎧 Help & Support": ("compact_support", None),
    }
    ok = all(actions.get(k) == v for k, v in required.items())
    bot.logger.warning(
        "COMPACT_MENU_INTEGRITY ok=%s miniapp=telegram account=callback updates=callback support=callback "
        "cta_style=success quiz_style=primary rewards_style=primary compact_secondary_row=on",
        ok,
    )
    if not ok:
        raise RuntimeError("Compact customer menu action integrity failed")


# Patch every live menu reference that the preserved /start chain resolves at call time.
bot.public_menu = compact_public_menu
v96.v96_public_menu = compact_public_menu
v53.v53_public_menu = compact_public_menu
bot.callback_handler = compact_callback_handler
bot.start = compact_start
_diagnostic()

bot.logger.warning(
    "COMPACT_CUSTOMER_MENU active=on primary_actions=6 stable_actions=on miniapp=telegram_deeplink "
    "account=callback updates=callback support=callback daily_quiz=v110_30s "
    "cta_style=success quiz_style=primary rewards_style=primary compact_secondary_row=on"
)
