"""Highlight the post-quiz schedule in every BETROXY completion message."""
from __future__ import annotations

import html
import re
from datetime import timedelta

import bot
import daily_quiz_experience_v2 as experience

_installed = False


def install(production_globals):
    global _installed
    if _installed:
        return

    previous_result_text = production_globals.get("_result_text")
    if not callable(previous_result_text):
        raise RuntimeError("Quiz completion result formatter is unavailable")

    def _result_text_with_timeline(entry, rank, already=False):
        text = previous_result_text(entry, rank, already)

        # Experience V2 currently ends with a simple 'Tomorrow: <theme>' line.
        # Replace it with the complete schedule so the player knows exactly
        # when today's ranking locks, when winners are announced and when to return.
        text = re.sub(
            r"\n\n📅 Tomorrow: <b>.*?</b>\s*$",
            "",
            text,
            flags=re.DOTALL,
        )

        tomorrow = experience._india_now().date() + timedelta(days=1)
        theme = html.escape(experience.bank.theme_for_date(tomorrow))
        text += (
            "\n\n⏰ <b>Leaderboard locks: 9:00 PM IST</b>"
            "\n🏆 <b>Final results announced: 9:05 PM IST</b>"
            "\n📅 <b>Next quiz starts: Tomorrow at 10:00 AM IST</b>"
            f"\n🎯 Tomorrow's theme: <b>{theme}</b>"
        )
        return text

    production_globals["_result_text"] = _result_text_with_timeline
    _installed = True
    bot.logger.warning(
        "QUIZ_COMPLETION_TIMELINE active=on leaderboard_lock=21:00_IST final_result=21:05_IST next_quiz=10:00_IST"
    )
