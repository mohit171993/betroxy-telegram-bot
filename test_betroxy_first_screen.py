"""Offline presentation tests. No Telegram login or database access."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

from telegram.ext import ApplicationHandlerStop
from betroxy_start_verification_pilot import StartVerificationPilot, PENDING


class FirstScreenTests(unittest.IsolatedAsyncioTestCase):
    async def screen(self):
        store = NS(test_uid=2, test_busy=MagicMock(return_value=False))
        ui = NS(store=store)
        verifier = NS(_verified_mobile=MagicMock(return_value=None))
        pilot = StartVerificationPilot(ui, verifier)
        message = NS(reply_text=AsyncMock(), business_connection_id=None)
        update = NS(
            effective_user=NS(id=2),
            effective_chat=NS(id=2, type="private"),
            effective_message=message,
            to_dict=MagicMock(return_value={"update_id": 1}),
        )
        context = NS(user_data={}, args=["dailyquiz"])
        with self.assertRaises(ApplicationHandlerStop):
            await pilot.start(update, context)
        message.reply_text.assert_awaited_once()
        return message.reply_text.call_args, context

    async def test_compact_copy_identifies_actual_service(self):
        call, _ = await self.screen()
        text = call.args[0]
        self.assertLessEqual(len(text), 320)
        self.assertTrue(text.startswith("📱 <b>Mobile verification required</b>"))
        self.assertIn("<b>BETROXY</b>", text)
        self.assertIn("Sportsbook · Daily Quiz · Rewards", text)
        self.assertNotIn("Live scores", text)
        self.assertNotIn("Match Pulse", text)
        self.assertEqual(call.kwargs["parse_mode"], "HTML")
        self.assertEqual(text.count("<b>"), text.count("</b>"))

    async def test_privacy_and_cancellation_copy_preserved(self):
        call, context = await self.screen()
        text = call.args[0]
        self.assertIn("Saved for your account and rewards", text)
        self.assertIn("Marketing preferences stay unchanged", text)
        self.assertIn("/cancelcrm or /stop", text)
        self.assertEqual(context.user_data[PENDING]["args"], ["dailyquiz"])

    async def test_one_existing_contact_button_no_new_destination(self):
        call, _ = await self.screen()
        markup = call.kwargs["reply_markup"]
        self.assertEqual(len(markup.keyboard), 1)
        self.assertEqual(len(markup.keyboard[0]), 1)
        button = markup.keyboard[0][0]
        self.assertEqual(button.text, "✅ VERIFY & CONTINUE")
        self.assertTrue(button.request_contact)
        self.assertIsNone(button.web_app)
        self.assertTrue(markup.one_time_keyboard)
        self.assertTrue(markup.resize_keyboard)


if __name__ == "__main__":
    unittest.main()
