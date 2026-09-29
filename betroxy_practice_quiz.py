"""Verified, on-demand sports warm-up with no official quiz side effects.

Practice questions are authored separately from the Daily/Sunday question bank.
The only database read is the existing contact-verification check; practice
state itself never writes an entry, answer, leaderboard, points, or reward row.
The official quiz and its opening window remain separate routes.
"""
from __future__ import annotations

import html
import secrets
import time

import bot
import betroxy_universal_verification as access
import daily_quiz_question_bank as official_bank


SESSION_KEY = "btx_practice_session"
SESSION_SECONDS = 15 * 60

# FIDE Laws 3.5-3.6: https://handbook.fide.com/chapter/e012023
# IFAB Law 12: https://www.theifab.com/laws/latest/fouls-and-misconduct/
# World Archery target guide: https://www.worldarchery.sport/sport/disciplines/target-archery
QUESTIONS = (
    {
        "question": "Which chess piece can jump over other pieces?",
        "options": ("Bishop", "Knight", "Rook", "Queen"),
        "correct": 1,
        "explanation": "The knight is the only chess piece that can jump over pieces between its starting and landing squares.",
    },
    {
        "question": "What does a red card mean in football?",
        "options": ("A corner kick", "A sending-off", "A substitution", "A goal"),
        "correct": 1,
        "explanation": "A red card signals a sending-off under the Laws of the Game.",
    },
    {
        "question": "What colour is the centre of a standard target-archery face?",
        "options": ("Blue", "Black", "Gold", "White"),
        "correct": 2,
        "explanation": "The central scoring area is gold; the outer rings use other colours.",
    },
)


def _check_separation():
    official = {str(item["question"]).casefold() for item in official_bank.QUESTION_BANK}
    practice = [item["question"].casefold() for item in QUESTIONS]
    if len(set(practice)) != len(practice) or official.intersection(practice):
        raise RuntimeError("Practice questions must be unique and absent from the official bank")
    if any(len(item["options"]) != 4 or item["correct"] not in range(4) or not item["explanation"] for item in QUESTIONS):
        raise RuntimeError("Every practice question needs four options, one answer, and an explanation")


def _menu_with_practice(markup):
    rows = [list(row) for row in markup.inline_keyboard]
    if any(getattr(button, "callback_data", None) == "btx_practice" for row in rows for button in row):
        return markup
    button = bot.InlineKeyboardButton("🎯 Practice Quiz • No prizes", callback_data="btx_practice")
    insert_at = len(rows)
    for index, row in enumerate(rows):
        if any("Sunday Mega Quiz" in str(getattr(button, "text", "")) for button in row):
            insert_at = index + 1
            break
        if any("Daily Quiz" in str(getattr(button, "text", "")) for button in row):
            insert_at = index + 1
    rows.insert(insert_at, [button])
    return bot.InlineKeyboardMarkup(rows)


def _question_markup(session):
    question = QUESTIONS[session["index"]]
    rows = [
        [bot.InlineKeyboardButton(option, callback_data=f"btx_practice:{session['nonce']}:{session['index']}:{i}")]
        for i, option in enumerate(question["options"])
    ]
    return bot.InlineKeyboardMarkup(rows)


async def _send_question(message, session):
    question = QUESTIONS[session["index"]]
    await message.reply_text(
        f"🎯 <b>Practice {session['index'] + 1}/{len(QUESTIONS)}</b> — no prizes or leaderboard\n\n"
        f"{html.escape(question['question'])}",
        parse_mode=bot.ParseMode.HTML,
        reply_markup=_question_markup(session),
    )


async def _handle_callback(q, context):
    data = str(q.data or "")
    state = context.user_data.get(SESSION_KEY)
    if data == "btx_practice":
        await q.answer()
        state = {"nonce": secrets.token_hex(4), "index": 0, "score": 0,
                 "pending_next": False, "expires": time.monotonic() + SESSION_SECONDS}
        context.user_data[SESSION_KEY] = state
        await _send_question(q.message, state)
        return

    parts = data.split(":")
    if (not state or len(parts) not in (3, 4) or parts[1] != state["nonce"]
            or time.monotonic() > state["expires"]):
        await q.answer("This practice round expired. Open Practice Quiz again.", show_alert=True)
        return
    try:
        index = int(parts[2])
    except ValueError:
        await q.answer("Invalid practice question.", show_alert=True)
        return
    if index != state["index"]:
        await q.answer("That question is already closed.", show_alert=True)
        return

    if parts[0] == "btx_practice_next" and len(parts) == 3 and state["pending_next"]:
        await q.answer()
        state["index"] += 1
        state["pending_next"] = False
        await _send_question(q.message, state)
        return

    if parts[0] != "btx_practice" or len(parts) != 4 or state["pending_next"]:
        await q.answer("That question is already closed.", show_alert=True)
        return
    try:
        selected = int(parts[3])
    except ValueError:
        selected = -1
    if selected not in range(4):
        await q.answer("Invalid answer.", show_alert=True)
        return

    await q.answer()
    try:
        await q.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    question = QUESTIONS[index]
    correct = selected == question["correct"]
    state["score"] += int(correct)
    state["pending_next"] = True
    label = "✅ Correct" if correct else "↪️ Good try"
    answer = html.escape(question["options"][question["correct"]])
    explanation = html.escape(question["explanation"])
    if index + 1 < len(QUESTIONS):
        markup = bot.InlineKeyboardMarkup([[
            bot.InlineKeyboardButton("Next practice question", callback_data=f"btx_practice_next:{state['nonce']}:{index}")
        ]])
    else:
        markup = bot.InlineKeyboardMarkup([[
            bot.InlineKeyboardButton("Practice again", callback_data="btx_practice"),
            bot.InlineKeyboardButton("Main menu", callback_data="compact_home"),
        ]])
        context.user_data.pop(SESSION_KEY, None)
    ending = f"\n\nPractice score: <b>{state['score']}/{len(QUESTIONS)}</b>" if index + 1 == len(QUESTIONS) else ""
    await q.message.reply_text(
        f"{label}. Correct answer: <b>{answer}</b>\n{explanation}{ending}\n\n"
        "Practice has no prizes, ranking, or effect on the official quiz.",
        parse_mode=bot.ParseMode.HTML,
        reply_markup=markup,
    )


def install(compact_menu):
    """Add a verified-only practice action after all production menu overlays."""
    _check_separation()
    if getattr(bot, "_btx_practice_installed", False):
        return
    import welcome_experience_v2 as welcome

    for module, attribute in (
        (welcome, "bot_menu"), (compact_menu, "compact_public_menu"),
        (compact_menu.v96, "v96_public_menu"), (compact_menu.v53, "v53_public_menu"),
        (bot, "public_menu"),
    ):
        previous = getattr(module, attribute)

        def with_practice(*args, _previous=previous, **kwargs):
            return _menu_with_practice(_previous(*args, **kwargs))

        setattr(module, attribute, with_practice)

    previous_callback = bot.callback_handler

    async def practice_callback(update, context):
        q = getattr(update, "callback_query", None)
        data = str(getattr(q, "data", "") or "") if q else ""
        if not q or not (data == "btx_practice" or data.startswith("btx_practice:") or data.startswith("btx_practice_next:")):
            return await previous_callback(update, context)
        user, chat = update.effective_user, update.effective_chat
        if (not user or not chat or str(chat.type) != "private"
                or getattr(q.message, "business_connection_id", None)
                or not access.is_verified(user.id)):
            await q.answer("Verify your account to continue.", show_alert=True)
            return
        return await _handle_callback(q, context)

    bot.callback_handler = practice_callback
    if not any(getattr(button, "callback_data", None) == "btx_practice"
               for row in welcome.bot_menu(None).inline_keyboard for button in row):
        raise RuntimeError("Verified practice action is missing from the OfficialBot menu")
    bot._btx_practice_installed = True
    bot.logger.warning("BTX_PRACTICE_READY verified_only=on questions=%s rewards=off leaderboard=off official_bank=separate", len(QUESTIONS))
