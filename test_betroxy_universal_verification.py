import asyncio
import importlib.util
import re
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch


class StopProcessing(Exception):
    pass


def load_gate():
    telegram = ModuleType("telegram")
    telegram.__path__ = []

    class Markup:
        def __init__(self, *args, **kwargs):
            self.args, self.kwargs = args, kwargs

    for name in (
        "InlineKeyboardButton", "InlineKeyboardMarkup", "KeyboardButton",
        "MenuButtonCommands", "MenuButtonWebApp", "ReplyKeyboardMarkup",
        "ReplyKeyboardRemove", "WebAppInfo",
    ):
        setattr(telegram, name, Markup)
    ext = ModuleType("telegram.ext")
    ext.ApplicationHandlerStop = StopProcessing
    ext.CallbackQueryHandler = Markup
    ext.MessageHandler = Markup
    ext.filters = SimpleNamespace()
    spec = importlib.util.spec_from_file_location(
        "isolated_betroxy_universal_verification",
        Path(__file__).with_name("betroxy_universal_verification.py"),
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"telegram": telegram, "telegram.ext": ext}):
        spec.loader.exec_module(module)
    return module


class FakeMessage:
    def __init__(self, text=None, contact=None):
        self.text, self.contact, self.replies = text, contact, []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))


class FakeBot:
    def __init__(self):
        self.menus, self.sent = [], []

    async def set_chat_menu_button(self, **kwargs):
        self.menus.append(kwargs)

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(message_id=7)


class Verifier:
    def __init__(self):
        self.verified = {}

    def _verified_mobile(self, uid):
        return self.verified.get(uid)

    def _save_verification(self, uid, number):
        self.verified[uid] = number
        return number


def update(uid, message=None, callback=None):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=uid),
        effective_chat=SimpleNamespace(type="private", id=uid),
        effective_message=message,
        callback_query=callback,
    )


class BetroxyUniversalVerificationTests(unittest.TestCase):
    def setUp(self):
        self.gate = load_gate()
        self.verifier = Verifier()
        self.gate._verifier = self.verifier
        self.gate._bot = SimpleNamespace(APP_URL="https://betroxy.com", BOT_USERNAME="BetroxyBot")
        self.bot = FakeBot()
        self.context = SimpleNamespace(bot=self.bot, user_data={})

    def test_revoked_admin_start_and_old_button_are_gated_with_neutral_copy(self):
        msg = FakeMessage("/start")
        with self.assertRaises(StopProcessing):
            asyncio.run(self.gate._message(update(1456774567, msg), self.context))
        query = SimpleNamespace(message=msg, answer=self._answer)
        with self.assertRaises(StopProcessing):
            asyncio.run(self.gate._callback(update(1456774567, callback=query), self.context))
        copy = " ".join(text for text, _ in msg.replies)
        self.assertNotRegex(copy, re.compile(r"sports|betting|gambl|casino|odds|wager|18\+", re.I))
        self.assertEqual(len(msg.replies), 2)

    async def _answer(self):
        return None

    def test_only_self_contact_unlocks_existing_routes(self):
        uid = 1456774567
        wrong = FakeMessage(contact=SimpleNamespace(user_id=2, phone_number="+919876543210"))
        with self.assertRaises(StopProcessing):
            asyncio.run(self.gate._message(update(uid, wrong), self.context))
        self.assertFalse(self.gate.is_verified(uid))
        own = FakeMessage(contact=SimpleNamespace(user_id=uid, phone_number="+919876543210"))
        with self.assertRaises(StopProcessing):
            asyncio.run(self.gate._message(update(uid, own), self.context))
        self.assertEqual(self.gate.is_verified(uid), True)
        self.assertEqual(len(self.bot.menus), 1)
        start = FakeMessage("/start")
        asyncio.run(self.gate._message(update(uid, start), self.context))
        self.assertEqual(start.replies, [])

    def test_business_reply_uses_only_verification_link(self):
        inbox = SimpleNamespace(_mark_auto_ack=lambda *_: None, _record_outbound=lambda *_: None)
        conversion = SimpleNamespace(_update_lead_state=lambda *_args, **_kwargs: {"id": 1})
        enquiry = {"id": 1, "customer_chat_id": 4, "connection_id": "abc", "auto_ack_sent_at": None}
        asyncio.run(self.gate.neutral_business_reply(self.context, enquiry, "general", inbox, conversion))
        copy = self.bot.sent[0]["text"]
        self.assertNotRegex(copy, re.compile(r"sports|betting|gambl|casino|odds|wager|18\+", re.I))
        self.assertIn("verify", copy.lower())


if __name__ == "__main__":
    unittest.main()

