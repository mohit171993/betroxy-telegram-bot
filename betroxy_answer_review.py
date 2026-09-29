"""Read-only personal Daily/Sunday answer review after actual campaign close.

No answer key is read until the stored campaign closes_at has passed. The
callback is private and contact-verified, and every entry query is scoped to
the Telegram user. This module never writes a quiz, ranking, or reward row.
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone

import bot
import betroxy_universal_verification as access


TABLES = {
    "daily": ("v110_quiz_campaigns", "v110_quiz_entries", "v110_quiz_questions", "v110_quiz_answers", "AND c.test_mode=FALSE"),
    "mega": ("mega_quiz_campaigns", "mega_quiz_entries", "mega_quiz_questions", "mega_quiz_answers", ""),
}


def _closed(campaign, now=None):
    closes_at = campaign.get("closes_at") if campaign else None
    if not isinstance(closes_at, datetime) or closes_at.tzinfo is None:
        return False
    return (now or datetime.now(timezone.utc)) >= closes_at


def _latest_closed_entry(uid, kind):
    campaigns, entries, _questions, _answers, condition = TABLES[kind]
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT e.id AS entry_id, e.campaign_id, c.campaign_date, c.closes_at "
                f"FROM {entries} e JOIN {campaigns} c ON c.id=e.campaign_id "
                f"WHERE e.telegram_user_id=%s AND c.closes_at<=NOW() {condition} "
                "ORDER BY c.closes_at DESC LIMIT 1",
                (int(uid),),
            )
            return cur.fetchone()


def _question_rows(entry, kind):
    campaigns, _entries, questions, answers, _condition = TABLES[kind]
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT q.seq, q.question, q.options_json, q.correct_option, "
                f"a.selected_option, a.is_correct, a.id AS answer_id "
                f"FROM {questions} q JOIN {campaigns} c ON c.id=q.campaign_id "
                f"LEFT JOIN {answers} a "
                "ON a.question_id=q.id AND a.entry_id=%s "
                "WHERE q.campaign_id=%s AND c.closes_at<=NOW() ORDER BY q.seq",
                (int(entry["entry_id"]), int(entry["campaign_id"])),
            )
            return cur.fetchall()


def _render_review(entry, rows, kind):
    label = "Daily Quiz" if kind == "daily" else "Sunday Mega Quiz"
    date = entry["campaign_date"].strftime("%d %b %Y")
    lines = [f"📖 <b>{label} answer review — {date}</b>", "", "Only your missed or unanswered questions are shown."]
    footer = "\nThe official quiz result and ranking are unchanged by this review."
    missed = 0
    shortened = False
    for row in rows:
        if row.get("is_correct") is True:
            continue
        try:
            options = json.loads(row["options_json"])
            correct = html.escape(str(options[int(row["correct_option"])]))
            selected_index = row.get("selected_option")
            selected = html.escape(str(options[int(selected_index)])) if selected_index is not None else "No answer recorded"
            if selected_index is not None and int(selected_index) == int(row["correct_option"]):
                selected += " (not counted in time)"
        except (KeyError, ValueError, TypeError, IndexError):
            bot.logger.exception("BTX_REVIEW_INVALID_QUESTION seq=%s kind=%s", row.get("seq"), kind)
            continue
        block = (
            f"\n<b>Q{int(row['seq'])}.</b> {html.escape(str(row['question']))}\n"
            f"Your answer: {selected}\nCorrect answer: <b>{correct}</b>"
        )
        # Keep complete HTML blocks; slicing a message can leave open tags and
        # cause Telegram to reject the entire review.
        if len("\n".join(lines + [block, footer])) > 3700:
            lines.append("\nReview shortened. Open support if you need the remaining questions.")
            shortened = True
            break
        missed += 1
        lines.append(block)
    if not missed and not shortened:
        lines.append("\nNo missed answers in this entry. Well played!")
    lines.append(footer)
    return "\n".join(lines)


def _menu_with_review(markup):
    rows = [list(row) for row in markup.inline_keyboard]
    if any(getattr(button, "callback_data", None) == "btx_review" for row in rows for button in row):
        return markup
    button = bot.InlineKeyboardButton("📖 Answer review", callback_data="btx_review")
    for row in rows:
        if any(getattr(item, "callback_data", None) == "btx_practice" for item in row):
            row.append(button)
            return bot.InlineKeyboardMarkup(rows)
    rows.append([button])
    return bot.InlineKeyboardMarkup(rows)


async def _review_callback(q, kind):
    await q.answer()
    if kind is None:
        await q.message.reply_text(
            "📖 <b>Answer review</b>\n\nChoose a completed quiz. Answers are available only after that campaign closes.",
            parse_mode=bot.ParseMode.HTML,
            reply_markup=bot.InlineKeyboardMarkup([
                [bot.InlineKeyboardButton("Daily Quiz", callback_data="btx_review:daily")],
                [bot.InlineKeyboardButton("Sunday Mega Quiz", callback_data="btx_review:mega")],
            ]),
        )
        return
    try:
        entry = _latest_closed_entry(q.from_user.id, kind)
        if not entry or not _closed(entry):
            await q.message.reply_text("No completed quiz is available for review yet. Try after the quiz closes.")
            return
        rows = _question_rows(entry, kind)
        if not rows:
            await q.message.reply_text("This quiz is not available for review yet. Try after it closes.")
            return
        body = _render_review(entry, rows, kind)
        await q.message.reply_text(body, parse_mode=bot.ParseMode.HTML)
    except Exception:
        bot.logger.exception("BTX_ANSWER_REVIEW_FAILED kind=%s uid=%s", kind, q.from_user.id)
        await q.message.reply_text("Answer review is unavailable right now. Please try again later.")


def install(compact_menu, production_globals):
    if getattr(bot, "_btx_answer_review_installed", False):
        return
    import welcome_experience_v2 as welcome
    import weekly_mega_quiz as mega

    for module, attribute in (
        (welcome, "bot_menu"), (compact_menu, "compact_public_menu"),
        (compact_menu.v96, "v96_public_menu"), (compact_menu.v53, "v53_public_menu"),
        (bot, "public_menu"),
    ):
        previous = getattr(module, attribute)

        def with_review(*args, _previous=previous, **kwargs):
            return _menu_with_review(_previous(*args, **kwargs))

        setattr(module, attribute, with_review)

    daily_rows = production_globals["_result_rows"]

    def daily_rows_with_review(campaign):
        return [[{"text": "📖 Review answers after close", "callback_data": "btx_review:daily"}]] + daily_rows(campaign)

    production_globals["_result_rows"] = daily_rows_with_review

    mega_rows = mega._completion_rows

    def mega_rows_with_review(campaign):
        return [[{"text": "📖 Review answers after close", "callback_data": "btx_review:mega"}]] + mega_rows(campaign)

    mega._completion_rows = mega_rows_with_review

    previous_callback = bot.callback_handler

    async def review_callback(update, context):
        q = getattr(update, "callback_query", None)
        data = str(getattr(q, "data", "") or "") if q else ""
        if not q or data not in {"btx_review", "btx_review:daily", "btx_review:mega"}:
            return await previous_callback(update, context)
        user, chat = update.effective_user, update.effective_chat
        if (not user or not chat or str(chat.type) != "private"
                or getattr(q.message, "business_connection_id", None)
                or not access.is_verified(user.id)):
            await q.answer("Verify your account to continue.", show_alert=True)
            return
        kind = data.split(":", 1)[1] if ":" in data else None
        return await _review_callback(q, kind)

    bot.callback_handler = review_callback
    if not any(getattr(button, "callback_data", None) == "btx_review"
               for row in welcome.bot_menu(None).inline_keyboard for button in row):
        raise RuntimeError("Answer review action is missing from the OfficialBot menu")
    bot._btx_answer_review_installed = True
    bot.logger.warning("BTX_ANSWER_REVIEW_READY verified_only=on close=campaign_closes_at read_only=on")
