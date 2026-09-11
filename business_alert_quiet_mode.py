"""Quiet normal Telegram Business lead alerts without changing lead handling.

Normal new leads are still stored and auto-replied to, but the admin is not
interrupted with a NEW BUSINESS LEAD Telegram card. Attention/reopened alerts
remain enabled in v85_silent_business_inbox.
"""
import bot
import v85_silent_business_inbox as v85

_installed = False


async def _silent_new_lead_alert(context, enquiry, intent, inbound_preview, auto_replied):
    bot.logger.info(
        "BUSINESS_NEW_LEAD_SILENT enquiry_id=%s intent=%s auto_replied=%s inbox=stored admin_popup=off",
        enquiry.get("id"), intent, auto_replied,
    )
    return None


def install():
    global _installed
    if _installed:
        return _silent_new_lead_alert
    v85._send_new_lead_alert = _silent_new_lead_alert
    _installed = True
    bot.logger.warning(
        "BUSINESS_ALERT_POLICY new_lead_popup=off normal_leads=silent_inbox "
        "attention_alerts=on reopened_alerts=on auto_reply=unchanged"
    )
    return _silent_new_lead_alert
