"""Keep new Business enquiry alerts active while normal follow-ups stay quiet.

The V85 handler already sends one alert for a new enquiry and escalates reopened
or attention-needed conversations. The consolidated daily report handles the
summary, so this installer does not replace the V85 new-lead sender.
"""
import bot
import v85_silent_business_inbox as v85

_installed = False


def install():
    global _installed
    if not _installed:
        _installed = True
        bot.logger.warning(
            "BUSINESS_ALERT_POLICY new_lead_popup=on normal_followups=silent_inbox "
            "attention_alerts=on reopened_alerts=on auto_reply=unchanged"
        )
    return v85._send_new_lead_alert
