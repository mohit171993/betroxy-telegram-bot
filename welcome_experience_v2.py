"""Authoritative BETROXY welcome/menu experience for OfficialBot and Telegram Business.

This module is installed late in production so every customer-facing entry path resolves
one visible menu hierarchy. Direct-bot actions use callbacks where appropriate;
Telegram Business uses URL/deep-link equivalents because Business replies have different
button constraints. The labels, order and styling remain the same.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bot

IST = timezone(timedelta(hours=5, minutes=30))
OFFICIAL_BOT = "BetroxyOfficialBot"
UPDATES_URL = "https://t.me/betroxyupdates"
MINIAPP_DEEPLINK = "https://t.me/BetroxyBot/sportsbook?startapp=sportsbook"

_installed = False


def _ist_now():
    return datetime.now(timezone.utc).astimezone(IST)


def _quiz_state():
    now = _ist_now()
    minute = now.hour * 60 + now.minute
    if minute < 10 * 60:
        return "upcoming_today"
    if minute < 21 * 60:
        return "live"
    return "closed"


def _bot_deeplink(payload):
    return f"https://t.me/{OFFICIAL_BOT}?start={payload}"


def _menu_button(text, *, style=None, **kwargs):
    """Use Bot API button styles without requiring a PTB major-version upgrade."""
    api_kwargs = {"style": style} if style else None
    return bot.InlineKeyboardButton(text, api_kwargs=api_kwargs, **kwargs)


def _welcome_text(returning=False):
    state = _quiz_state()
    if state == "live":
        quiz_line = "🏆 Today's Daily Quiz is <b>LIVE</b> • ₹1,000 prize pool • closes 9:00 PM IST."
    elif state == "upcoming_today":
        quiz_line = "🏆 Today's Daily Quiz starts at <b>10:00 AM IST</b> • ₹1,000 prize pool."
    else:
        quiz_line = "🏆 Today's Daily Quiz is closed • next quiz tomorrow at <b>10:00 AM IST</b>."

    hello = "👋 <b>Welcome back to BETROXY</b>" if returning else "👋 <b>Welcome to BETROXY</b>"
    return (
        f"{hello}\n\n"
        "🚀 <b>Open BETROXY</b> for the full platform experience.\n\n"
        f"{quiz_line}\n"
        "🎁 Rewards, account access and official updates are available below.\n\n"
        "Choose what you want to do 👇"
    )


def _bot_welcome_text():
    return _welcome_text(returning=False)


def _business_welcome_text():
    # Keep Business DM visually identical to the direct bot welcome.
    return _welcome_text(returning=False)


def _prize_text():
    state = _quiz_state()
    if state == "live":
        status = "🟢 <b>Today's quiz is LIVE</b> — entries close at <b>9:00 PM IST</b>."
    elif state == "upcoming_today":
        status = "⏰ <b>Today's quiz starts at 10:00 AM IST.</b>"
    else:
        status = "🌙 <b>Today's quiz is closed.</b> Next quiz starts tomorrow at <b>10:00 AM IST</b>."
    return (
        "🎁 <b>BETROXY DAILY QUIZ — PRIZE DETAILS</b>\n\n"
        "Daily prize pool: <b>₹1,000</b>\n\n"
        "🥇 1st — <b>₹500</b>\n"
        "🥈 2nd — <b>₹300</b>\n"
        "🥉 3rd — <b>₹200</b>\n\n"
        "7 questions • 30 seconds each • one attempt per day\n"
        "Ranking: accuracy → hard-question accuracy → total answer time\n"
        "💯 Free to participate — no deposit or wager required.\n\n"
        f"{status}\n"
        "🏆 Final results: <b>9:05 PM IST</b>"
    )


def _primary_bot_button():
    return _menu_button("🏆 Daily Quiz", callback_data="compact_daily_quiz", style="primary")


def bot_menu(user_id=None):
    """Authoritative direct-bot menu: BETROXY first, engagement second."""
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


def business_menu(styled=True):
    """Same visible menu as bot_menu, using Business-compatible deep links."""
    success_style = "success" if styled else None
    primary_style = "primary" if styled else None
    return bot.InlineKeyboardMarkup([
        [_menu_button("🚀 Open BETROXY", url=MINIAPP_DEEPLINK, style=success_style)],
        [_menu_button("🏆 Daily Quiz", url=_bot_deeplink("dailyquiz"), style=primary_style)],
        [_menu_button("🎁 My Rewards", url=_bot_deeplink("rewards"), style=primary_style)],
        [
            _menu_button("👤 My Account", url=_bot_deeplink("account")),
            _menu_button("📢 Updates & Promotions", url=_bot_deeplink("updates")),
        ],
        [_menu_button("🎧 Help & Support", url=_bot_deeplink("support"))],
    ])


def _labels(markup):
    return [str(getattr(b, "text", "")) for row in markup.inline_keyboard for b in row]


def install(compact_menu, business_module):
    global _installed
    if _installed:
        return business_menu

    v75 = business_module
    biz51 = v75.biz51
    v59 = v75.v59
    previous_start = bot.start
    previous_callback = bot.callback_handler
    previous_business_payload = v75._business_reply_payload

    async def welcome_start(update, context):
        args = list(getattr(context, "args", []) or [])
        payload = str(args[0]).strip().lower() if args else ""
        user = getattr(update, "effective_user", None)
        msg = getattr(update, "effective_message", None)

        # Preserve admin/affiliate dashboards and every existing specialized deep-link.
        if user and (bot.is_admin(user.id) or bot.find_agent_by_telegram_user_id(user.id)):
            return await previous_start(update, context)
        if payload and payload != "prizes":
            return await previous_start(update, context)
        if not msg:
            return await previous_start(update, context)

        try:
            if user:
                try:
                    compact_menu.v83._touch_user(user.id, "welcome_v2")
                except Exception:
                    pass

            # Preserve old prize-detail deep links, but keep Prize Details off the main menu.
            if payload == "prizes":
                await msg.reply_text(
                    _prize_text(),
                    parse_mode=bot.ParseMode.HTML,
                    reply_markup=bot.InlineKeyboardMarkup([
                        [_primary_bot_button()],
                        [_menu_button("🚀 Open BETROXY", url=MINIAPP_DEEPLINK, style="success")],
                        [_menu_button("⬅️ Main Menu", callback_data="welcome_home")],
                    ]),
                    disable_web_page_preview=True,
                )
                return

            try:
                await msg.reply_photo(
                    photo=v75.v63.BANNER_URL,
                    caption="✨ <b>BETROXY</b> • Platform • Daily Quiz • Rewards",
                    parse_mode=bot.ParseMode.HTML,
                )
            except Exception:
                bot.logger.exception("WELCOME_V2_BANNER_FAILED uid=%s", getattr(user, "id", None))

            await msg.reply_text(
                _bot_welcome_text(),
                parse_mode=bot.ParseMode.HTML,
                reply_markup=bot_menu(user.id if user else None),
                disable_web_page_preview=True,
            )
            bot.logger.warning("WELCOME_V2_START uid=%s state=%s menu=authoritative6", getattr(user, "id", None), _quiz_state())
            return
        except Exception:
            bot.logger.exception("WELCOME_V2_START_FAILED uid=%s", getattr(user, "id", None))
            return await previous_start(update, context)

    async def welcome_callback(update, context):
        q = getattr(update, "callback_query", None)
        data = str(getattr(q, "data", "") or "") if q else ""
        if q and data == "welcome_prizes":
            await q.answer()
            await q.message.reply_text(
                _prize_text(),
                parse_mode=bot.ParseMode.HTML,
                reply_markup=bot.InlineKeyboardMarkup([
                    [_primary_bot_button()],
                    [_menu_button("🚀 Open BETROXY", url=MINIAPP_DEEPLINK, style="success")],
                    [_menu_button("⬅️ Main Menu", callback_data="welcome_home")],
                ]),
                disable_web_page_preview=True,
            )
            return
        if q and data in {"welcome_home", "compact_home", "ux_home", "home"}:
            await q.answer()
            await q.message.reply_text(
                _welcome_text(returning=True),
                parse_mode=bot.ParseMode.HTML,
                reply_markup=bot_menu(q.from_user.id),
                disable_web_page_preview=True,
            )
            return
        return await previous_callback(update, context)

    def welcome_business_payload(intent, first_reply=False):
        if first_reply or intent in {"greeting", "general"}:
            return _business_welcome_text(), business_menu(styled=True), "engaged"
        return previous_business_payload(intent, first_reply=False)

    # One authoritative public menu. This replaces the globals used by the old V59
    # free-text greeting path as well, so typing "hi" can no longer show the legacy
    # Account & Services / Transactions / Refer a Friend menu.
    compact_menu.compact_public_menu = bot_menu
    compact_menu.v96.v96_public_menu = bot_menu
    compact_menu.v53.v53_public_menu = bot_menu
    bot.public_menu = bot_menu
    bot.public_welcome_text = _bot_welcome_text
    v59.v59_public_menu = bot_menu
    v59.v59_public_welcome_text = lambda returning=False: _welcome_text(returning=bool(returning))

    # Business sender resolves these functions at send time.
    v75._business_menu = business_menu
    v75._business_reply_payload = welcome_business_payload
    biz51._reply_payload = welcome_business_payload

    # Old Business callback screens can still exist in users' chat history. Patch
    # their Main Menu renderer too so tapping an old button returns the new menu.
    try:
        import v78_business_officialbot_actions as v78
        v78.business_main_menu = business_menu
        v78._business_reply_payload = welcome_business_payload
    except Exception:
        bot.logger.exception("WELCOME_V2_V78_COMPAT_PATCH_FAILED")

    # Final customer-facing wrappers; all previous specialized routes are preserved.
    bot.start = welcome_start
    bot.callback_handler = welcome_callback

    direct_labels = _labels(bot_menu(None))
    business_labels = _labels(business_menu(True))
    if direct_labels != business_labels:
        raise RuntimeError(f"Authoritative customer menu mismatch: bot={direct_labels} business={business_labels}")

    _installed = True
    bot.logger.warning(
        "WELCOME_EXPERIENCE_V2 active=on authoritative_menu=6 bot_menu=6 business_menu=6 "
        "betroxy_first=on prize_details_main=off styles=success_primary_same labels_match=on "
        "v59_free_text_menu=patched old_business_home=patched"
    )
    return business_menu
