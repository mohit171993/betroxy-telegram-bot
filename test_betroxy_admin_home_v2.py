"""Synthetic tests for the BETROXY grouped admin home (no network, no DB)."""
import asyncio
import sys
import types
import unittest
from contextlib import contextmanager
from types import SimpleNamespace

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationHandlerStop, CommandHandler

import betroxy_crm_ui
import betroxy_admin_home_v2 as v2

ADMINS = {8992664481, 8860632140}
PROTECTED = {"bot.py", "production.py", "daily_quiz_schedule.py", "quiz_mobile_verification_overlay.py",
             "reward_code_display_fix.py", "reward_receipt_confirmation.py", "weekly_mega_quiz.py",
             "safe_reminder_delivery.py"}


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def callbacks(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


class Query:
    def __init__(self, data, uid):
        self.data, self.from_user = data, SimpleNamespace(id=uid)
        self.message = Msg()
        self.edits, self.answers = [], []

    async def answer(self, *a, **k):
        self.answers.append((a, k))

    async def edit_message_text(self, **kwargs):
        self.edits.append(kwargs)

    async def edit_message_reply_markup(self, **kwargs):
        self.edits.append(kwargs)


class Msg:
    def __init__(self):
        self.sent = []

    async def reply_text(self, *a, **k):
        self.sent.append((a, k))


def update(uid=8992664481, data=None, text="/admin"):
    q = Query(data, uid) if data else None
    msg = q.message if q else Msg()
    return SimpleNamespace(effective_user=SimpleNamespace(id=uid), effective_chat=SimpleNamespace(type="private"),
                           callback_query=q, effective_message=msg, message=msg)


def ctx(args=None):
    return SimpleNamespace(user_data={}, args=args or [], bot=None)


class App:
    def __init__(self):
        self.handlers = {}

    def add_handler(self, handler, group=0):
        self.handlers.setdefault(group, []).append(handler)


class Harness:
    def __init__(self, broken=False):
        self.broken = broken
        self.posted = []

        @contextmanager
        def get_db():
            raise RuntimeError("db down")
            yield None

        self.bot = SimpleNamespace(is_admin=lambda uid: int(uid) in ADMINS, get_db=get_db, post_init=None,
                                   main=lambda: "started")
        people = {1: {"uid": 1, "verified": True, "phone": "1", "assigned_to": None, "status": "new",
                      "next_followup": None, "is_test": False, "first_seen": None,
                      "username": "", "name": ""}}

        def snapshot():
            if self.broken:
                raise RuntimeError("db down")
            return dict(people)

        self.store = SimpleNamespace(snapshot=snapshot)
        old = InlineKeyboardMarkup([[InlineKeyboardButton("📊 TEAM CRM", callback_data="btxcrm:home")],
                                    [InlineKeyboardButton("Old", callback_data="v91_admincat:system")]])
        self.v94 = types.ModuleType("v94_admin_mode_cleanup")
        self.v94.v94_admin_menu = lambda: old
        self.v94._admin_home_text = lambda: "OLD HOME"
        self.v91 = types.ModuleType("v91_admin_categories_reporting_hub")
        self.v91.v91_admin_menu = lambda: old
        self.v89 = types.ModuleType("v89_rewards_mobile_ai")
        self.v89._reward_center_keyboard = lambda: InlineKeyboardMarkup([
            [InlineKeyboardButton("🎁 Rewards", callback_data="v89_rewards")],
            [InlineKeyboardButton("🧪 PhonePe", callback_data="v102_phonepe_test")]])
        self.saved = {n: sys.modules.get(n) for n in ("v94_admin_mode_cleanup", "v91_admin_categories_reporting_hub",
                                                      "v89_rewards_mobile_ai", "v97_giftport_live_adapter")}
        sys.modules.update({"v94_admin_mode_cleanup": self.v94, "v91_admin_categories_reporting_hub": self.v91,
                            "v89_rewards_mobile_ai": self.v89})
        sys.modules.pop("v97_giftport_live_adapter", None)
        self.old_dashboard = betroxy_crm_ui.dashboard

        async def post_10am_now(update, context):
            self.posted.append(update)

        self.app = App()
        self.app.add_handler(CommandHandler("post_10am_now", post_10am_now), group=-111)
        self.panel = v2.prepare(SimpleNamespace(bot=self.bot), self.store)
        assert self.bot.post_init is None  # nothing hooked until the bot starts
        assert self.bot.main() == "started"
        run(self.bot.post_init(self.app))

    def close(self):
        betroxy_crm_ui.dashboard = self.old_dashboard
        for name, mod in self.saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod

    def handler(self, kind, pattern=None, command=None):
        for h in self.app.handlers.get(v2.GROUP, []):
            if command and isinstance(h, CommandHandler) and command in h.commands:
                return h.callback
            if pattern and getattr(h, "pattern", None) is not None and h.pattern.match(pattern):
                return h.callback
        raise AssertionError(kind)


class BetroxyAdminHomeV2Tests(unittest.TestCase):
    def setUp(self):
        self.h = Harness()

    def tearDown(self):
        self.h.close()

    def test_home_has_six_categories_two_per_row(self):
        text, markup = self.h.panel.home()
        self.assertIn("BETROXY ADMIN", text)
        self.assertEqual(len(self.h.panel.order), 6)
        self.assertTrue(all(len(r) <= 2 for r in markup.inline_keyboard))
        for key in self.h.panel.order:
            _, sub = self.h.panel.category(key)
            self.assertTrue(all(len(r) <= 2 for r in sub.inline_keyboard))
            self.assertEqual(callbacks(sub)[-2:], ["adm2:home", "adm2:home"])

    def test_buttons_unique_across_categories_except_shared_inbox(self):
        seen = []
        for key in self.h.panel.order:
            seen += [c for c in callbacks(self.h.panel.category(key)[1]) if not c.startswith("adm2:home")]
        dupes = {c for c in seen if seen.count(c) > 1} - {"business_home"}
        self.assertEqual(dupes, set())
        self.assertNotIn("v102_phonepe_test", seen)
        self.assertIn("adm2:act:phonepe", seen)

    def test_status_dash_on_error_and_cached(self):
        self.h.broken = True
        line = self.h.panel.compute_status()
        self.assertIn("👥 -", line)
        self.h.broken = False
        run(self.h.panel.warm())
        self.assertIn("👥 -", self.h.panel.status_line())  # cached for 60s
        run(self.h.panel.warm(force=True))
        self.assertIn("👥 1 leads", self.h.panel.status_line())

    def test_legacy_home_paths_render_single_home(self):
        self.assertIn("adm2:cat:crm", callbacks(self.h.v94.v94_admin_menu()))
        self.assertIs(self.h.v91.v91_admin_menu, self.h.v94.v94_admin_menu)
        self.assertIn("BETROXY ADMIN", self.h.v94._admin_home_text())
        u = update(data="v94_admin_home")
        with self.assertRaises(ApplicationHandlerStop):
            run(self.h.handler("home", pattern="v94_admin_home")(u, ctx()))
        self.assertIn("adm2:cat:crm", callbacks(u.callback_query.edits[0]["reply_markup"]))

    def test_admin_command_both_admins_and_non_admin_ignored(self):
        cb = self.h.handler("admin", command="admin")
        for uid in ADMINS:
            u = update(uid)
            c = ctx()
            c.user_data["btxcrm_pending"] = "x"
            with self.assertRaises(ApplicationHandlerStop):
                run(cb(u, c))
            self.assertNotIn("btxcrm_pending", c.user_data)
            self.assertEqual(len(u.message.sent), 1)
        u = update(12345)
        run(cb(u, ctx()))
        self.assertEqual(u.message.sent, [])

    def test_crm_dashboard_dedup_with_back_home(self):
        text, keys = betroxy_crm_ui.dashboard({})
        data = callbacks(keys)
        self.assertIn("TEAM DASHBOARD", text)
        for moved in ("campaign_add_single", "campaign_pixel_manager", "v94_admin_home", "btxcrm:guide"):
            self.assertNotIn(moved, data)
        self.assertIn("btxcrm:q:new:0", data)
        self.assertEqual(data[-2:], ["adm2:cat:crm", "adm2:home"])

    def test_phonepe_needs_confirmation(self):
        import v89_rewards_mobile_ai as v89
        self.assertNotIn("v102_phonepe_test", callbacks(v89._reward_center_keyboard()))
        guard = self.h.handler("phonepe", pattern="v102_phonepe_test")
        c = ctx()
        u = update(data="v102_phonepe_test")
        with self.assertRaises(ApplicationHandlerStop):
            run(guard(u, c))  # no pass -> confirm card
        self.assertIn("CONFIRM PHONEPE", u.callback_query.edits[0]["text"])
        run(guard(update(data="v102_phonepe_test"), c))  # pass present -> falls through to v102
        with self.assertRaises(ApplicationHandlerStop):
            run(guard(update(data="v102_phonepe_test"), c))  # pass is single-use

    def test_post_10am_now_confirm(self):
        h = [x for x in self.h.app.handlers[-111] if isinstance(x, CommandHandler)][0]
        c = ctx()
        run(h.callback(update(text="/post_10am_now"), c))
        self.assertEqual(self.h.posted, [])
        token = next(iter(c.user_data["adm2_pending"]))
        u = update(data=f"adm2:ok:{token}")
        with self.assertRaises(ApplicationHandlerStop):
            run(self.h.handler("adm2", pattern="adm2:ok:x")(u, c))
        self.assertEqual(len(self.h.posted), 1)

    def test_kill_switch_restores_classic(self):
        self.h.panel.set_enabled(False)  # DB down -> in-memory
        self.assertEqual(callbacks(self.h.v94.v94_admin_menu()), ["btxcrm:home", "v91_admincat:system"])
        self.assertEqual(self.h.v94._admin_home_text(), "OLD HOME")
        run(self.h.handler("admin", command="admin")(update(), ctx()))  # no stop: classic /admin runs

    def test_protected_files_untouched_and_entrypoint_loads_module(self):
        import betroxy_crm_preflight
        self.assertTrue(PROTECTED <= set(betroxy_crm_preflight.PROTECTED))
        with open("betroxy_admin_authority.py", encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("betroxy_admin_home_v2.prepare(prior.production, store)", src)


if __name__ == "__main__":
    unittest.main()
