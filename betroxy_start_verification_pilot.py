"""Connect the pinned BETROXY tester's /start to real mobile verification.

No startup sends/resets, new scheduler or new poller. Non-test accounts and
legacy callbacks are untouched. After an explicit self-contact share, reuse
the existing quiz contact saver and resume the exact original /start payload.
The separate /verifytest command remains available without changing live data.
"""
from __future__ import annotations

import asyncio
import logging
import time

from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import CommandHandler, MessageHandler, ApplicationHandlerStop, filters

from betroxy_crm_store import own_contact, phone

log = logging.getLogger(__name__)
PENDING = "btx_start_verification_pending"
TTL_SECONDS = 600


def keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("✅ VERIFY & CONTINUE", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Tap VERIFY & CONTINUE",
    )


class StartVerificationPilot:
    def __init__(self, ui, verifier):
        self.ui = ui
        self.verifier = verifier
        self.lock = asyncio.Lock()

    def is_tester(self, update):
        user, chat, message = update.effective_user, update.effective_chat, update.effective_message
        return bool(
            user and chat and message
            and self.ui.store.test_uid is not None
            and int(user.id) == int(self.ui.store.test_uid)
            and str(chat.type) == "private"
            and int(chat.id) == int(user.id)
            and not getattr(message, "business_connection_id", None)
        )

    async def start(self, update, context):
        if not self.is_tester(update):
            return
        uid = int(update.effective_user.id)
        async with self.lock:
            context.user_data.pop(PENDING, None)
            try:
                verified = await asyncio.to_thread(self.verifier._verified_mobile, uid)
                busy = await asyncio.to_thread(self.ui.store.test_busy) if not verified else False
            except Exception as exc:
                log.warning("BTX_START_VERIFY_LOOKUP_FAILED uid=%s error_type=%s", uid, type(exc).__name__)
                await update.effective_message.reply_text("Verification could not be checked. Please send /start again shortly.")
                raise ApplicationHandlerStop
            if verified:
                log.info("BTX_START_VERIFY_PASS uid=%s existing_verified=on", uid)
                return  # The original /start handler receives this update normally.
            if busy:
                await update.effective_message.reply_text("Please finish the current quiz question, then send /start to verify your mobile.")
                raise ApplicationHandlerStop
            context.user_data[PENDING] = {
                "expires": time.monotonic() + TTL_SECONDS,
                "start_update": update.to_dict(),
                "args": list(context.args or []),
            }
            await update.effective_message.reply_text(
                "📱 Mobile verification required\n\n"
                "Before continuing, share the mobile number linked to your Telegram account.\n\n"
                "Tap ✅ VERIFY & CONTINUE below. After verification, your existing BETROXY menu or requested page will open.\n\n"
                "Your number will be saved for your account and rewards. This does not change your marketing preferences.\n"
                "Use /cancelcrm or /stop to cancel.",
                reply_markup=keyboard(),
            )
            log.warning("BTX_START_VERIFY_PROMPT_SENT uid=%s scope=pinned_tester old_welcome_deferred=on", uid)
        raise ApplicationHandlerStop

    async def contact(self, update, context):
        if not self.is_tester(update) or not context.user_data.get(PENDING):
            return
        uid = int(update.effective_user.id)
        message = update.effective_message
        async with self.lock:
            state = context.user_data.get(PENDING)
            if not state:
                raise ApplicationHandlerStop  # Another concurrent reply already completed it.
            if time.monotonic() > state["expires"]:
                context.user_data.pop(PENDING, None)
                await message.reply_text("Verification expired. Send /start to try again.", reply_markup=ReplyKeyboardRemove())
                raise ApplicationHandlerStop
            if str(message.text or "").strip().lower() == "cancel":
                context.user_data.pop(PENDING, None)
                await message.reply_text("Verification cancelled. Your saved records were not changed.", reply_markup=ReplyKeyboardRemove())
                raise ApplicationHandlerStop
            contact = message.contact
            try:
                valid = bool(contact and own_contact(uid, contact.user_id, contact.phone_number))
            except (TypeError, ValueError):
                valid = False
            if not valid:
                await message.reply_text("Please tap ✅ VERIFY & CONTINUE and share your own Telegram-linked contact. Typing a number or sharing another person's contact does not verify your account.", reply_markup=keyboard())
                raise ApplicationHandlerStop
            normalized = phone(contact.phone_number)
            try:
                original = Update.de_json(state["start_update"], context.bot)
                if not self.is_tester(original):
                    raise ValueError("Invalid original test identity")
                saved = await asyncio.to_thread(self.verifier._save_verification, uid, normalized)
                current = await asyncio.to_thread(self.verifier._verified_mobile, uid)
                if phone(saved) != normalized or phone(current) != normalized:
                    raise ValueError("Verification was not persisted")
            except Exception as exc:
                log.warning("BTX_START_VERIFY_SAVE_FAILED uid=%s error_type=%s", uid, type(exc).__name__)
                await message.reply_text("Could not complete verification. Please try the verification button again. No prize was issued.", reply_markup=keyboard())
                raise ApplicationHandlerStop

            # Optional pilot evidence, separate from real profile verification.
            try:
                await asyncio.to_thread(self.ui.store.save_test, uid, contact.user_id, normalized)
            except Exception as exc:
                log.warning("BTX_START_VERIFY_AUDIT_UNAVAILABLE uid=%s error_type=%s", uid, type(exc).__name__)
            if context.user_data.get(PENDING) is not state:
                raise ApplicationHandlerStop  # A concurrent STOP/cancel must not be undone.
            context.user_data.pop(PENDING, None)
            context.user_data.pop("btxverify_pending", None)
            await message.reply_text("✅ Mobile verified. Continuing to BETROXY…", reply_markup=ReplyKeyboardRemove())
            old_args = context.args
            try:
                context.args = list(state["args"])
                # Use the final installed legacy router, not a replacement menu.
                await self.ui.bot.start(original, context)
            finally:
                context.args = old_args
            log.warning("BTX_START_VERIFY_COMPLETED uid=%s source=telegram_contact original_start_resumed=on", uid)
        raise ApplicationHandlerStop

    def register(self, app):
        if app.bot_data.get("btx_start_verification_installed"):
            return
        normal = filters.ChatType.PRIVATE & ~filters.UpdateType.BUSINESS_MESSAGE
        # Existing CRM clearing middleware is at -10004. Legacy quiz/contact
        # handlers are later; a handled verification update stops propagation.
        app.add_handler(CommandHandler("start", self.start, filters=normal), group=-10003)
        app.add_handler(MessageHandler(normal & (filters.CONTACT | (filters.TEXT & ~filters.COMMAND)), self.contact), group=-10003)
        app.bot_data["btx_start_verification_installed"] = True
        log.warning("BTX_START_VERIFY_READY scope=pinned_tester uid=%s start_prompt=on resume_original_payload=on other_users=unchanged sms_otp=off startup_sends=off", self.ui.store.test_uid)


def attach(ui, verifier=None):
    if getattr(ui, "_start_verification_pilot", None) is not None:
        return ui._start_verification_pilot
    if verifier is None:
        import quiz_mobile_verification_overlay as verifier
    pilot = StartVerificationPilot(ui, verifier)
    old_register = ui.register
    old_clear = ui.clear_pending
    old_verify_begin = ui.verify_begin

    async def clear_pending(update, context):
        context.user_data.pop(PENDING, None)
        return await old_clear(update, context)

    async def verify_begin(update, context):
        if pilot.is_tester(update):
            context.user_data.pop(PENDING, None)
        return await old_verify_begin(update, context)

    def register(app):
        old_register(app)
        pilot.register(app)

    ui.clear_pending = clear_pending
    ui.verify_begin = verify_begin
    ui.register = register
    ui._start_verification_pilot = pilot
    return pilot
