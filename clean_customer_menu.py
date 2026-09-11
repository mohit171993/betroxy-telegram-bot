"""Compact BETROXY customer navigation.

Reorganizes the existing live actions into six primary choices. Existing callbacks
and URLs are preserved; secondary actions move into submenus.
"""
import bot
import v96_customer_menu_final as v96
import v53_attractive_customer_experience_bootstrap as v53
import daily_quiz_schedule as daily_schedule

v110 = daily_schedule.v110

_old_menu = bot.public_menu
_old_callback = bot.callback_handler


def _source_markup(user_id=None):
    """Use the preserved pre-cleanup menu as the canonical action source."""
    try:
        return v96._base_markup(user_id)
    except TypeError:
        return v96._base_markup()
    except Exception:
        try:
            return _old_menu(user_id)
        except TypeError:
            return _old_menu()


def _buttons(user_id=None):
    markup = _source_markup(user_id)
    return [b for row in (getattr(markup, "inline_keyboard", []) or []) for b in row]


def _find(user_id, *needles):
    for b in _buttons(user_id):
        low = str(getattr(b, "text", "") or "").lower()
        if any(n in low for n in needles):
            return b
    return None


def _clone(b, label):
    if not b:
        return None
    kwargs = {}
    for attr in (
        "url", "callback_data", "web_app", "login_url", "switch_inline_query",
        "switch_inline_query_current_chat", "callback_game", "pay",
        "switch_inline_query_chosen_chat", "copy_text",
    ):
        value = getattr(b, attr, None)
        if value is not None:
            kwargs[attr] = value
    return bot.InlineKeyboardButton(label, **kwargs)


def compact_public_menu(user_id=None):
    app = _clone(_find(user_id, "open betroxy", "open app", "play now"), "🚀 Open BETROXY")
    if not app:
        app = bot.InlineKeyboardButton("🚀 Open BETROXY", url=bot.APP_URL)

    # Explicit stable callbacks: do not inherit quiz routing from older menus.
    quiz = bot.InlineKeyboardButton("🏆 Daily Quiz", callback_data="compact_daily_quiz")
    rewards = bot.InlineKeyboardButton("🎁 My Rewards", callback_data="v89_my_rewards")
    account = bot.InlineKeyboardButton("👤 My Account", callback_data="compact_account")
    updates = bot.InlineKeyboardButton("📢 Updates & Promotions", callback_data="compact_updates")
    support = bot.InlineKeyboardButton("🎧 Help & Support", url=bot.TELEGRAM_SUPPORT_URL)

    return bot.InlineKeyboardMarkup([
        [app], [quiz], [rewards], [account], [updates], [support],
    ])


def _submenu(user_id, kind):
    if kind == "account":
        specs = [
            (("account & services",), "👤 Account & Services"),
            (("transactions",), "🧾 Transactions"),
            (("mobile for rewards", "verify mobile"), "📱 Mobile for Rewards"),
            (("refer a friend",), "👥 Refer a Friend"),
            (("how it works",), "ℹ️ How It Works"),
        ]
    else:
        specs = [
            (("promotions",), "🎁 Promotions"),
            (("updates & quiz", "notification preferences"), "🔔 Notification Preferences"),
            (("news & updates", "updates"), "📢 Updates"),
        ]
    rows = []
    seen = set()
    for needles, label in specs:
        b = _find(user_id, *needles)
        if not b:
            continue
        key = str(getattr(b, "callback_data", "") or getattr(b, "url", "") or label)
        if key in seen:
            continue
        seen.add(key)
        rows.append([_clone(b, label)])
    rows.append([bot.InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="compact_home")])
    return bot.InlineKeyboardMarkup(rows)


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


async def compact_callback_handler(update, context):
    q = update.callback_query
    data = str(q.data or "") if q else ""
    if q and data == "compact_daily_quiz":
        return await _open_daily_quiz(q)
    if q and data in {"compact_account", "compact_updates", "compact_home"}:
        await q.answer()
        uid = q.from_user.id
        if data == "compact_account":
            text, markup = "👤 <b>My Account</b>\n\nChoose what you need.", _submenu(uid, "account")
        elif data == "compact_updates":
            text, markup = "📢 <b>Updates & Promotions</b>\n\nChoose an option.", _submenu(uid, "updates")
        else:
            text, markup = "👋 <b>Welcome to BETROXY</b>\n\nChoose an option below.", compact_public_menu(uid)
        try:
            await q.edit_message_text(text, parse_mode=bot.ParseMode.HTML, reply_markup=markup)
        except Exception:
            await q.message.reply_text(text, parse_mode=bot.ParseMode.HTML, reply_markup=markup)
        return
    return await _old_callback(update, context)


# Patch every live menu reference that the preserved /start chain resolves at call time.
bot.public_menu = compact_public_menu
v96.v96_public_menu = compact_public_menu
v53.v53_public_menu = compact_public_menu
bot.callback_handler = compact_callback_handler

bot.logger.warning(
    "COMPACT_CUSTOMER_MENU active=on primary_actions=6 guaranteed=on daily_quiz=v110_30s "
    "secondary_actions=preserved start_renderer=patched submenu_source=legacy"
)
