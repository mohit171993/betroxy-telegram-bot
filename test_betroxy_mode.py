"""Focused mode persistence and outbound-route checks (no Telegram token)."""

import unittest
import asyncio
import ast
import html
import json
import sys
import types
from pathlib import Path
from unittest.mock import patch

import betroxy_mode as mode


class FakeCursor:
    def __init__(self, database):
        self.database = database
        self.row = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        if sql.startswith("INSERT INTO betroxy_officialbot_mode"):
            self.database.setdefault("mode", "full")
        elif sql.startswith("UPDATE betroxy_officialbot_mode"):
            self.database["mode"] = params[0]
            self.row = {"mode": params[0]}
        elif sql.startswith("SELECT mode FROM betroxy_officialbot_mode"):
            self.row = {"mode": self.database["mode"]}

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, database):
        self.database = database

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        return FakeCursor(self.database)

    def commit(self):
        self.database["commits"] = self.database.get("commits", 0) + 1


class FakeBot:
    ADMIN_ID = 123
    BOT_USERNAME = "BetroxyOfficialBot"
    APP_URL = "https://example.test/app"

    def __init__(self, database):
        self.database = database
        self.InlineKeyboardButton = lambda text, **kwargs: (text, kwargs)
        self.InlineKeyboardMarkup = lambda rows: rows

    def get_db(self):
        return FakeConnection(self.database)


class ModeTests(unittest.TestCase):
    def setUp(self):
        mode._mode = "quiz"
        mode._bot = None

    def test_mode_is_durable_and_full_default_preserves_current_bot(self):
        database = {}
        self.assertEqual(mode.initialize(FakeBot(database)), "full")
        self.assertEqual(mode.set_mode("quiz", 123), "quiz")
        self.assertTrue(mode.is_quiz())
        mode._mode = "quiz"  # Simulated fresh process before database read.
        self.assertEqual(mode.initialize(FakeBot(database)), "quiz")
        self.assertEqual(mode.set_mode("full", 123), "full")
        self.assertEqual(database["mode"], "full")

    def test_external_product_links_are_removed_in_quiz_mode(self):
        mode._mode = "quiz"
        rows = [
            [{"text": "Quiz", "callback_data": "v110_join:1"}],
            [{"text": "Official quiz", "url": "https://t.me/BetroxyOfficialBot?start=dailyquiz"}],
            [{"text": "Sportsbook", "url": "https://t.me/BetroxyBot/sportsbook?startapp=sportsbook"}],
            [{"text": "Web app", "web_app": {"url": "https://betroxy.com/"}}],
            [{"text": "Betting bonus", "callback_data": "v110_join:1"}],
            [{"text": "My rewards", "callback_data": "v89_my_rewards"}],
            [{"text": "Casino", "callback_data": "some_old_menu"}],
        ]
        clean = mode.strip_external_rows(rows)
        self.assertEqual(len(clean), 2)
        self.assertEqual(clean[0][0]["callback_data"], "v110_join:1")
        self.assertIn("dailyquiz", clean[1][0]["url"])
        mode._mode = "full"
        self.assertIs(mode.strip_external_rows(rows), rows)

    def test_quiz_copy_removes_gambling_terms_and_blocks_product_copy(self):
        mode._mode = "quiz"
        self.assertEqual(
            mode.clean_quiz_text("Free entry — no deposit or wager required."),
            "Entry is free.",
        )
        self.assertIsNone(mode.clean_quiz_text("Open the casino for your bonus."))
        self.assertIsNone(mode.clean_quiz_text("Live odds are available now."))
        self.assertIsNone(mode.clean_quiz_text("Explore BETROXY now."))
        self.assertIsNone(mode.clean_quiz_text("Open https://betroxy.com"))
        self.assertEqual(
            mode.clean_quiz_text("https://t.me/BetroxyOfficialBot?start=dailyquiz"),
            "https://t.me/BetroxyOfficialBot?start=dailyquiz",
        )
        self.assertEqual(mode.clean_quiz_text("Today's sports quiz is open."),
                         "Today's sports quiz is open.")
        mode._mode = "full"
        self.assertEqual(mode.clean_quiz_text("Open the casino."), "Open the casino.")

    def test_callback_allowlist_blocks_reward_and_product_menus(self):
        self.assertTrue(mode._is_quiz_callback("compact_daily_quiz"))
        self.assertTrue(mode._is_quiz_callback("v110_answer:1:2"))
        self.assertTrue(mode._is_quiz_callback("mega_join:2"))
        self.assertTrue(mode._is_quiz_callback("reward_received:22"))
        self.assertFalse(mode._is_quiz_callback("v89_my_rewards"))
        self.assertFalse(mode._is_quiz_callback("compact_updates"))
        self.assertFalse(mode._is_quiz_callback("admin_home"))

    def test_quiz_voucher_delivery_remains_available_without_product_link(self):
        mode._mode = "quiz"
        receipt = (
            "🎉 <b>BETROXY Reward Delivered</b>\n🏆 Rank: <b>#1</b>\n"
            "🔗 Claim: https://giftport.in/claim/abc\n"
            "Keep this voucher private. You can also find it later under 🎁 My Rewards."
        )
        clean = mode.clean_quiz_reward_text(receipt)
        self.assertIn("https://giftport.in/claim/abc", clean)
        self.assertNotIn("My Rewards", clean)
        self.assertIsNone(mode.clean_quiz_reward_text(receipt.replace("giftport.in", "betroxy.com")))
        self.assertIsNone(mode.clean_quiz_reward_text(receipt + " Casino bonus"))

    def test_mode_command_is_private_admin_only_and_persists(self):
        class Stop(Exception):
            pass

        ext = types.ModuleType("telegram.ext")
        ext.ApplicationHandlerStop = Stop
        telegram = types.ModuleType("telegram")
        telegram.ext = ext
        database = {}
        mode.initialize(FakeBot(database))

        class Message:
            def __init__(self):
                self.replies = []

            async def reply_text(self, text, **kwargs):
                self.replies.append(text)

        def update(uid, chat_type="private"):
            return types.SimpleNamespace(
                effective_user=types.SimpleNamespace(id=uid),
                effective_chat=types.SimpleNamespace(type=chat_type),
                effective_message=Message(),
            )

        with patch.dict(sys.modules, {"telegram": telegram, "telegram.ext": ext}):
            stranger = update(999)
            with self.assertRaises(Stop):
                asyncio.run(mode._mode_command(stranger, types.SimpleNamespace(args=["quiz"])))
            self.assertEqual(stranger.effective_message.replies, [])
            self.assertEqual(database["mode"], "full")

            group_admin = update(123, "group")
            with self.assertRaises(Stop):
                asyncio.run(mode._mode_command(group_admin, types.SimpleNamespace(args=["quiz"])))
            self.assertEqual(group_admin.effective_message.replies, [])

            admin = update(123)
            with self.assertRaises(Stop):
                asyncio.run(mode._mode_command(admin, types.SimpleNamespace(args=["quiz"])))
            self.assertEqual(database["mode"], "quiz")
            self.assertIn("QUIZ", admin.effective_message.replies[-1])

    def test_verified_text_and_callbacks_cannot_open_full_routes_in_quiz(self):
        class Stop(Exception):
            pass

        ext = types.ModuleType("telegram.ext")
        ext.ApplicationHandlerStop = Stop
        telegram = types.ModuleType("telegram")
        telegram.ext = ext
        verification = types.ModuleType("betroxy_universal_verification")
        verification.is_verified = lambda uid: uid == 999
        mode.initialize(FakeBot({"mode": "quiz"}))

        class Message:
            def __init__(self, value):
                self.text = value
                self.contact = None
                self.replies = []

            async def reply_text(self, text, **kwargs):
                self.replies.append(text)

        def update(value, uid=999):
            return types.SimpleNamespace(
                effective_user=types.SimpleNamespace(id=uid),
                effective_chat=types.SimpleNamespace(type="private"),
                effective_message=Message(value),
            )

        with patch.dict(sys.modules, {
            "telegram": telegram,
            "telegram.ext": ext,
            "betroxy_universal_verification": verification,
        }), patch.object(mode, "_is_quiz_registration_input", return_value=False):
            for value in ("sportsbook", "hello", "9876543210", "/affiliate", ""):
                event = update(value)
                with self.assertRaises(Stop):
                    asyncio.run(mode._message_guard(event, None))
                self.assertEqual(event.effective_message.replies, [mode.QUIZ_HOME_TEXT])

            unverified = update("casino", uid=777)
            self.assertIsNone(asyncio.run(mode._message_guard(unverified, None)))
            self.assertEqual(unverified.effective_message.replies, [])

        with patch.dict(sys.modules, {
            "telegram": telegram,
            "telegram.ext": ext,
            "betroxy_universal_verification": verification,
        }):
            old = update("")
            old.callback_query = types.SimpleNamespace(data="v89_my_rewards", answer=lambda: None,
                                                       message=old.effective_message)
            async def answer():
                return None
            old.callback_query.answer = answer
            with self.assertRaises(Stop):
                asyncio.run(mode._callback_guard(old, None))
            self.assertEqual(old.effective_message.replies, [mode.QUIZ_HOME_TEXT])

            pending = update("")
            pending.callback_query = types.SimpleNamespace(data="v89_my_rewards")
            pending.effective_user.id = 777
            self.assertIsNone(asyncio.run(mode._callback_guard(pending, None)))

    def test_quiz_registration_input_requires_matching_quiz_state(self):
        daily = types.SimpleNamespace(v110=types.SimpleNamespace(
            _session=lambda uid: {"flow_state": "quiz_register" if uid == 5 else "complete"}
        ))
        mega = types.SimpleNamespace(_session=lambda uid: {"flow_state": "register" if uid == 6 else "complete"})
        with patch.dict(sys.modules, {"production": daily, "weekly_mega_quiz": mega}):
            self.assertTrue(mode._is_quiz_registration_input(5, "9876543210"))
            self.assertTrue(mode._is_quiz_registration_input(6, "+91 98765 43210"))
            self.assertFalse(mode._is_quiz_registration_input(7, "9876543210"))
            self.assertFalse(mode._is_quiz_registration_input(5, "hello"))

    def test_menu_refresh_pages_all_known_chats_and_clears_revoked_overrides(self):
        telegram = types.ModuleType("telegram")
        telegram.MenuButtonCommands = type("MenuButtonCommands", (), {})
        telegram.MenuButtonWebApp = lambda **kwargs: kwargs
        telegram.WebAppInfo = lambda **kwargs: kwargs
        verification = types.ModuleType("betroxy_universal_verification")
        verification.is_verified = lambda uid: uid == 5
        mode._bot = FakeBot({"mode": "quiz"})
        mode._reconcile_generation = 1
        mode._reconcile_status = {
            "mode": "quiz", "state": "running", "checked": 0,
            "updated": 0, "skipped": 0, "failed": 0,
        }

        class TelegramBot:
            def __init__(self):
                self.updated = []

            async def set_chat_menu_button(self, **kwargs):
                self.updated.append(kwargs["chat_id"])

        api = TelegramBot()
        pages = lambda cursor: [5, 6] if cursor == 0 else []
        with patch.dict(sys.modules, {
            "telegram": telegram,
            "betroxy_universal_verification": verification,
        }), patch.object(mode, "_user_id_page", side_effect=pages), patch.object(
            mode, "MENU_REFRESH_INTERVAL_SECONDS", 0,
        ):
            asyncio.run(mode._reconcile_menu_buttons(api, "quiz", 1))
        self.assertEqual(api.updated, [5, 6])
        self.assertEqual(mode._reconcile_status["state"], "complete")
        self.assertEqual(mode._reconcile_status["checked"], 2)
        self.assertEqual(mode._reconcile_status["skipped"], 0)

    def test_raw_business_backfill_rejection_never_falls_back_to_product_link(self):
        # Execute the actual recovery function without importing the live
        # Telegram worker or making a network request.
        source = Path(__file__).with_name("business_dm_reply_fix.py")
        tree = ast.parse(source.read_text(encoding="utf-8"))
        function = next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef) and node.name == "_direct_business_send")
        send = ast.Module(body=[function], type_ignores=[])

        class Markup:
            def to_dict(self):
                return {"inline_keyboard": [[{"text": "Daily Quiz", "url": "https://t.me/BetroxyOfficialBot?start=dailyquiz"}]]}

        replies = []
        def post(url, data, timeout):
            replies.append(dict(data))
            ok = len(replies) == 2
            return types.SimpleNamespace(ok=ok, content=b"{}", status_code=200,
                                         json=lambda: {"ok": ok, "result": {"message_id": 91}})

        biz51 = types.SimpleNamespace(
            _detect_intent=lambda text: "general",
            _reply_payload=lambda intent, first_reply: (
                "Open casino bonus", Markup(), "old-stage"),
            _update_lead_state=lambda *args, **kwargs: None,
            BETROXY_PRODUCT_BOT="https://t.me/BetroxyBot",
            BETROXY_WEBSITE="https://betroxy.com/",
        )
        bot = types.SimpleNamespace(BOT_TOKEN="test", BOT_USERNAME="BetroxyOfficialBot")
        v49 = types.SimpleNamespace(_mark_auto_ack=lambda *_: None,
                                    _record_outbound=lambda *args: None)
        namespace = {
            "access": types.SimpleNamespace(is_verified=lambda uid: True),
            "bot": bot, "betroxy_mode": mode, "biz51": biz51, "v49": v49,
            "json": json, "html": html, "requests": types.SimpleNamespace(post=post),
        }
        mode._mode = "quiz"
        mode._bot = FakeBot({"mode": "quiz"})
        with patch.object(mode, "clean_business_menu", return_value=Markup()):
            exec(compile(send, str(source), "exec"), namespace)
            result = namespace["_direct_business_send"]({
                "id": 1, "customer_user_id": 5, "customer_chat_id": 5,
                "connection_id": "abc", "last_message_text": "hi",
            })
        self.assertEqual(result, 91)
        self.assertEqual(len(replies), 2)
        self.assertNotIn("casino", replies[0]["text"].lower())
        self.assertNotIn("BetroxyBot", replies[1]["text"])
        self.assertNotIn("betroxy.com", replies[1]["text"])
        self.assertIn("start=dailyquiz", replies[1]["text"])


if __name__ == "__main__":
    unittest.main()

