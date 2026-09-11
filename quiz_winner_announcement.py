"""Daily free-quiz winner announcement enhancement.

Adds a clearer public Top-3 announcement and one private result notification
per winner. Existing reward processing and admin approval are unchanged.
"""
from __future__ import annotations

import html
import bot

_INSTALLED = False


def install(schedule):
    global _INSTALLED
    if _INSTALLED:
        return

    v110 = schedule.v110
    original_issue = schedule._issue_winner_rewards
    original_winner_text = schedule._winner_text

    def _send_winner_notice(campaign, row, rank):
        uid = int(row.get("telegram_user_id") or 0)
        if not uid:
            return False
        delivery_type = f"winner_notice_rank_{int(rank)}"
        if v110._delivery_exists(campaign["id"], str(uid), delivery_type):
            return True
        score = int(row.get("correct_count") or 0)
        text = (
            "🎉 <b>Congratulations!</b>\n\n"
            f"You finished today's free quiz at <b>#{int(rank)}</b> with <b>{score}/7</b>.\n\n"
            "Your final position has been recorded.\n"
            "📅 <b>Next quiz starts tomorrow at 10:00 AM IST.</b>"
        )
        try:
            ok, data = schedule._send_text(uid, text)
        except Exception:
            bot.logger.exception("QUIZ_WINNER_NOTICE_FAILED campaign=%s uid=%s rank=%s", campaign["id"], uid, rank)
            return False
        if ok:
            payload = data if isinstance(data, dict) else {}
            mid = ((payload.get("result") or {}).get("message_id"))
            v110._mark_delivery(campaign["id"], str(uid), delivery_type, mid)
        bot.logger.warning("QUIZ_WINNER_NOTICE sent=%s campaign=%s uid=%s rank=%s", ok, campaign["id"], uid, rank)
        return bool(ok)

    def _issue_and_notify(campaign, rows):
        original_issue(campaign, rows)
        for rank in (1, 2, 3):
            if len(rows) >= rank:
                _send_winner_notice(campaign, rows[rank - 1], rank)

    def _public_winner_text(rows):
        if not rows:
            return original_winner_text(rows)
        medals = ["🥇", "🥈", "🥉"]
        prizes = [500, 300, 200]
        lines = [
            "🏆 <b>BETROXY DAILY CHALLENGE — FINAL RESULTS</b>",
            "",
            "🎉 <b>CONGRATULATIONS TO TODAY'S TOP 3!</b>",
            "",
        ]
        for i, row in enumerate(rows[:3]):
            name = row.get("telegram_username") or f"Player {str(row.get('telegram_user_id') or '')[-4:]}"
            lines.append(
                f"{medals[i]} <b>{html.escape(str(name))}</b> — "
                f"{int(row.get('correct_count') or 0)}/7 — <b>₹{prizes[i]}</b>"
            )
        lines += [
            "",
            "Final ranking: accuracy → hard-question accuracy → total answer time.",
            "📅 <b>Next quiz starts tomorrow at 10:00 AM IST.</b>",
        ]
        return "\n".join(lines)

    schedule._issue_winner_rewards = _issue_and_notify
    schedule._winner_text = _public_winner_text
    _INSTALLED = True
    bot.logger.warning(
        "QUIZ_WINNER_ANNOUNCEMENT active=on public_top3=on public_prizes=500/300/200 "
        "private_winner_notice=on duplicate_guard=on"
    )
