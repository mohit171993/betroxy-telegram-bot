"""Afternoon, last-call and results channel posts use the committed poster files."""

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch


def load_manager():
    bot = ModuleType("bot")
    bot.logger = SimpleNamespace(warning=lambda *a, **k: None, error=lambda *a, **k: None,
                                 exception=lambda *a, **k: None)
    spec = importlib.util.spec_from_file_location(
        "isolated_channel_media_manager", Path(__file__).with_name("channel_media_manager.py"))
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"bot": bot}):
        spec.loader.exec_module(module)
    return module


class Resp:
    ok = True
    content = b"{}"

    def json(self):
        return {"ok": True, "result": {"message_id": 5}}


class RepoPosterTests(unittest.TestCase):
    def setUp(self):
        self.m = load_manager()
        self.text_calls, self.posts = [], []
        self.schedule = SimpleNamespace(_send_text=lambda c, t, r=None: self.text_calls.append((c, t, r)) or (True, {}))
        self.m._v110 = SimpleNamespace(CHANNEL_CHAT="@betroxyupdate", TG_API="https://api.telegram.invalid/botX")
        self.m._schedule = self.schedule
        self.m._approved_file_id = lambda key: "OLD_FILE_ID"

        def fake_post(url, data=None, files=None, timeout=None):
            self.posts.append((url, dict(data or {}), files))
            return Resp()

        p = patch.object(self.m.requests, "post", fake_post)
        p.start()
        self.addCleanup(p.stop)
        self.m._install_channel_send_wrapper()

    def test_poster_files_decode_to_jpeg(self):
        for key in ("quiz_afternoon", "quiz_last_chance", "quiz_result"):
            data = self.m._repo_poster_bytes(key)
            self.assertTrue(data and data[:3] == b"\xff\xd8\xff", key)

    def test_three_posts_upload_repo_poster_with_same_caption_and_buttons(self):
        rows = [[{"text": "🏆 Play Today's Quiz", "url": "https://t.me/BetroxyOfficialBot?start=dailyquiz"}]]
        for key, text in (("quiz_afternoon", "<b>Can You Reach Today's Top 3?</b>"),
                          ("quiz_last_chance", "Only 2 Hours Left — Final Call"),
                          ("quiz_result", "BETROXY DAILY CHALLENGE — FINAL RESULTS")):
            self.posts.clear()
            self.schedule._send_text("@betroxyupdate", text, rows)
            url, data, files = self.posts[0]
            self.assertTrue(url.endswith("/sendPhoto"))
            self.assertEqual(data["caption"], text)
            self.assertEqual(data["parse_mode"], "HTML")
            self.assertEqual(json.loads(data["reply_markup"]), {"inline_keyboard": rows})
            self.assertNotIn("photo", data)  # uploaded bytes, not the old file_id
            self.assertEqual(files["photo"][0], f"{key}.jpg")
            self.assertEqual(files["photo"][1], self.m._repo_poster_bytes(key))
        self.assertEqual(self.text_calls, [])

    def test_quiz_open_still_uses_approved_file_id(self):
        self.schedule._send_text("@betroxyupdate", "Today's BETROXY Daily Quiz is OPEN", None)
        _, data, files = self.posts[0]
        self.assertEqual(data["photo"], "OLD_FILE_ID")
        self.assertIsNone(files)

    def test_other_chats_and_texts_unchanged(self):
        self.schedule._send_text(123, "Can You Reach Today's Top 3?", None)
        self.schedule._send_text("@betroxyupdate", "Other post", None)
        self.assertEqual(self.posts, [])
        self.assertEqual(len(self.text_calls), 2)


if __name__ == "__main__":
    unittest.main()
