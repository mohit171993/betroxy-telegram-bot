"""Gate private Betroxy customer routes behind Telegram self-contact verification.

Existing quiz verification storage remains the authority. This adds no OTP,
marketing consent, background worker, or automatic customer send.
"""

import asyncio
import inspect
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, MenuButtonCommands, MenuButtonWebApp, ReplyKeyboardMarkup, ReplyKeyboardRemove, WebAppInfo
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler, MessageHandler, filters

from betroxy_crm_store import own_contact, phone


LOGGER = logging.getLogger(__name__)
_verifier = None
_bot = None
_outbound_installed = False


def is_verified(uid):
    """Fail closed unless the live verified contact matches the saved contact."""
    try:
        return bool(uid is not None and _verifier and _verifier._verified_mobile(int(uid)))
    except Exception:
        LOGGER.exception("BTX_ACCOUNT_VERIFY_LOOKUP_FAILED uid=%s", uid)
        return False


async def neutral_business_reply(context, enquiry, intent, inbox, conversion):
    """Keep the Business lead record and acknowledgement without product copy."""
    url = f"https://t.me/{str(_bot.BOT_USERNAME).lstrip('@')}?start=verify"
    text = "To continue, verify your Telegram account in the official bot."
    try:
        sent = await context.bot.send_message(
            chat_id=int(enquiry["customer_chat_id"]),
            text=text,
            business_connection_id=str(enquiry["connection_id"]),
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Verify account", url=url)]]
            ),
            disable_web_page_preview=True,
        )
    except Exception:
        text += f"\n{url}"
        sent = await context.bot.send_message(
            chat_id=int(enquiry["customer_chat_id"]),
            text=text,
            business_connection_id=str(enquiry["connection_id"]),
            disable_web_page_preview=True,
        )
    if not enquiry.get("auto_ack_sent_at"):
        inbox._mark_auto_ack(enquiry["id"])
    inbox._record_outbound(enquiry["id"], getattr(sent, "message_id", None), text)
    return conversion._update_lead_state(
        enquiry["id"], intent=intent, stage=None, auto_replied=True
    ) or enquiry


def _keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("Verify my account", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=False,
        is_persistent=True,
        input_field_placeholder="Share your own Telegram contact",
    )


PROMPT = (
    "To continue, verify your Telegram account. Tap the button below to share "
    "your own Telegram-linked contact. A typed number or someone else's contact "
    "cannot verify your account."
)


async def _verified_menu(context, uid):
    """Give the existing app menu only to a verified private chat."""
    if context.user_data.get("btx_verified_menu_ready"):
        return
    try:
        await context.bot.set_chat_menu_button(
            chat_id=int(uid),
            menu_button=MenuButtonWebApp(text="Open App", web_app=WebAppInfo(url=_bot.APP_URL)),
        )
        context.user_data["btx_verified_menu_ready"] = True
    except Exception:
        LOGGER.exception("BTX_VERIFIED_MENU_UNAVAILABLE uid=%s", uid)


async def _message(update, context):
    user, chat, message = update.effective_user, update.effective_chat, update.effective_message
    if not user or not chat or not message or str(chat.type) != "private":
        return
    uid = int(user.id)
    if is_verified(uid):
        await _verified_menu(context, uid)
        return

    words = str(message.text or "").split(None, 1)
    command = words[0].split("@", 1)[0].lower() if words else ""
    if command in {"/stop", "/cancel", "/cancelcrm"}:
        return

    contact = getattr(message, "contact", None)
    if contact is not None:
        try:
            valid = own_contact(uid, contact.user_id, contact.phone_number)
        except (TypeError, ValueError):
            valid = False
        if valid:
            number = phone(contact.phone_number)
            try:
                saved = await asyncio.to_thread(_verifier._save_verification, uid, number)
                current = await asyncio.to_thread(_verifier._verified_mobile, uid)
                if phone(saved) != number or phone(current) != number:
                    raise RuntimeError("Telegram contact verification did not persist")
            except Exception:
                LOGGER.exception("BTX_ACCOUNT_VERIFY_SAVE_FAILED uid=%s", uid)
                await message.reply_text(
                    "Verification could not be completed. Please try again.",
                    reply_markup=_keyboard(),
                )
                raise ApplicationHandlerStop
            await _verified_menu(context, uid)
            await message.reply_text(
                "Account verified. Send /start to continue.",
                reply_markup=ReplyKeyboardRemove(),
            )
            LOGGER.warning("BTX_ACCOUNT_VERIFY_COMPLETED uid=%s method=telegram_self_contact", uid)
            raise ApplicationHandlerStop

    await message.reply_text(PROMPT, reply_markup=_keyboard())
    LOGGER.info("BTX_ACCOUNT_VERIFY_PROMPT uid=%s route=message", uid)
    raise ApplicationHandlerStop


async def _callback(update, context):
    query = update.callback_query
    if query is None:
        return
    user, chat = update.effective_user, update.effective_chat
    if not user or not chat or str(chat.type) != "private":
        return
    uid = int(user.id)
    if is_verified(uid):
        await _verified_menu(context, uid)
        return
    await query.answer()
    if query.message:
        await query.message.reply_text(PROMPT, reply_markup=_keyboard())
    LOGGER.info("BTX_ACCOUNT_VERIFY_PROMPT uid=%s route=callback", uid)
    raise ApplicationHandlerStop


async def _profile_and_handlers(app):
    normal = filters.ChatType.PRIVATE & ~filters.UpdateType.BUSINESS_MESSAGE
    app.add_handler(MessageHandler(normal, _message), group=-20000)
    app.add_handler(CallbackQueryHandler(_callback), group=-20000)
    try:
        await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        await app.bot.set_my_commands([("start", "Verify or open your account")])
        await app.bot.set_my_description(
            "Betroxy account access and support. Verify your Telegram-linked contact in the bot to continue."
        )
        await app.bot.set_my_short_description("Secure account access and support.")
        LOGGER.warning("BTX_ACCOUNT_GATE_READY private_messages=on private_callbacks=on public_profile=neutral global_menu=commands")
    except Exception:
        LOGGER.exception("BTX_ACCOUNT_GATE_PROFILE_UPDATE_FAILED")


def install(bot_module, verifier):
    """Install before production.main without importing any late callback layers."""
    global _verifier, _bot, _outbound_installed
    _verifier, _bot = verifier, bot_module
    previous_post_init = bot_module.post_init

    async def gated_post_init(app):
        result = previous_post_init(app)
        if inspect.isawaitable(result):
            await result
        await _profile_and_handlers(app)

    bot_module.post_init = gated_post_init

    if not _outbound_installed:
        import safe_reminder_delivery

        previous_send = safe_reminder_delivery.send_claimed_result

        def verified_send(user_id, *args, **kwargs):
            if not is_verified(user_id):
                LOGGER.info("BTX_ACCOUNT_GATE_OUTBOUND_SKIPPED uid=%s", user_id)
                return {
                    "sent": False, "status": "verification_required", "retried": 0,
                    "permanent": False, "rate_limited": False,
                }
            return previous_send(user_id, *args, **kwargs)

        safe_reminder_delivery.send_claimed_result = verified_send
        _outbound_installed = True
