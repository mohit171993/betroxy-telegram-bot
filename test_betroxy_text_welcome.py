"""Offline presentation/regression checks. No API or production DB access."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

from betroxy_text_welcome import WELCOME_TEXT, install, prepare


class TextWelcomeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.old_start = AsyncMock(return_value="legacy")
        self.bot = NS(start=self.old_start, is_admin=lambda uid: uid == 100,
                      find_agent_by_telegram_user_id=MagicMock(return_value=None))
        self.menu = object()
        self.business_text = MagicMock(return_value="Business unchanged")
        self.welcome = NS(bot_menu=MagicMock(return_value=self.menu),
                          _bot_welcome_text=MagicMock(return_value="old welcome"),
                          _business_welcome_text=self.business_text)
        self.touch = MagicMock()
        self.start = install(self.bot, self.welcome, self.touch)
        self.context = NS(args=[], user_data={"unrelated_state": "keep"})

    def update(self, uid=2, kind="private", business=False):
        message = NS(reply_text=AsyncMock(), reply_photo=AsyncMock(),
                     business_connection_id="connection" if business else None)
        return NS(effective_user=NS(id=uid), effective_message=message,
                  effective_chat=NS(id=uid if kind == "private" else -99, type=kind))

    async def test_plain_start_sends_one_short_text_and_no_banner(self):
        update = self.update()
        await self.start(update, self.context)
        update.effective_message.reply_text.assert_awaited_once()
        update.effective_message.reply_photo.assert_not_awaited()
        self.old_start.assert_not_awaited()
        self.assertEqual(update.effective_message.reply_text.call_args.args[0], WELCOME_TEXT)
        self.assertLess(len(WELCOME_TEXT), 180)
        self.assertIn("Sportsbook", WELCOME_TEXT)
        self.assertIn("Quizzes", WELCOME_TEXT)
        self.assertIn("Rewards", WELCOME_TEXT)

    async def test_exact_existing_menu_object_and_new_weekly_buttons_retained(self):
        update = self.update()
        await self.start(update, self.context)
        self.welcome.bot_menu.assert_called_once_with(2)
        self.assertIs(update.effective_message.reply_text.call_args.kwargs["reply_markup"], self.menu)
        later_menu = object()
        self.welcome.bot_menu.return_value = later_menu
        await self.start(update, self.context)
        self.assertIs(update.effective_message.reply_text.call_args.kwargs["reply_markup"], later_menu)

    async def test_specialized_and_referral_payloads_delegate_unchanged(self):
        for payload in ("dailyquiz", "megaquiz", "rewards", "account", "support", "prizes", "samratking"):
            context = NS(args=[payload], user_data={})
            update = self.update()
            self.assertEqual(await self.start(update, context), "legacy")
            self.old_start.assert_awaited_with(update, context)
            update.effective_message.reply_text.assert_not_awaited()
        self.touch.assert_not_called()

    async def test_admin_and_affiliate_dashboards_not_replaced(self):
        await self.start(self.update(uid=100), self.context)
        self.bot.find_agent_by_telegram_user_id.return_value = {"id": 7}
        await self.start(self.update(), self.context)
        self.assertEqual(self.old_start.await_count, 2)
        self.welcome.bot_menu.assert_not_called()

    async def test_business_and_group_routes_not_replaced(self):
        for update in (self.update(business=True), self.update(kind="group")):
            await self.start(update, self.context)
            self.old_start.assert_awaited_with(update, self.context)
            update.effective_message.reply_photo.assert_not_awaited()
        self.assertIs(self.welcome._business_welcome_text, self.business_text)

    async def test_missing_message_delegates(self):
        update = self.update()
        update.effective_message = None
        await self.start(update, self.context)
        self.old_start.assert_awaited_once_with(update, self.context)

    async def test_activity_state_and_menu_home_text(self):
        await self.start(self.update(), self.context)
        self.touch.assert_called_once_with(2, "banner_manager_start")
        self.assertEqual(self.context.user_data, {"unrelated_state": "keep"})
        self.assertEqual(self.welcome._bot_welcome_text(), WELCOME_TEXT)
        self.assertEqual(self.welcome._business_welcome_text(), "Business unchanged")

    async def test_activity_failure_does_not_restore_banner(self):
        self.touch.side_effect = RuntimeError("test only")
        update = self.update()
        await self.start(update, self.context)
        update.effective_message.reply_text.assert_awaited_once()
        self.old_start.assert_not_awaited()

    async def test_send_failure_never_replays_legacy_banner(self):
        update = self.update()
        update.effective_message.reply_text.side_effect = RuntimeError("send failed")
        with self.assertRaises(RuntimeError):
            await self.start(update, self.context)
        self.old_start.assert_not_awaited()
        update.effective_message.reply_photo.assert_not_awaited()

    async def test_install_is_idempotent(self):
        self.assertIs(install(self.bot, self.welcome, self.touch), self.start)
        await self.bot.start(self.update(), self.context)
        self.touch.assert_called_once()

    async def test_prepare_waits_for_all_legacy_menu_installers(self):
        initial_start = AsyncMock()
        final_start = AsyncMock()
        main = MagicMock(return_value="running")
        bot = NS(start=initial_start, main=main, is_admin=lambda uid: False,
                 find_agent_by_telegram_user_id=lambda uid: None)
        production = NS(bot=bot, v83=NS(_touch_user=self.touch))
        prepare(production)
        prepared = bot.main
        prepare(production)
        self.assertIs(bot.main, prepared)
        self.assertIs(bot.start, initial_start)
        bot.start = final_start  # Simulate the later legacy startup installers.
        with patch("betroxy_text_welcome.importlib.import_module", return_value=self.welcome):
            self.assertEqual(bot.main(), "running")
        self.assertIs(bot.start._previous_start, final_start)
        main.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
