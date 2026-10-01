"""@Liveline_proadmin (8860632140) has the same admin rights as ADMIN_ID."""

import os
import unittest

os.environ.setdefault("BOT_TOKEN", "123:test")
os.environ.setdefault("DATABASE_URL", "postgresql://test@127.0.0.1:1/test")
os.environ.setdefault("ADMIN_ID", "8992664481")

try:
    import bot
    import betroxy_extra_admins  # noqa: F401
except Exception as exc:  # pragma: no cover - optional runtime deps
    bot = None
    _IMPORT_ERROR = exc

PROADMIN = 8860632140


@unittest.skipIf(bot is None, "bot runtime dependencies unavailable")
class BetroxyExtraAdminTests(unittest.TestCase):
    def test_proadmin_and_main_admin_are_admins(self):
        self.assertIn(PROADMIN, bot.EXTRA_ADMIN_IDS)
        self.assertTrue(bot.is_admin(PROADMIN))
        self.assertTrue(bot.is_admin(str(PROADMIN)))
        self.assertTrue(bot.is_admin(bot.ADMIN_ID))
        self.assertFalse(bot.is_admin(5))
        self.assertFalse(bot.is_admin(None))


if __name__ == "__main__":
    unittest.main()
