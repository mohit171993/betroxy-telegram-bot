import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch


class Button:
    def __init__(self, text, **kwargs):
        self.text = text
        self.callback_data = kwargs.get("callback_data")


class Markup:
    def __init__(self, rows):
        self.inline_keyboard = rows


class Message:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))


class Query:
    def __init__(self, data, message):
        self.data, self.message = data, message
        self.answers = []
        self.edits = 0

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))

    async def edit_message_reply_markup(self, **_kwargs):
        self.edits += 1


def load_practice(verified):
    bot = ModuleType("bot")
    bot.InlineKeyboardButton = Button
    bot.InlineKeyboardMarkup = Markup
    bot.ParseMode = SimpleNamespace(HTML="HTML")
    bot.logger = SimpleNamespace(warning=lambda *_args, **_kwargs: None)
    bot.public_menu = lambda *_: Markup([[Button("Daily Quiz", callback_data="compact_daily_quiz")]])
    calls = []

    async def previous_callback(*_args):
        calls.append("previous")

    bot.callback_handler = previous_callback
    gate = ModuleType("betroxy_universal_verification")
    gate.is_verified = lambda uid: uid in verified
    welcome = ModuleType("welcome_experience_v2")
    welcome.bot_menu = bot.public_menu
    compact = ModuleType("clean_customer_menu")
    compact.compact_public_menu = bot.public_menu
    compact.v96 = SimpleNamespace(v96_public_menu=bot.public_menu)
    compact.v53 = SimpleNamespace(v53_public_menu=bot.public_menu)

    spec = importlib.util.spec_from_file_location(
        "isolated_betroxy_practice_quiz", Path(__file__).with_name("betroxy_practice_quiz.py")
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {
        "bot": bot, "betroxy_universal_verification": gate,
        "welcome_experience_v2": welcome,
    }):
        spec.loader.exec_module(module)
        module.install(compact)
    return module, bot, welcome, calls


def update(uid, query):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=uid),
        effective_chat=SimpleNamespace(type="private", id=uid),
        callback_query=query,
    )


class PracticeQuizTests(unittest.TestCase):
    def test_verified_practice_has_explanations_but_no_official_side_effects(self):
        module, bot, welcome, calls = load_practice({42})
        menu = welcome.bot_menu(42)
        self.assertEqual(sum(b.callback_data == "btx_practice" for row in menu.inline_keyboard for b in row), 1)
        self.assertEqual(sum(b.callback_data == "btx_practice" for row in bot.public_menu(42).inline_keyboard for b in row), 1)

        msg = Message()
        context = SimpleNamespace(user_data={})
        asyncio.run(bot.callback_handler(update(42, Query("btx_practice", msg)), context))
        state = context.user_data[module.SESSION_KEY]
        self.assertIn("no prizes or leaderboard", msg.replies[-1][0])

        for index, question in enumerate(module.QUESTIONS):
            nonce = state["nonce"]
            answer = Query(f"btx_practice:{nonce}:{index}:{question['correct']}", msg)
            asyncio.run(bot.callback_handler(update(42, answer), context))
            self.assertEqual(answer.edits, 1)
            self.assertIn(question["explanation"], msg.replies[-1][0])
            if index + 1 < len(module.QUESTIONS):
                stale = Query(f"btx_practice:{nonce}:{index}:{question['correct']}", msg)
                asyncio.run(bot.callback_handler(update(42, stale), context))
                self.assertEqual(state["score"], index + 1)
                asyncio.run(bot.callback_handler(update(42, Query(f"btx_practice_next:{nonce}:{index}", msg)), context))

        self.assertNotIn(module.SESSION_KEY, context.user_data)
        self.assertIn(f"{len(module.QUESTIONS)}/{len(module.QUESTIONS)}", msg.replies[-1][0])
        self.assertEqual(calls, [])
        # The fake bot deliberately has no database or reward methods.

    def test_unverified_callback_does_not_start_practice(self):
        module, bot, _welcome, _calls = load_practice(set())
        msg = Message()
        query = Query("btx_practice", msg)
        context = SimpleNamespace(user_data={})
        asyncio.run(bot.callback_handler(update(42, query), context))
        self.assertNotIn(module.SESSION_KEY, context.user_data)
        self.assertEqual(msg.replies, [])
        self.assertIn("Verify", query.answers[-1][0][0])


if __name__ == "__main__":
    unittest.main()
