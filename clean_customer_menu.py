"""Compact BETROXY customer navigation.

Reorganizes the existing live actions into six primary choices. Existing callbacks
and URLs are preserved; secondary actions move into submenus.
"""
import bot
import v96_customer_menu_final as v96

_old_menu = bot.public_menu
_old_callback = bot.callback_handler


def _buttons(user_id=None):
    try:
        markup = _old_menu(user_id)
    except TypeError:
        markup = _old_menu()
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
    for attr in ("url", "callback_data", "web_app", "login_url", "switch_inline_query", "switch_inline_query_current_chat", "callback_game", "pay", "switch_inline_query_chosen_chat", "copy_text"):
        value = getattr(b, attr, None)
        if value is not None:
            kwargs[attr] = value
    return bot.InlineKeyboardButton(label, **kwargs)


def compact_public_menu(user_id=None):
    app = _clone(_find(user_id, "open betroxy", "open app", "play now"), "🚀 Open BETROXY")
    quiz = _clone(_find(user_id, "free quiz", "daily quiz"), "🏆 Daily Quiz")
    rewards = _clone(_find(user_id, "my rewards"), "🎁 My Rewards")
    support = _clone(_find(user_id, "help & support", "support"), "🎧 Help & Support")
    rows = []
    if app: rows.append([app])
    if quiz: rows.append([quiz])
    if rewards: rows.append([rewards])
    rows.append([bot.InlineKeyboardButton("👤 My Account", callback_data="compact_account")])
    rows.append([bot.InlineKeyboardButton("📢 Updates & Promotions", callback_data="compact_updates")])
    if support: rows.append([support])
    return bot.InlineKeyboardMarkup(rows)


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


async def compact_callback_handler(update, context):
    q = update.callback_query
    data = str(q.data or "") if q else ""
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


bot.public_menu = compact_public_menu
v96.v96_public_menu = compact_public_menu
bot.callback_handler = compact_callback_handler
bot.logger.warning("COMPACT_CUSTOMER_MENU active=on primary_actions=6 secondary_actions=preserved")
