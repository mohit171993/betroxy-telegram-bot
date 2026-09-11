"""Quiz-first BETROXY welcome experience for OfficialBot and Telegram Business."""
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


def _bot_welcome_text():
    state = _quiz_state()
    if state == "live":
        lead = (
            "Play the <b>BETROXY Daily Quiz</b> and compete for today's "
            "<b>₹1,000 prize pool</b>."
        )
        timing = (
            "⏰ <b>Today's Timeline</b>\n"
            "Entries close: <b>9:00 PM IST</b>\n"
            "🏆 Final results: <b>9:05 PM IST</b>"
        )
    elif state == "upcoming_today":
        lead = (
            "Today's <b>₹1,000 BETROXY Daily Quiz</b> starts at "
            "<b>10:00 AM IST</b>."
        )
        timing = (
            "⏰ <b>Today's Timeline</b>\n"
            "Quiz starts: <b>10:00 AM IST</b>\n"
            "Entries close: <b>9:00 PM IST</b>\n"
            "🏆 Final results: <b>9:05 PM IST</b>"
        )
    else:
        lead = (
            "Today's Daily Quiz has closed. The next <b>₹1,000 BETROXY Daily Quiz</b> "
            "starts <b>tomorrow at 10:00 AM IST</b>."
        )
        timing = (
            "⏰ <b>Next Quiz</b>\n"
            "Starts: <b>Tomorrow at 10:00 AM IST</b>\n"
            "Entries close: <b>9:00 PM IST</b>\n"
            "🏆 Final results: <b>9:05 PM IST</b>"
        )

    return (
        "👋 <b>Welcome to BETROXY</b>\n\n"
        f"{lead}\n\n"
        "🏆 <b>Daily Prizes</b>\n"
        "🥇 1st — <b>₹500</b>\n"
        "🥈 2nd — <b>₹300</b>\n"
        "🥉 3rd — <b>₹200</b>\n\n"
        "🎯 <b>Quiz Format</b>\n"
        "7 questions • 30 seconds each • 1 attempt per day\n\n"
        "💯 <b>Free to participate — no deposit or wager required</b>\n\n"
        f"{timing}\n\n"
        "Choose what you want to do 👇"
    )


def _business_welcome_text():
    state = _quiz_state()
    if state == "live":
        lead = "Today's <b>BETROXY Daily Quiz is LIVE</b> with a <b>₹1,000 prize pool</b>."
        timing = "⏰ Entries close at <b>9:00 PM IST</b>\n🏆 Final results at <b>9:05 PM IST</b>"
    elif state == "upcoming_today":
        lead = "Today's <b>₹1,000 BETROXY Daily Quiz</b> starts at <b>10:00 AM IST</b>."
        timing = "⏰ Starts at <b>10:00 AM IST</b>\n🏆 Final results at <b>9:05 PM IST</b>"
    else:
        lead = (
            "Today's quiz has closed. The next <b>₹1,000 BETROXY Daily Quiz</b> "
            "starts <b>tomorrow at 10:00 AM IST</b>."
        )
        timing = "⏰ Next quiz: <b>Tomorrow at 10:00 AM IST</b>\n🏆 Final results at <b>9:05 PM IST</b>"

    return (
        "👋 <b>Welcome to BETROXY</b>\n\n"
        f"{lead}\n\n"
        "🥇 <b>₹500</b> • 🥈 <b>₹300</b> • 🥉 <b>₹200</b>\n\n"
        "🎯 7 questions • 30 seconds each • 1 attempt per day\n"
        "💯 Free to participate — no deposit or wager required.\n\n"
        f"{timing}\n\n"
        "Tap below to get started 👇"
    )


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
    if _quiz_state() == "live":
        return bot.InlineKeyboardButton("🏆 Play Today's Quiz", callback_data="compact_daily_quiz")
    return bot.InlineKeyboardButton("🔔 Quiz Updates & Results", url=UPDATES_URL)


def bot_menu(user_id=None):
    return bot.InlineKeyboardMarkup([
        [_primary_bot_button()],
        [bot.InlineKeyboardButton("🎁 Prize Details", callback_data="welcome_prizes")],
        [bot.InlineKeyboardButton("🚀 Open BETROXY", url=MINIAPP_DEEPLINK)],
        [bot.InlineKeyboardButton("🎁 My Rewards", callback_data="v89_my_rewards")],
        [bot.InlineKeyboardButton("👤 My Account", callback_data="compact_account")],
        [bot.InlineKeyboardButton("📢 Updates & Results", callback_data="compact_updates")],
        [bot.InlineKeyboardButton("🎧 Help & Support", callback_data="compact_support")],
    ])


def business_menu(styled=True):
    if _quiz_state() == "live":
        first = bot.InlineKeyboardButton("🏆 Play Today's Quiz", url=_bot_deeplink("dailyquiz"))
    else:
        first = bot.InlineKeyboardButton("🔔 Quiz Updates & Results", url=UPDATES_URL)
    return bot.InlineKeyboardMarkup([
        [first],
        [bot.InlineKeyboardButton("🎁 Prize Details", url=_bot_deeplink("prizes"))],
        [bot.InlineKeyboardButton("🚀 Open BETROXY", url=MINIAPP_DEEPLINK)],
        [bot.InlineKeyboardButton("🎧 Help & Support", url=_bot_deeplink("support"))],
    ])


def install(compact_menu, business_module):
    global _installed
    if _installed:
        return

    v75 = business_module
    biz51 = v75.biz51
    previous_start = bot.start
    previous_callback = bot.callback_handler
    previous_business_payload = v75._business_reply_payload

    async def welcome_start(update, context):
        args = list(getattr(context, "args", []) or [])
        payload = str(args[0]).strip().lower() if args else ""
        user = getattr(update, "effective_user", None)
        msg = getattr(update, "effective_message", None)

        # Preserve admin/affiliate dashboards and every existing deep-link except
        # the new prize-details route.
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

            if payload == "prizes":
                await msg.reply_text(
                    _prize_text(),
                    parse_mode=bot.ParseMode.HTML,
                    reply_markup=bot.InlineKeyboardMarkup([
                        [_primary_bot_button()],
                        [bot.InlineKeyboardButton("⬅️ Main Menu", callback_data="welcome_home")],
                    ]),
                    disable_web_page_preview=True,
                )
                return

            try:
                await msg.reply_photo(
                    photo=v75.v63.BANNER_URL,
                    caption="✨ <b>BETROXY</b> • Daily Quiz & Rewards",
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
            bot.logger.warning("WELCOME_V2_START uid=%s state=%s", getattr(user, "id", None), _quiz_state())
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
                    [bot.InlineKeyboardButton("⬅️ Main Menu", callback_data="welcome_home")],
                ]),
                disable_web_page_preview=True,
            )
            return
        if q and data in {"welcome_home", "compact_home"}:
            await q.answer()
            await q.message.reply_text(
                _bot_welcome_text(),
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

    # Public menu references used by the current compact navigation.
    compact_menu.compact_public_menu = bot_menu
    compact_menu.v96.v96_public_menu = bot_menu
    compact_menu.v53.v53_public_menu = bot_menu
    bot.public_menu = bot_menu

    # Business sender resolves these functions at send time.
    v75._business_menu = business_menu
    v75._business_reply_payload = welcome_business_payload
    biz51._reply_payload = welcome_business_payload

    # Final customer-facing wrappers; all previous specialized routes are preserved.
    bot.start = welcome_start
    bot.callback_handler = welcome_callback

    _installed = True
    bot.logger.warning(
        "WELCOME_EXPERIENCE_V2 active=on bot_menu=7 business_menu=4 quiz_first=on prize_pool=1000 "
        "old_weekly_points_copy=off dynamic_IST_state=on after_21_closed=on before_10_upcoming=on"
    )
