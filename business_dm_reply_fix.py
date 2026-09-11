"""Production hotfix: explicit Business DM greetings must always receive a bot menu reply."""
import time

import bot
from telegram.ext import ApplicationHandlerStop
import v85_silent_business_inbox as v85

v49 = v85.v49
biz51 = v85.biz51

# Prevent rapid repeated greetings from producing duplicate replies while still
# allowing an existing customer to request the menu again later.
_GREETING_COOLDOWN_SECONDS = 20
_last_greeting_reply = {}


def _explicit_menu_request(text):
    t = v85._clean_text(text)
    return t in {
        "/start", "start", "menu", "/menu",
        "hi", "hello", "hey", "hii", "hiii", "hello sir", "hi sir",
        "good morning", "good afternoon", "good evening", "namaste", "namaskar",
    }


def _greeting_cooldown_ok(chat_id):
    now = time.monotonic()
    previous = _last_greeting_reply.get(int(chat_id), 0.0)
    if now - previous < _GREETING_COOLDOWN_SECONDS:
        return False
    _last_greeting_reply[int(chat_id)] = now
    return True


async def business_message_update_with_menu_reply(update, context):
    message = update.business_message
    if not message:
        return

    connection_id = getattr(message, "business_connection_id", None)
    if not connection_id:
        raise ApplicationHandlerStop

    conn = await v49._ensure_connection_from_message(context, connection_id)
    owner_user_id = (conn or {}).get("owner_user_id")
    sender = getattr(message, "from_user", None)

    if owner_user_id and sender and int(sender.id) == int(owner_user_id):
        raise ApplicationHandlerStop
    if getattr(message, "sender_business_bot", None):
        raise ApplicationHandlerStop

    previous = v85._existing_enquiry(connection_id, message.chat_id)
    was_resolved = bool(previous and previous.get("status") == "resolved")
    inbound_preview = v49._message_preview(message)
    message_type = v49._message_type(message)
    message_text = getattr(message, "text", None) or getattr(message, "caption", None) or ""

    try:
        enquiry = v49._upsert_incoming_enquiry(connection_id, message)
    except Exception:
        bot.logger.exception("BUSINESS_DM_REPLY_FIX_SAVE_FAILED")
        raise ApplicationHandlerStop

    intent = biz51._detect_intent(message_text)
    enquiry = biz51._update_lead_state(enquiry["id"], intent=intent, stage=None, auto_replied=False) or enquiry

    auto_replied = False
    auto_reply_failed = False
    settings = v49._business_settings()
    explicit_menu = _explicit_menu_request(message_text)

    # The old V85 flow suppresses greetings after auto_ack_sent_at is set. That
    # made /start and hello appear dead for returning customers. Explicit menu
    # requests now bypass that historical-state suppression, with a short local
    # cooldown to avoid accidental duplicate replies.
    should_attempt = False
    if settings.get("auto_ack_enabled"):
        if explicit_menu and _greeting_cooldown_ok(message.chat_id):
            should_attempt = True
        elif biz51._auto_reply_allowed(enquiry):
            should_attempt = (not enquiry.get("auto_ack_sent_at")) or intent not in {"general", "greeting"}

    if should_attempt:
        try:
            enquiry = await biz51._send_smart_reply(context, enquiry, intent)
            auto_replied = True
            if explicit_menu:
                bot.logger.warning(
                    "BUSINESS_DM_MENU_REPLY sent=on chat_id=%s intent=%s text=%s",
                    message.chat_id, intent, v85._clean_text(message_text)[:40],
                )
        except Exception:
            auto_reply_failed = True
            bot.logger.exception("BUSINESS_DM_REPLY_FIX_SEND_FAILED")

    needs_attention, reason, escalation_key = v85._attention_decision(
        message_text, intent, message_type, auto_reply_failed=auto_reply_failed
    )
    if was_resolved and not needs_attention:
        reason = "Customer returned after enquiry was resolved"
        escalation_key = "reopened"

    priority = "attention" if needs_attention else "auto"
    enquiry = v85._update_silent_state(
        enquiry["id"], inbound_preview, priority,
        reason=reason if needs_attention else None,
        escalation_key=escalation_key if needs_attention else None,
    ) or enquiry

    notify_new = previous is None
    notify_reopened = was_resolved
    notify_attention = needs_attention and v85._should_repeat_attention(previous, escalation_key)

    try:
        if notify_attention:
            await v85._send_attention_alert(context, enquiry, intent, inbound_preview, reason, reopened=was_resolved)
            v85._mark_admin_alert(enquiry["id"], "attention", reason, escalation_key)
        elif notify_reopened:
            await v85._send_attention_alert(context, enquiry, intent, inbound_preview, reason, reopened=True)
            v85._mark_admin_alert(enquiry["id"], "attention", reason, "reopened")
        elif notify_new:
            await v85._send_new_lead_alert(context, enquiry, intent, inbound_preview, auto_replied)
            v85._mark_admin_alert(enquiry["id"], "auto", None, None)
        else:
            bot.logger.info(
                "V85_BUSINESS_SILENT_STORED enquiry_id=%s unread=%s intent=%s auto_replied=%s",
                enquiry["id"], int(enquiry.get("silent_unread_count") or 0), intent, auto_replied,
            )
    except Exception:
        bot.logger.exception("BUSINESS_DM_REPLY_FIX_ADMIN_ALERT_FAILED")

    raise ApplicationHandlerStop


def install():
    v49._business_message_update = business_message_update_with_menu_reply
    bot.logger.warning(
        "BUSINESS_DM_REPLY_FIX active=on explicit_start=reply explicit_greeting=reply cooldown=20s"
    )
    return business_message_update_with_menu_reply
