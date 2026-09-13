"""Additive opening-time guard for the BETROXY Daily Quiz.

Purpose:
- hard-block Daily Quiz participation before 10:00 AM IST
- keep the existing 9:00 PM IST close unchanged
- show a clear customer message before opening / after closing
- protect both the normal Daily Quiz button and ?start=dailyquiz deep link
- provide a final hard guard around v110._start_quiz for any other route

This module does not modify the locked Daily Quiz schedule, question bank,
prizes, result timing, banners, reminders, payout logic, or Daily Quiz files.
"""
from __future__ import annotations

from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

import bot

IST = ZoneInfo("Asia/Kolkata")
OPEN_AT = dtime(10, 0)
CLOSE_AT = dtime(21, 0)

_installed = False


def _now_ist():
    return datetime.now(IST)


def _window_state(now=None):
    local = now or _now_ist()
    clock = local.time().replace(tzinfo=None)
    if clock < OPEN_AT:
        return "before_open"
    if clock >= CLOSE_AT:
        return "closed"
    return "open"


def _message(state):
    if state == "before_open":
        return (
            "⏰ <b>BETROXY Daily Quiz opens at 10:00 AM IST.</b>\n\n"
            "Today's challenge is not open yet. Come back between "
            "<b>10:00 AM and 9:00 PM IST</b> to play your one daily attempt.\n\n"
            "🎁 ₹1,000 daily prize pool • 7 questions • 30 seconds each"
        )
    return (
        "🔒 <b>Today's BETROXY Daily Quiz is closed.</b>\n\n"
        "The next Daily Quiz opens tomorrow at <b>10:00 AM IST</b>.\n\n"
        "🎁 ₹1,000 daily prize pool • 7 questions • 30 seconds each"
    )


def install(v110, quiz, compact_menu):
    global _installed
    if _installed:
        return

    # 1) Hard safety gate around the actual Daily Quiz starter. Any route that
    # reaches v110._start_quiz outside 10:00-21:00 IST is blocked here.
    previous_start_quiz = v110._start_quiz

    async def guarded_start_quiz(uid, username, campaign, source="officialbot"):
        state = _window_state()
        if state != "open":
            ok, _ = quiz._tg_send_text(int(uid), _message(state), None)
            bot.logger.warning(
                "DAILY_QUIZ_OPENING_GUARD_BLOCK uid=%s source=%s state=%s sent=%s",
                uid, source, state, ok,
            )
            return
        return await previous_start_quiz(uid, username, campaign, source)

    guarded_start_quiz._daily_opening_guard = True
    v110._start_quiz = guarded_start_quiz

    # 2) Normal customer-menu route: block before mobile/consent prompts so a
    # user tapping Daily Quiz before 10 AM sees the opening time immediately.
    previous_open_daily_quiz = compact_menu._open_daily_quiz

    async def guarded_open_daily_quiz(q):
        state = _window_state()
        if state != "open":
            await q.answer()
            await q.message.reply_text(_message(state), parse_mode=bot.ParseMode.HTML)
            bot.logger.warning(
                "DAILY_QUIZ_OPENING_GUARD_MENU uid=%s state=%s",
                getattr(q.from_user, "id", 0), state,
            )
            return
        return await previous_open_daily_quiz(q)

    guarded_open_daily_quiz._daily_opening_guard = True
    compact_menu._open_daily_quiz = guarded_open_daily_quiz

    # 3) Business/deep-link route: unified_customer_menu has already installed
    # its direct ?start=dailyquiz handler by the time this module is installed.
    previous_bot_start = bot.start

    async def guarded_bot_start(update, context):
        args = list(getattr(context, "args", []) or [])
        payload = str(args[0]).strip().lower() if args else ""
        if payload == "dailyquiz":
            state = _window_state()
            if state != "open":
                msg = getattr(update, "effective_message", None)
                user = getattr(update, "effective_user", None)
                if msg:
                    await msg.reply_text(_message(state), parse_mode=bot.ParseMode.HTML)
                bot.logger.warning(
                    "DAILY_QUIZ_OPENING_GUARD_DEEPLINK uid=%s state=%s",
                    getattr(user, "id", 0) if user else 0, state,
                )
                return
        return await previous_bot_start(update, context)

    guarded_bot_start._daily_opening_guard = True
    bot.start = guarded_bot_start

    _installed = True
    bot.logger.warning(
        "DAILY_QUIZ_OPENING_GUARD active=on timezone=Asia/Kolkata "
        "open=10:00 close=21:00 routes=menu+dailyquiz_deeplink+hard_start_guard "
        "locked_daily_files_unchanged=on prizes_unchanged=on result_21:05_unchanged=on"
    )
