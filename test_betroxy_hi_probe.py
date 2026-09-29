import asyncio
import unittest
from types import SimpleNamespace

import betroxy_hi_probe as probe


class FakeCursor:
    def __init__(self, state):
        self.state = state
        self.row = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=()):
        compact = " ".join(sql.split())
        if compact.startswith("SELECT telegram_user_id FROM btx_crm_test_identity"):
            self.row = {"telegram_user_id": self.state["pinned"]}
        elif compact.startswith("INSERT INTO btx_one_time_probe_sends"):
            if self.state["claimed"]:
                self.row = None
            else:
                self.state["claimed"] = True
                self.row = {"probe_key": probe.PROBE_KEY}
        elif compact.startswith("UPDATE btx_one_time_probe_sends"):
            self.state["outcome"] = params[:3]
            self.row = None
        else:
            self.row = None

    def fetchone(self):
        row, self.row = self.row, None
        return row


class FakeConnection:
    def __init__(self, state):
        self.state = state

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        return FakeCursor(self.state)

    def commit(self):
        return None


class HiProbeTests(unittest.TestCase):
    def test_exact_pinned_user_receives_one_hi_even_after_second_startup(self):
        state = {"pinned": probe.TARGET_UID, "claimed": False, "outcome": None}
        sent = []

        async def previous(_app):
            return None

        async def send_message(**kwargs):
            sent.append(kwargs)
            return SimpleNamespace(message_id=42)

        bot = SimpleNamespace(post_init=previous, get_db=lambda: FakeConnection(state))
        app = SimpleNamespace(bot=SimpleNamespace(send_message=send_message))
        probe.install(bot)
        asyncio.run(bot.post_init(app))
        asyncio.run(bot.post_init(app))
        self.assertEqual(sent, [{"chat_id": probe.TARGET_UID, "text": "hi"}])
        self.assertEqual(state["outcome"], ("sent", 42, None))

    def test_identity_mismatch_sends_nothing(self):
        state = {"pinned": 999, "claimed": False, "outcome": None}
        sent = []

        async def send_message(**kwargs):
            sent.append(kwargs)

        bot = SimpleNamespace(post_init=lambda _app: None, get_db=lambda: FakeConnection(state))
        app = SimpleNamespace(bot=SimpleNamespace(send_message=send_message))
        probe.install(bot)
        asyncio.run(bot.post_init(app))
        self.assertEqual(sent, [])
        self.assertFalse(state["claimed"])


if __name__ == "__main__":
    unittest.main()
