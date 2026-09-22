"""Offline Telegram tests; no bot login, SMS, real users or production DB."""
import time
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

from telegram import Update, User
from telegram.ext import Application, ApplicationHandlerStop, CommandHandler, ExtBot
from betroxy_crm_ui import UI
from betroxy_start_verification_pilot import attach, PENDING

PHONE = "+971501234567"


class StartVerificationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tg = ExtBot("123:TEST_ONLY_NOT_A_REAL_TOKEN")
        object.__setattr__(self.tg, "_bot_user", User(123, "Test", True, username="BetroxyOfficialBot"))
        self.store = MagicMock()
        self.store.test_uid = 2
        self.store.test_busy.return_value = False
        self.core = NS(is_admin=lambda uid: uid == 100, start=AsyncMock())
        self.verifier = NS(_verified_mobile=MagicMock(return_value=None), _save_verification=MagicMock())
        self.ui = UI(self.store, self.core, NS())
        self.pilot = attach(self.ui, self.verifier)
        self.context = NS(user_data={}, args=[], bot=self.tg)
        self.reply_patch = patch("telegram.Message.reply_text", new_callable=AsyncMock)
        self.reply = self.reply_patch.start()
        self.addCleanup(self.reply_patch.stop)

    def update(self, uid=2, text="/start", kind="private", contact=None, update_id=1, business=False):
        message = {"message_id": update_id, "date": 1790064000,
                   "chat": {"id": uid if kind == "private" else -99, "type": kind},
                   "from": {"id": uid, "first_name": "Tester", "is_bot": False, "username": "mohit_97saxena"}}
        if text is not None:
            message["text"] = text
            if text.startswith("/"):
                message["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
        if contact is not None:
            message["contact"] = contact
        if business:
            message["business_connection_id"] = "offline_test_connection"
        return Update.de_json({"update_id": update_id, "message": message}, self.tg)

    def own_contact(self, contact_uid=2, number=PHONE, **kwargs):
        contact = {"phone_number": number, "first_name": "Tester"}
        if contact_uid is not None:
            contact["user_id"] = contact_uid
        return self.update(text=None, contact=contact, update_id=9, **kwargs)

    async def begin(self, payload=""):
        self.context.args = [payload] if payload else []
        original = self.update(text="/start" + (" " + payload if payload else ""))
        with self.assertRaises(ApplicationHandlerStop):
            await self.pilot.start(original, self.context)
        return original

    def configure_success(self):
        def save(uid, number):
            self.verifier._verified_mobile.return_value = number
            return number
        self.verifier._save_verification.side_effect = save

    async def test_unverified_start_prompts_before_legacy_welcome(self):
        await self.begin()
        self.assertIn(PENDING, self.context.user_data)
        self.core.start.assert_not_awaited()
        self.verifier._save_verification.assert_not_called()
        markup = self.reply.call_args.kwargs["reply_markup"]
        self.assertTrue(markup.keyboard[0][0].request_contact)
        self.assertEqual(markup.keyboard[0][0].text, "✅ VERIFY & CONTINUE")

    async def test_verified_start_passes_through_without_prompt(self):
        self.verifier._verified_mobile.return_value = PHONE
        await self.pilot.start(self.update(), self.context)
        self.reply.assert_not_called()
        self.assertNotIn(PENDING, self.context.user_data)

    async def test_username_match_alone_does_not_enable_pilot(self):
        await self.pilot.start(self.update(uid=3), self.context)
        self.verifier._verified_mobile.assert_not_called()
        self.reply.assert_not_called()

    async def test_group_and_business_are_not_intercepted(self):
        await self.pilot.start(self.update(kind="group"), self.context)
        await self.pilot.start(self.update(business=True), self.context)
        self.verifier._verified_mobile.assert_not_called()

    async def test_busy_quiz_not_reset(self):
        self.store.test_busy.return_value = True
        with self.assertRaises(ApplicationHandlerStop):
            await self.pilot.start(self.update(), self.context)
        self.assertNotIn(PENDING, self.context.user_data)
        self.store.change.assert_not_called()
        self.verifier._save_verification.assert_not_called()

    async def test_own_contact_saves_and_resumes_original_payload(self):
        original = await self.begin("dailyquiz")
        self.configure_success()
        observed = []
        async def legacy(update, context):
            observed.append((update.update_id, update.effective_message.text, list(context.args)))
        self.core.start.side_effect = legacy
        self.context.args = ["unrelated_contact_context"]
        with self.assertRaises(ApplicationHandlerStop):
            await self.pilot.contact(self.own_contact(), self.context)
        self.verifier._save_verification.assert_called_once_with(2, PHONE)
        self.store.save_test.assert_called_once_with(2, 2, PHONE)
        self.assertEqual(observed, [(original.update_id, "/start dailyquiz", ["dailyquiz"])])
        self.assertEqual(self.context.args, ["unrelated_contact_context"])
        self.assertNotIn(PENDING, self.context.user_data)
        self.store.change.assert_not_called()

    async def test_referral_payload_retained(self):
        await self.begin("samratking")
        self.assertEqual(self.context.user_data[PENDING]["args"], ["samratking"])

    async def test_typed_foreign_missing_and_bad_contact_rejected(self):
        await self.begin()
        for update in [self.update(text=PHONE), self.own_contact(3), self.own_contact(None), self.own_contact(number="12")]:
            with self.assertRaises(ApplicationHandlerStop):
                await self.pilot.contact(update, self.context)
        self.verifier._save_verification.assert_not_called()
        self.core.start.assert_not_awaited()

    async def test_unrelated_contact_not_consumed(self):
        await self.pilot.contact(self.own_contact(), self.context)
        self.verifier._save_verification.assert_not_called()
        self.reply.assert_not_called()

    async def test_expired_flow_cannot_save(self):
        await self.begin()
        self.context.user_data[PENDING]["expires"] = 0
        with self.assertRaises(ApplicationHandlerStop):
            await self.pilot.contact(self.own_contact(), self.context)
        self.verifier._save_verification.assert_not_called()
        self.assertNotIn(PENDING, self.context.user_data)

    async def test_failed_save_does_not_open_menu(self):
        await self.begin()
        self.verifier._save_verification.return_value = None
        with self.assertRaises(ApplicationHandlerStop):
            await self.pilot.contact(self.own_contact(), self.context)
        self.core.start.assert_not_awaited()
        self.assertIn(PENDING, self.context.user_data)

    async def test_lookup_exception_blocks_only_tester(self):
        self.verifier._verified_mobile.side_effect = RuntimeError("offline")
        with self.assertRaises(ApplicationHandlerStop):
            await self.pilot.start(self.update(), self.context)
        await self.pilot.start(self.update(uid=3), self.context)
        self.core.start.assert_not_awaited()

    async def test_stop_clears_pending_without_reset(self):
        await self.begin()
        await self.ui.clear_pending(self.update(text="/stop"), self.context)
        self.assertNotIn(PENDING, self.context.user_data)
        await self.pilot.contact(self.own_contact(), self.context)
        self.verifier._save_verification.assert_not_called()

    async def test_plain_cancel_clears_pending(self):
        await self.begin()
        with self.assertRaises(ApplicationHandlerStop):
            await self.pilot.contact(self.update(text="Cancel"), self.context)
        self.assertNotIn(PENDING, self.context.user_data)
        self.verifier._save_verification.assert_not_called()

    async def test_verifytest_remains_separate(self):
        await self.begin()
        with self.assertRaises(ApplicationHandlerStop):
            await self.ui.verify_begin(self.update(text="/verifytest"), self.context)
        self.assertNotIn(PENDING, self.context.user_data)
        self.assertIn("btxverify_pending", self.context.user_data)
        self.verifier._save_verification.assert_not_called()

    async def test_registration_is_idempotent(self):
        app = Application.builder().bot(self.tg).build()
        self.ui.register(app)
        count = sum(len(v) for v in app.handlers.values())
        self.ui.register(app)
        self.assertEqual(count, sum(len(v) for v in app.handlers.values()))
        self.assertIs(attach(self.ui, self.verifier), self.pilot)

    async def test_real_handler_order_and_stop_passthrough(self):
        app = Application.builder().bot(self.tg).build()
        legacy_start, legacy_stop = AsyncMock(), AsyncMock()
        app.add_handler(CommandHandler("start", legacy_start), group=0)
        app.add_handler(CommandHandler("stop", legacy_stop), group=0)
        self.ui.register(app)
        # Unit harness: permit dispatch without initializing Telegram/network.
        app._initialized = True
        await app.process_update(self.update())
        legacy_start.assert_not_awaited()
        self.assertIn(PENDING, app.user_data[2])
        await app.process_update(self.update(text="/stop", update_id=2))
        legacy_stop.assert_awaited_once()
        self.assertNotIn(PENDING, app.user_data[2])
        await app.process_update(self.update(uid=3, update_id=3))
        self.assertEqual(legacy_start.await_count, 1)
        self.verifier._verified_mobile.return_value = PHONE
        await app.process_update(self.update(update_id=4))
        self.assertEqual(legacy_start.await_count, 2)


if __name__ == "__main__":
    unittest.main()
