"""Add a direct public-channel subscription CTA to BETROXY quiz reminders.

This is a conversion-only overlay. It does not change who receives reminders,
Business eligibility, the 7-day Business cooldown, customer pacing, or retry
safety. The same reminder now gives users an easy one-tap route to the public
updates channel in addition to the Daily Quiz CTA.
"""
import bot

CHANNEL_HANDLE = "@betroxyupdates"
CHANNEL_URL = "https://t.me/betroxyupdates"

_installed = False
_original_message_for = None
_original_quiz_button = None


def install(quiz_alerts):
    global _installed, _original_message_for, _original_quiz_button
    if _installed:
        return

    _original_message_for = quiz_alerts._message_for
    _original_quiz_button = quiz_alerts._quiz_button

    def _message_for_with_channel(target_day):
        text = str(_original_message_for(target_day) or "")
        if CHANNEL_HANDLE.lower() not in text.lower():
            text += (
                "\n\n📢 <b>Join @betroxyupdates</b> for quiz opening, "
                "last-call and winner/result updates."
            )
        return text

    def _quiz_button_with_channel():
        rows = [list(row) for row in (_original_quiz_button() or [])]
        rows.append([{
            "text": "📢 Join BETROXY Updates",
            "url": CHANNEL_URL,
        }])
        return rows

    quiz_alerts._message_for = _message_for_with_channel
    quiz_alerts._quiz_button = _quiz_button_with_channel
    quiz_alerts.UPDATES_CHANNEL_URL = CHANNEL_URL
    quiz_alerts._channel_subscription_cta_installed = True
    _installed = True

    bot.logger.warning(
        "CHANNEL_SUBSCRIPTION_CTA active=on channel=%s reminder_text=on "
        "direct_join_button=on officialbot=on business_weekly=on "
        "customer_pacing_unchanged=on business_7d_unchanged=on",
        CHANNEL_HANDLE,
    )
