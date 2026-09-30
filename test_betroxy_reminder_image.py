"""Daily quiz reminder DMs lead with the reminder image; text and buttons stay the same."""

import importlib.util
import json
import sys
import unittest
from datetime import date
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch


def load_alerts():
    text_sends = []

    def tg_send(chat_id, text, keyboard=None, business_connection_id=None):
        text_sends.append((chat_id, text, keyboard, business_connection_id))
        return True, {"ok": True, "result": {"message_id": 1}}

    v83 = SimpleNamespace(TG_API="https://api.telegram.invalid/botTEST", _tg_send=tg_send, TZ_OFFSET=0)
    v110 = SimpleNamespace(v83=v83, TZ_OFFSET=0, CHANNEL_CHAT="@test")
    bot = ModuleType("bot")
    bot.logger = SimpleNamespace(warning=lambda *a, **k: None, exception=lambda *a, **k: None)
    bot.BOT_USERNAME = "BetroxyOfficialBot"
    schedule = ModuleType("daily_quiz_schedule")
    schedule.v110 = v110
    safe = ModuleType("safe_reminder_delivery")
    safe._original_tg_send = None

    def install(v):
        safe._original_tg_send = v._tg_send

    def send_claimed_result(user_id, action, key, text, keyboard=None, *, chat_id=None,
                            business_connection_id=None, **_):
        ok, data = safe._original_tg_send(
            int(chat_id if chat_id is not None else user_id), text, keyboard,
            business_connection_id=business_connection_id,
        )
        return {"sent": ok, "status": "sent" if ok else "failed"}

    safe.install = install
    safe.send_claimed_result = send_claimed_result
    media = ModuleType("channel_media_manager")
    media.install = lambda *a: None
    private = ModuleType("channel_media_private_test")
    private.install = lambda *a: None
    modules = {
        "bot": bot, "daily_quiz_schedule": schedule, "safe_reminder_delivery": safe,
        "channel_media_manager": media, "channel_media_private_test": private,
    }
    spec = importlib.util.spec_from_file_location(
        "isolated_daily_quiz_alerts", Path(__file__).with_name("daily_quiz_alerts.py"),
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module, safe, text_sends


class FakeResponse:
    ok = True
    content = b"{}"

    def json(self):
        return {"ok": True, "result": {"message_id": 7}}


class BetroxyReminderImageTests(unittest.TestCase):
    def setUp(self):
        self.alerts, self.safe, self.text_sends = load_alerts()
        self.posts = []

        def fake_post(url, data=None, files=None, timeout=None):
            self.posts.append((url, dict(data or {}), files["photo"][0] if files else None))
            return FakeResponse()

        patcher = patch.object(self.alerts.requests, "post", fake_post)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_image_file_exists(self):
        self.assertTrue(self.alerts.REMINDER_IMAGE.is_file())

    def test_official_reminder_is_photo_with_same_caption_and_buttons(self):
        day = date(2026, 10, 1)
        result = self.alerts._send_official(42, "daily_quiz_open:2026-10-01", day)
        self.assertTrue(result["sent"])
        self.assertEqual(self.text_sends, [])
        self.assertEqual(len(self.posts), 1)
        url, data, name = self.posts[0]
        self.assertTrue(url.endswith("/sendPhoto"))
        self.assertEqual(name, "betroxy_reminder.jpg")
        self.assertEqual(data["caption"], self.alerts._message_for(day))
        self.assertEqual(data["parse_mode"], "HTML")
        self.assertEqual(json.loads(data["reply_markup"]), {"inline_keyboard": self.alerts._quiz_button()})
        self.assertNotIn("business_connection_id", data)

    def test_business_reminder_keeps_connection(self):
        row = {"customer_user_id": 42, "customer_chat_id": 4242, "connection_id": "bc-1", "enquiry_id": 1}
        with patch.object(self.alerts, "_business_still_safe", return_value=True):
            self.alerts._send_business(row, "daily_quiz_open:2026-10-01", date(2026, 10, 1))
        _, data, _ = self.posts[0]
        self.assertEqual(data["chat_id"], 4242)
        self.assertEqual(data["business_connection_id"], "bc-1")

    def test_long_text_sends_photo_then_unchanged_text(self):
        long_text = "x" * (self.alerts.CAPTION_LIMIT + 1)
        with patch.object(self.alerts, "_message_for", return_value=long_text):
            self.alerts._send_official(42, "k", date(2026, 10, 1))
        self.assertEqual(len(self.posts), 1)
        self.assertNotIn("caption", self.posts[0][1])
        self.assertNotIn("reply_markup", self.posts[0][1])
        self.assertEqual(self.text_sends, [(42, long_text, self.alerts._quiz_button(), None)])

    def test_other_senders_stay_text_only(self):
        self.safe._original_tg_send(42, "hello", None)
        self.assertEqual(self.posts, [])
        self.assertEqual(self.text_sends, [(42, "hello", None, None)])


if __name__ == "__main__":
    unittest.main()
