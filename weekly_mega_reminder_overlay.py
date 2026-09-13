"""Add the Sunday Mega Quiz to existing safe customer reminder messages.

This module is intentionally additive:
- it does not create a new OfficialBot campaign
- it does not create a new Telegram Business campaign
- it does not change recipient eligibility, pacing, cooldowns or retry policy
- OfficialBot keeps the existing one-reminder-per-target-day queue
- Business keeps the existing rolling 7-day reminder policy

For Sunday target days, the existing reminder text also promotes the weekly
₹5,000 Amazon Pay Gift Voucher Mega Quiz. The existing reminder keyboard gets a
Mega Quiz button on every day so users can inspect the upcoming Sunday event.
"""
from __future__ import annotations

import bot

_installed = False
_original_message_for = None
_original_quiz_button = None


def _is_sunday(target_day):
    try:
        return int(target_day.weekday()) == 6
    except Exception:
        return False


def install(weekly_mega, quiz_alerts):
    global _installed, _original_message_for, _original_quiz_button
    if _installed:
        return

    # Cover every current menu renderer, including the dynamic approved-banner
    # /start path, without changing or replacing any of the old menu actions.
    try:
        import clean_customer_menu as compact_menu
        import weekly_mega_menu_overlay
        weekly_mega_menu_overlay.install(weekly_mega, compact_menu)
    except Exception:
        bot.logger.exception("MEGA_MENU_OVERLAY_INSTALL_FAILED")

    _original_message_for = quiz_alerts._message_for
    _original_quiz_button = quiz_alerts._quiz_button

    def message_for_with_mega(target_day):
        text = str(_original_message_for(target_day) or "")
        if not _is_sunday(target_day):
            return text
        marker = "SUNDAY MEGA QUIZ"
        if marker.lower() in text.lower():
            return text
        return (
            text
            + "\n\n🔥 <b>SUNDAY MEGA QUIZ • ₹5,000 AMAZON PAY VOUCHERS</b>"
            + "\n10 questions • 30 seconds each • free entry"
            + "\n🥇 ₹2,500 • 🥈 ₹1,500 • 🥉 ₹1,000"
            + "\nOpen Sunday <b>10:00 AM–9:00 PM IST</b>."
        )

    def quiz_button_with_mega():
        rows = [list(row) for row in (_original_quiz_button() or [])]
        if not any(
            "Mega Quiz" in str(button.get("text") or "")
            for row in rows
            for button in row
            if isinstance(button, dict)
        ):
            # Put the weekly event directly below the Daily Quiz button. This is
            # only another action in the SAME safe reminder; no second DM is sent.
            insert_at = 1 if rows else 0
            rows.insert(insert_at, [{
                "text": "🔥 Sunday Mega Quiz • ₹5,000",
                "url": str(weekly_mega.BOT_DEEPLINK),
            }])
        return rows

    quiz_alerts._message_for = message_for_with_mega
    quiz_alerts._quiz_button = quiz_button_with_mega
    quiz_alerts._sunday_mega_reminder_overlay_installed = True
    _installed = True

    bot.logger.warning(
        "MEGA_PRIVATE_REMINDER_OVERLAY active=on officialbot=same_daily_queue "
        "business=same_7d_policy extra_customer_dm=off sunday_text=on "
        "mega_button=on pacing_unchanged=on eligibility_unchanged=on"
    )
