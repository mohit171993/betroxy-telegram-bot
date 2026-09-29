"""Real python-telegram-bot compatibility checks without Telegram or DB I/O."""

import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, User
from telegram.ext import ApplicationBuilder, ApplicationHandlerStop

import betroxy_mode as mode
import reward_receipt_confirmation as receipt


class RealPTBModeTests(unittest.TestCase):
    def setUp(self):
        self.app = ApplicationBuilder().token("123456:FAKE_LOCAL_TOKEN").build()
        # CommandHandler needs a bot username; initializing ExtBot would make
        # a live getMe request, so provide only the local identity it reads.
        self.app.bot._bot_user = User(
            id=123456, is_bot=True, first_name="Betroxy", username="BetroxyOfficialBot"
        )
        mode._bot = SimpleNamespace(
            ADMIN_ID=123,
            InlineKeyboardButton=InlineKeyboardButton,
            InlineKeyboardMarkup=InlineKeyboardMarkup,
        )
        mode._mode = "quiz"
        mode._mode_checked_at = time.monotonic()
        mode._install_application(self.app)

    def tearDown(self):
        mode._bot = None
        mode._mode = "full"

    def _message_update(self, text, kind="message", uid=999):
        message = {
            "message_id": 1,
            "date": 1690000000,
            "chat": {"id": uid, "type": "private"},
            "from": {"id": uid, "is_bot": False, "first_name": "Tester"},
            "text": text,
        }
        if text.startswith("/"):
            command = text.split(None, 1)[0]
            message["entities"] = [
                {"type": "bot_command", "offset": 0, "length": len(command)}
            ]
        if kind == "business_message":
            message["business_connection_id"] = "local-biz"
        return Update.de_json({"update_id": 1, kind: message}, self.app.bot)

    def test_real_handlers_match_private_command_and_exclude_business_message(self):
        regular = self._message_update("/mode status")
        business = self._message_update("/mode status", "business_message")

        self.assertTrue(self.app.handlers[-30002][0].check_update(regular))
        self.assertTrue(self.app.handlers[-30000][0].check_update(regular))
        self.assertFalse(self.app.handlers[-30000][0].check_update(business))

    def test_verified_legacy_command_stops_before_full_router(self):
        update = self._message_update("/affiliate")
        send = AsyncMock()
        with patch("betroxy_universal_verification.is_verified", return_value=True), patch.object(
            type(update.message), "reply_text", send
        ):
            with self.assertRaises(ApplicationHandlerStop):
                asyncio.run(mode._message_guard(update, None))
        send.assert_awaited_once()
        self.assertEqual(send.await_args.args[0], mode.QUIZ_HOME_TEXT)

    def test_receipt_edit_does_not_republish_old_product_button_in_quiz(self):
        acknowledgement = [InlineKeyboardButton("Voucher confirmed", callback_data="reward_received:1")]
        old_product_row = [InlineKeyboardButton("Open App", url="https://betroxy.com/")]

        mode._mode = "quiz"
        self.assertEqual(receipt._confirmation_markup_rows(acknowledgement, [old_product_row]), [acknowledgement])

        mode._mode = "full"
        self.assertEqual(
            receipt._confirmation_markup_rows(acknowledgement, [old_product_row]),
            [acknowledgement, old_product_row],
        )


if __name__ == "__main__":
    unittest.main()
