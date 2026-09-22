"""Offline regression tests for the text-only OfficialBot welcome.

No Telegram login, production database, customer send, voucher call or live
bot token is used.
"""
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

import welcome_no_banner_overlay


class NoBannerWelcomeTests(unittest.IsolatedAsyncioTestCase):
    def make(self):
        original_start = AsyncMock(return_value="legacy")
        original_main = MagicMock(return_value="main-ok")
        bot = NS(
            start=original_start,
            main=original_main,
            is_admin=lambda uid: uid == 100,
            find_agent_by_telegram_user_id=lambda uid: None,
            ParseMode=NS(HTML="HTML"),
            logger=MagicMock(),
        )
        production = NS(bot=bot)
        return production, bot, original_start, original_main

    async def test_normal_start_is_text_only_and_preserves_menu(self):
        production, bot, old_start, old_main = self.make()
        touch = MagicMock()
        welcome = NS(
            _bot_welcome_text=lambda: "WELCOME TEXT",
            bot_menu=lambda uid=None: ("MENU", uid),
        )
        clean = NS(v83=NS(_touch_user=touch))
        with patch.dict(
            sys.modules,
            {
                "welcome_experience_v2": welcome,
                "clean_customer_menu": clean,
            },
        ):
            welcome_no_banner_overlay.prepare(production)
            self.assertEqual(bot.main(), "main-ok")
            old_main.assert_called_once()

            msg = NS(reply_text=AsyncMock())
            update = NS(
                effective_user=NS(id=2),
                effective_message=msg,
            )
            context = NS(args=[])
            await bot.start(update, context)

        msg.reply_text.assert_awaited_once_with(
            "WELCOME TEXT",
            parse_mode="HTML",
            reply_markup=("MENU", 2),
            disable_web_page_preview=True,
        )
        old_start.assert_not_awaited()
        touch.assert_called_once_with(2, "welcome_v2")
        self.assertFalse(hasattr(msg, "reply_photo"))

    async def test_deeplinks_admin_affiliates_stay_on_existing_router(self):
        production, bot, old_start, _ = self.make()
        welcome = NS(_bot_welcome_text=lambda: "WELCOME", bot_menu=lambda uid=None: "MENU")
        with patch.dict(sys.modules, {"welcome_experience_v2": welcome}):
            welcome_no_banner_overlay.prepare(production)
            bot.main()

            msg = NS(reply_text=AsyncMock())
            update = NS(effective_user=NS(id=2), effective_message=msg)
            await bot.start(update, NS(args=["dailyquiz"]))
            old_start.assert_awaited_once()
            msg.reply_text.assert_not_awaited()

            old_start.reset_mock()
            bot.is_admin = lambda uid: uid == 2
            await bot.start(update, NS(args=[]))
            old_start.assert_awaited_once()
            msg.reply_text.assert_not_awaited()

    async def test_business_and_channel_banner_code_is_not_touched(self):
        source = open("welcome_no_banner_overlay.py", "r", encoding="utf-8").read()
        self.assertIn("business_banner=unchanged", source)
        self.assertIn("channel_banners=unchanged", source)
        self.assertNotIn("_send_business_reply =", source)
        self.assertNotIn("_approved_file_id =", source)
        self.assertNotIn("reply_photo(", source)

    async def test_prepare_is_idempotent(self):
        production, bot, _, old_main = self.make()
        welcome = NS(_bot_welcome_text=lambda: "WELCOME", bot_menu=lambda uid=None: "MENU")
        with patch.dict(sys.modules, {"welcome_experience_v2": welcome}):
            welcome_no_banner_overlay.prepare(production)
            first_main = bot.main
            welcome_no_banner_overlay.prepare(production)
            self.assertIs(bot.main, first_main)
            bot.main()
        old_main.assert_called_once()


if __name__ == "__main__":
    unittest.main()
