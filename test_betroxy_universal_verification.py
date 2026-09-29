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

    class Filter:
        def __and__(self, _other):
            return self

        def __invert__(self):
            return self

    ext.filters = SimpleNamespace(
        ChatType=SimpleNamespace(PRIVATE=Filter()),
        UpdateType=SimpleNamespace(BUSINESS_MESSAGE=Filter()),
    )
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
        self.menus, self.sent, self.commands, self.descriptions = [], [], [], []

    async def set_chat_menu_button(self, **kwargs):
        self.menus.append(kwargs)

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(message_id=7)

    async def set_my_commands(self, commands):
        self.commands.append(commands)

    async def set_my_description(self, description):
        self.descriptions.append(description)

    async def set_my_short_description(self, short_description):
        self.descriptions.append(short_description)


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
        self.assertNotRegex(copy, re.compile(r"sports|betting|gambl|casino|odds|wager|18\+|ibetin\.com", re.I))
        self.assertEqual(len(msg.replies), 2)

    async def _answer(self):
        return None

    def test_unverified_contact_button_persists_across_start_and_hi(self):
        for text in ("/start", "hi"):
            with self.subTest(text=text):
                msg = FakeMessage(text)
                with self.assertRaises(StopProcessing):
                    asyncio.run(self.gate._message(update(1456774567, msg), self.context))
                keyboard = msg.replies[0][1]["reply_markup"]
                self.assertTrue(keyboard.kwargs["is_persistent"])
                self.assertFalse(keyboard.kwargs["one_time_keyboard"])
                button = keyboard.args[0][0][0]
                self.assertEqual(button.args[0], "📱 VERIFY & CONTINUE")
                self.assertTrue(button.kwargs["request_contact"])
                self.assertIn("VERIFY & CONTINUE", msg.replies[0][0])

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
        self.assertNotRegex(copy, re.compile(r"sports|betting|gambl|casino|odds|wager|18\+|ibetin\.com", re.I))
        self.assertIn("verify", copy.lower())

    def test_outbound_and_global_profile_do_not_expose_unverified_routes(self):
        safe = ModuleType("safe_reminder_delivery")
        sent = []
        safe.send_claimed_result = lambda uid, *args, **kwargs: sent.append(uid) or {"sent": True}
        previous = []

        async def previous_post_init(_app):
            previous.append(True)

        bot_module = SimpleNamespace(
            post_init=previous_post_init,
            APP_URL="https://betroxy.com",
            BOT_USERNAME="BetroxyBot",
        )
        with patch.dict(sys.modules, {"safe_reminder_delivery": safe}):
            self.gate.install(bot_module, self.verifier)
        self.assertEqual(safe.send_claimed_result(1456774567, "quiz", "key", "content")["status"], "verification_required")
        self.assertEqual(sent, [])
        self.verifier.verified[1456774567] = "+919876543210"
        self.assertTrue(safe.send_claimed_result(1456774567, "quiz", "key", "content")["sent"])
        self.assertEqual(sent, [1456774567])

        handlers = []
        app = SimpleNamespace(bot=self.bot, add_handler=lambda handler, group: handlers.append(group))
        asyncio.run(bot_module.post_init(app))
        self.assertEqual(previous, [True])
        self.assertEqual(handlers, [-20000, -20000])
        self.assertNotRegex(" ".join(self.bot.descriptions), re.compile(r"sports|betting|gambl|casino|odds|wager|18\+|ibetin\.com", re.I))


if __name__ == "__main__":
    unittest.main()
