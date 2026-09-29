import asyncio
import importlib.util
import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch


def load_status():
    bot = ModuleType("bot")
    events = []

    async def previous_post_init(_application):
        events.append("previous")

    bot.post_init = previous_post_init
    bot.is_admin = lambda uid: uid == 1
    bot.ParseMode = SimpleNamespace(HTML="HTML")

    fix = ModuleType("business_dm_reply_fix")
    fix.business_message_update_with_menu_reply = lambda *_: None
    inbox = ModuleType("v49_telegram_business_inbox_bootstrap")
    inbox._business_message_update = fix.business_message_update_with_menu_reply
    alerts = ModuleType("daily_quiz_alerts")
    alerts._disabled_engagement_worker = lambda: None
    alerts.v83 = SimpleNamespace(_worker_loop=alerts._disabled_engagement_worker)
    alerts.v110 = SimpleNamespace(CHANNEL_CHAT="@betroxyupdate")
    schedule = ModuleType("daily_quiz_schedule")
    schedule.SCHEDULE_ENABLED = True
    schedule.RESULT_CHANNEL_ENABLED = True
    telegram = ModuleType("telegram")
    telegram.__path__ = []
    ext = ModuleType("telegram.ext")

    class CommandHandler:
        def __init__(self, name, callback):
            self.name, self.callback = name, callback

    ext.CommandHandler = CommandHandler
    modules = {
        "bot": bot, "business_dm_reply_fix": fix,
        "v49_telegram_business_inbox_bootstrap": inbox,
        "daily_quiz_alerts": alerts, "daily_quiz_schedule": schedule,
        "telegram": telegram, "telegram.ext": ext,
    }
    spec = importlib.util.spec_from_file_location(
        "isolated_betroxy_delivery_status",
        Path(__file__).with_name("betroxy_delivery_status.py"),
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module, bot, events


class DeliveryStatusTests(unittest.TestCase):
    def test_registers_admin_command_after_existing_post_init(self):
        status, bot, events = load_status()
        status.install()
        app = SimpleNamespace(add_handler=lambda handler: events.append(handler.name))
        asyncio.run(bot.post_init(app))
        self.assertEqual(events, ["previous", "betroxy_status"])

    def test_non_admin_receives_no_status_or_database_query(self):
        status, _, _ = load_status()
        status._db_snapshot = lambda *_: self.fail("non-admin queried database")
        update = SimpleNamespace(effective_user=SimpleNamespace(id=2))
        asyncio.run(status.command(update, SimpleNamespace()))

    def test_status_distinguishes_workers_and_recorded_delivery(self):
        status, _, _ = load_status()
        status._worker_alive = lambda *_: True
        snapshot = {
            "last_post": {
                "delivery_type": "quiz_alert_open",
                "sent_at": datetime(2026, 9, 29, 4, 30, tzinfo=timezone.utc),
            },
            "membership_audit": None,
            "dm_counts": {("officialbot", "sent"): 3, ("business", "sent"): 1},
            "business_auto_reply": True,
        }
        message = status._render(snapshot, date(2026, 9, 29), True)
        self.assertIn("can post YES", message)
        self.assertIn("OfficialBot 3 · Business 1 · failed 0", message)
        self.assertIn("Other proactive reminders: OFF", message)


if __name__ == "__main__":
    unittest.main()
