import asyncio
import importlib.util
import json
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
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
        self.replies.append(text)


class Query:
    def __init__(self, data, message, uid=42):
        self.data, self.message = data, message
        self.from_user = SimpleNamespace(id=uid)
        self.answers = []

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))


class Cursor:
    def __init__(self):
        self.calls = []
        self.entry = None
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params):
        self.calls.append((sql, params))

    def fetchone(self):
        return self.entry

    def fetchall(self):
        return self.rows


class Connection:
    def __init__(self, cursor):
        self.cursor_instance = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self.cursor_instance


def load_review(verified):
    cursor = Cursor()
    bot = ModuleType("bot")
    bot.InlineKeyboardButton = Button
    bot.InlineKeyboardMarkup = Markup
    bot.ParseMode = SimpleNamespace(HTML="HTML")
    bot.logger = SimpleNamespace(warning=lambda *_a, **_k: None, exception=lambda *_a, **_k: None)
    bot.get_db = lambda: Connection(cursor)
    bot.public_menu = lambda *_: Markup([[Button("Practice", callback_data="btx_practice")]])
    bot.callback_handler = lambda *_: None
    gate = ModuleType("betroxy_universal_verification")
    gate.is_verified = lambda uid: uid in verified
    welcome = ModuleType("welcome_experience_v2")
    welcome.bot_menu = bot.public_menu
    mega = ModuleType("weekly_mega_quiz")
    mega._completion_rows = lambda _campaign: [[{"text": "Leaderboard"}]]
    compact = ModuleType("clean_customer_menu")
    compact.compact_public_menu = bot.public_menu
    compact.v96 = SimpleNamespace(v96_public_menu=bot.public_menu)
    compact.v53 = SimpleNamespace(v53_public_menu=bot.public_menu)
    production = {"_result_rows": lambda _campaign: [[{"text": "Leaderboard"}]]}

    spec = importlib.util.spec_from_file_location(
        "isolated_betroxy_answer_review", Path(__file__).with_name("betroxy_answer_review.py")
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {
        "bot": bot, "betroxy_universal_verification": gate,
        "welcome_experience_v2": welcome, "weekly_mega_quiz": mega,
    }):
        spec.loader.exec_module(module)
        module.install(compact, production)
    return module, bot, cursor, welcome, production, mega


def update(query):
    return SimpleNamespace(
        callback_query=query, effective_user=query.from_user,
        effective_chat=SimpleNamespace(type="private", id=query.from_user.id),
    )


class AnswerReviewTests(unittest.TestCase):
    def test_only_closed_campaigns_reveal_answer_key(self):
        module, bot, cursor, welcome, production, mega = load_review({42})
        self.assertEqual(sum(b.callback_data == "btx_review" for row in welcome.bot_menu(42).inline_keyboard for b in row), 1)
        self.assertEqual(production["_result_rows"]({})[0][0]["callback_data"], "btx_review:daily")
        self.assertEqual(mega._completion_rows({})[0][0]["callback_data"], "btx_review:mega")

        now = datetime.now(timezone.utc)
        cursor.entry = {
            "entry_id": 11, "campaign_id": 22, "campaign_date": date.today(),
            "closes_at": now + timedelta(minutes=10),
        }
        msg = Message()
        query = Query("btx_review:daily", msg)
        asyncio.run(bot.callback_handler(update(query), SimpleNamespace(user_data={})))
        self.assertEqual(len(cursor.calls), 1)
        self.assertIn("closes_at<=NOW()", cursor.calls[0][0])
        self.assertEqual(cursor.calls[0][1], (42,))
        self.assertNotIn("Correct answer", msg.replies[-1])

        cursor.entry["closes_at"] = now - timedelta(minutes=10)
        cursor.rows = [
            {"seq": 1, "question": "Example?", "options_json": json.dumps(["A", "B", "C", "D"]),
             "correct_option": 1, "selected_option": 0, "is_correct": False, "answer_id": 4},
            {"seq": 2, "question": "Other?", "options_json": json.dumps(["A", "B", "C", "D"]),
             "correct_option": 2, "selected_option": 2, "is_correct": True, "answer_id": 5},
            {"seq": 3, "question": "Unanswered?", "options_json": json.dumps(["A", "B", "C", "D"]),
             "correct_option": 3, "selected_option": None, "is_correct": False, "answer_id": 6},
            {"seq": 4, "question": "Late?", "options_json": json.dumps(["A", "B", "C", "D"]),
             "correct_option": 1, "selected_option": 1, "is_correct": False, "answer_id": 7},
        ]
        asyncio.run(bot.callback_handler(update(Query("btx_review:daily", msg)), SimpleNamespace(user_data={})))
        self.assertEqual(len(cursor.calls), 3)
        self.assertTrue(all(sql.strip().upper().startswith("SELECT") for sql, _ in cursor.calls))
        self.assertIn("c.closes_at<=NOW()", cursor.calls[-1][0])
        self.assertEqual(cursor.calls[-1][1], (11, 22))
        self.assertIn("Correct answer: <b>B</b>", msg.replies[-1])
        self.assertIn("No answer recorded", msg.replies[-1])
        self.assertIn("not counted in time", msg.replies[-1])
        self.assertNotIn("Other?", msg.replies[-1])

        # If an admin extends the close time between reads, the second SELECT
        # returns no questions and the callback does not disclose answer keys.
        cursor.rows = []
        asyncio.run(bot.callback_handler(update(Query("btx_review:daily", msg)), SimpleNamespace(user_data={})))
        self.assertNotIn("Correct answer", msg.replies[-1])

    def test_unverified_request_never_queries_answers(self):
        _module, bot, cursor, _welcome, _production, _mega = load_review(set())
        msg = Message()
        query = Query("btx_review:mega", msg)
        asyncio.run(bot.callback_handler(update(query), SimpleNamespace(user_data={})))
        self.assertEqual(cursor.calls, [])
        self.assertEqual(msg.replies, [])
        self.assertIn("Verify", query.answers[-1][0][0])

    def test_missing_or_naive_close_time_fails_closed(self):
        module, *_rest = load_review({42})
        self.assertFalse(module._closed({"closes_at": None}))
        self.assertFalse(module._closed({"closes_at": datetime.now(timezone.utc).replace(tzinfo=None)}))

    def test_long_review_keeps_complete_html_and_never_claims_no_misses(self):
        module, *_rest = load_review({42})
        entry = {"campaign_date": date.today()}
        rows = [{
            "seq": 1, "question": "A" * 4000,
            "options_json": json.dumps(["A", "B", "C", "D"]),
            "correct_option": 1, "selected_option": 0, "is_correct": False,
        }]
        body = module._render_review(entry, rows, "daily")
        self.assertLess(len(body), 3800)
        self.assertIn("Review shortened", body)
        self.assertNotIn("No missed answers", body)
        self.assertEqual(body.count("<b>"), body.count("</b>"))


if __name__ == "__main__":
    unittest.main()
