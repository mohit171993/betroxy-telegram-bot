"""BETROXY grouped admin home (v2): one admin home with six categories.

Overlay only: protected baseline files (see betroxy_crm_preflight.PROTECTED)
are never edited. Loaded from betroxy_admin_authority.py; every existing
callback keeps its handler, and the new screens just link to them.

* /admin, admin /start, "admin_home" and "v94_admin_home" all open one Home.
* /crm and "btxcrm:home" keep the CRM lead dashboard (stats), now with only
  the lead queues + Back/Home (duplicates moved to their category).
* The live PhonePe ₹30 test moves from Reward Center to Tools and needs a
  confirmation tap; /post_10am_now asks for confirmation.
* Kill switch: code default, or /adminui off|on (stored in
  btx_admin_ui_settings, created only when first used).
"""
from __future__ import annotations

import asyncio
import logging
import time
from html import escape

from telegram import InlineKeyboardButton as Button
from telegram import InlineKeyboardMarkup as Markup
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler, CommandHandler

from admin_panel_core import (DASH, PREFIX, STATUS_TTL, Act, Category, Cmd, Panel,
                              _act_classic, _num, rows2)

log = logging.getLogger(__name__)
VERSION = "btx-admin-home-v2-2026-10-01"
DEFAULT_ENABLED = True
GROUP = -10006  # before betroxy_crm_ui (-10003/-10004); after universal verification (-20000)
PHONEPE_CB = "v102_phonepe_test"
PHONEPE_PASS_TTL = 120


class BetroxyPanel(Panel):
    def __init__(self, *, bot, store, **kwargs):
        self.bot, self.store = bot, store
        self._memory_enabled = None
        super().__init__(**kwargs)

    # kill switch (Postgres; read is best-effort, table created on first write)
    def enabled(self) -> bool:
        if self._memory_enabled is not None:
            return self._memory_enabled
        try:
            with self.bot.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT value FROM btx_admin_ui_settings WHERE key=%s", ("admin_ui_v2",))
                    row = cur.fetchone()
            value = (row or {}).get("value") if isinstance(row, dict) else (row[0] if row else None)
            self._memory_enabled = DEFAULT_ENABLED if value is None else str(value) != "0"
        except Exception:
            self._memory_enabled = DEFAULT_ENABLED
        return self._memory_enabled

    def set_enabled(self, on: bool) -> None:
        self._memory_enabled = bool(on)
        try:
            with self.bot.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("CREATE TABLE IF NOT EXISTS btx_admin_ui_settings (key TEXT PRIMARY KEY, value TEXT)")
                    cur.execute("INSERT INTO btx_admin_ui_settings(key,value) VALUES(%s,%s) "
                                "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
                                ("admin_ui_v2", "1" if on else "0"))
        except Exception:
            log.warning("BTX_ADMIN_V2_TOGGLE_NOT_PERSISTED in_memory=%s", on)

    # status: computed off the event loop, rendered from cache
    def compute_status(self) -> str:
        from betroxy_crm_store import select_queue
        try:
            people = self.store.snapshot()
            total = len(people)
            verified = sum(bool(p.get("verified")) for p in people.values())
            parts = [f"👥 {_num(total)} leads", f"✅ {_num(verified)} verified",
                     f"🆕 {_num(len(select_queue(people, 'new')))} new",
                     f"⏰ {_num(len(select_queue(people, 'due')))} due"]
        except Exception:
            log.warning("BTX_ADMIN_V2_STATUS_UNAVAILABLE")
            parts = [f"👥 {DASH}", f"✅ {DASH}", f"🆕 {DASH}", f"⏰ {DASH}"]
        line = " · ".join(parts)
        self._status_cache = (time.time(), line)
        return line

    async def warm(self, force: bool = False):
        ts, cached = self._status_cache
        if force or not cached or time.time() - ts >= STATUS_TTL:
            await asyncio.to_thread(self.compute_status)

    def status_line(self, force: bool = False) -> str:
        return self._status_cache[1] or f"👥 {DASH} · ✅ {DASH} · ⏰ {DASH}"


def build_categories(phonepe_available: bool):
    q = lambda label, queue: (label, f"btxcrm:q:{queue}:0")  # noqa: E731
    tools = [
        ("📈 Campaign Control", "campaign_home"), ("➕ Create Link", "campaign_add_single"),
        ("🎯 Pixel Manager", "campaign_pixel_manager"), ("🔗 Creator Links", "campaign_links"),
        Cmd("ai", "🤖 AI Assistant", "Ask the reporting AI a question.", "/ai your question"),
    ]
    if phonepe_available:
        tools.append(Act("phonepe", "🧪 PhonePe ₹30 Test"))
    tools += [Act("classic", "🗂 Classic Menu"), Act("ui_off", "↩️ Use Classic Always")]
    return [
        Category("crm", "👥 Users & CRM", "👥 USERS & CRM", [
            q("🆕 New", "new"), q("⏰ Due Now", "due"), q("👥 All Leads", "all"), q("📱 Saved Mobiles", "mobile"),
            ("📋 All Queues", "btxcrm:home"), ("🔎 Search", "btxcrm:search"),
            ("💬 Business Inbox", "business_home"), ("👥 Affiliates", "v91_admincat:affiliates"),
            ("👤 Customer View", "v94_customer_view"), ("🧪 Test Account", "btxcrm:test"),
            ("📚 Team Guide", "btxcrm:guide"),
        ], "Lead cards keep their own ◀/▶ and Dashboard buttons."),
        Category("reports", "📊 Reports", "📊 REPORTS", [
            ("📊 Reporting Center", "v91_reports_home"), ("🎯 Ads / Campaigns", "v91_reports:url"),
            ("🧠 Intelligence", "intel_home"), ("📈 Bot Analytics", "bot_analytics"),
            ("👥 Affiliate Report", "admin_report"), ("📱 Mobile Report", "v89_mobile_report"),
            ("📋 Instagram", "reports_instagram"), ("📈 Landing", "reports_landing"),
            ("🎁 Engagement", "v91_reports:engage"), ("📦 Summary PDF", "v91_summary_pack"),
            ("📥 Leads CSV", "btxcrm:export"),
            Cmd("igstats", "📸 IG Stats", "Instagram check statistics.", "/igstats"),
        ], "Files and PDFs arrive as new messages."),
        Category("broadcast", "📣 Broadcast & Posts", "📣 BROADCAST & POSTS", [
            Cmd("banner_manager", "🖼 Banner Manager", "Manage channel banners."),
            Cmd("quiz_banners", "🏆 Quiz Banners", "Daily quiz banner set."),
            Cmd("welcome_banners", "👋 Welcome Banners", "Welcome banner set."),
            Cmd("bulk_banners", "📤 Bulk Banners", "Upload many banners at once."),
            Cmd("mega_banners", "🏆 Mega Banners",
                "Sunday Mega banners. Related: /mega_bulk_banners · /mega_weekday_banners · /mega_fix_banners"),
            Cmd("setup_channel_media", "📺 Channel Media", "Set up channel media. Test with /test_channel_media"),
            Cmd("post_10am_now", "🚀 Post 10 AM Now", "Post the locked 10 AM creative to the public channel.",
                confirm=True),
            Cmd("igtoday", "📸 IG Today", "Today's Instagram checks."),
        ]),
        Category("automation", "🤖 Automation & Rewards", "🤖 AUTOMATION & REWARDS", [
            ("🤖 Automation Status", "btxcrm:automation"), ("🛰 Autopilot", "autopilot_home"),
            ("🎁 Reward Center", "v89_reward_center"), ("💬 Inbox Auto-reply", "business_home"),
        ], "Reward Center toggles apply immediately."),
        Category("settings", "⚙️ Settings & Status", "⚙️ SETTINGS & STATUS", [
            Cmd("betroxy_status", "📟 Bot Status", "Live bot health and configuration."),
            Cmd("ai_status", "🧠 AI Status", "Reporting AI status."),
            ("🔌 Inbox Connection", "biz_connection_status"), ("🎨 Landing Design", "theme_home"),
        ]),
        Category("tools", "🧰 Tools & Campaigns", "🧰 TOOLS & CAMPAIGNS", tools,
                 "/adminui on|off switches the menu style."),
    ]


def _strip_phonepe(markup):
    rows = [[b for b in row if getattr(b, "callback_data", None) != PHONEPE_CB]
            for row in getattr(markup, "inline_keyboard", []) or []]
    return Markup([r for r in rows if r])


def prepare(production, store):
    """Call after betroxy_crm_ui.prepare(); hooks in at the app's post_init."""
    bot = production.bot
    import betroxy_crm_ui as crm_ui

    old_dashboard = crm_ui.dashboard
    phonepe_available = True
    panel = BetroxyPanel(
        bot=bot, store=store, brand="BETROXY", is_admin=lambda uid: bool(bot.is_admin(uid)),
        db=bot.get_db, status_parts=[], categories=build_categories(phonepe_available),
        classic=None, actions={},
    )
    classic_state = {}

    def authorized(update):
        user, chat = update.effective_user, update.effective_chat
        return bool(user and chat and str(chat.type) == "private" and bot.is_admin(user.id))

    # CRM dashboard: same stats text, queues only + Back/Home (duplicates removed)
    def dashboard(people):
        text, old_keys = old_dashboard(people)
        if not panel.enabled():
            return text, old_keys
        moved = {"v91_reports:url", "btxcrm:automation", "btxcrm:test", "btxcrm:guide",
                 "campaign_add_single", "campaign_pixel_manager", "v94_admin_home", "btxcrm:export"}
        buttons = [b for row in old_keys.inline_keyboard for b in row
                   if getattr(b, "callback_data", None) not in moved]
        return text, Markup(rows2(buttons) + [panel.nav(f"{PREFIX}cat:crm")])

    crm_ui.dashboard = dashboard

    async def act_phonepe(p, update, context):
        await Panel._answer(update.callback_query)
        context.user_data["adm2_phonepe_pass"] = time.time() + PHONEPE_PASS_TTL
        text = ("⚠️ <b>CONFIRM PHONEPE B2B ₹30 TEST</b>\n"
                "This requests a <b>real ₹30 PhonePe voucher</b> from Giftport for the test account.\n"
                f"<i>Confirm within {PHONEPE_PASS_TTL // 60} min.</i>")
        return text, Markup([[Button("✅ Confirm ₹30 test", callback_data=PHONEPE_CB),
                              Button("✖️ Cancel", callback_data=f"{PREFIX}act:phonepe_cancel")],
                             panel.nav(f"{PREFIX}cat:tools")])

    async def act_phonepe_cancel(p, update, context):
        context.user_data.pop("adm2_phonepe_pass", None)
        await Panel._answer(update.callback_query, "Cancelled")
        return p.category("tools")

    async def act_classic(p, update, context):
        await Panel._answer(update.callback_query)
        message = update.callback_query.message or update.effective_message
        old_text = classic_state.get("text")
        old_menu = classic_state.get("menu")
        if old_text and old_menu:
            await message.reply_text(old_text(), parse_mode="HTML", reply_markup=old_menu(),
                                     disable_web_page_preview=True)
        return None

    async def act_ui_off(p, update, context):
        await asyncio.to_thread(p.set_enabled, False)
        await Panel._answer(update.callback_query, "Classic admin restored. /adminui on to switch back.", alert=True)
        return None

    panel.actions.update({"phonepe": act_phonepe, "phonepe_cancel": act_phonepe_cancel,
                          "classic": act_classic, "ui_off": act_ui_off})

    def home_text():
        return panel.home()[0]

    async def admin_command(update, context):
        if not authorized(update) or not panel.enabled():
            return
        context.user_data.pop("btxcrm_pending", None)
        context.user_data.pop("btxcrm_search", None)
        await panel.warm()
        await panel.send_home(update, context)
        raise ApplicationHandlerStop

    async def adm2_callback(update, context):
        q = update.callback_query
        if not q or not authorized(update):
            if q:
                await Panel._answer(q, "Restricted", alert=True)
            raise ApplicationHandlerStop
        data = str(q.data or "")
        await panel.warm(force=data == f"{PREFIX}home:r")
        await panel.handle(update, context)
        raise ApplicationHandlerStop

    async def home_callback(update, context):
        # admin_home / v94_admin_home -> the single Home
        q = update.callback_query
        if not q or not authorized(update) or not panel.enabled():
            return
        await Panel._answer(q)
        await panel.warm()
        text, markup = panel.home()
        await panel.show(update, text, markup)
        raise ApplicationHandlerStop

    async def phonepe_guard(update, context):
        q = update.callback_query
        if not q or not authorized(update):
            return
        expiry = context.user_data.pop("adm2_phonepe_pass", 0)
        if expiry and time.time() <= expiry:
            log.warning("BTX_ADMIN_V2_PHONEPE_CONFIRMED uid=%s", update.effective_user.id)
            return  # confirmed: fall through to the existing v102 handler
        result = await act_phonepe(panel, update, context)
        await panel.show(update, *result)
        raise ApplicationHandlerStop

    async def adminui(update, context):
        if not authorized(update):
            return
        arg = (list(context.args or []) + [""])[0].lower()
        if arg in {"on", "off"}:
            await asyncio.to_thread(panel.set_enabled, arg == "on")
        state = "ON (grouped menu)" if panel.enabled() else "OFF (classic menus)"
        await update.effective_message.reply_text(f"Admin menu v2 is {state}.\nUse /adminui on or /adminui off.")
        raise ApplicationHandlerStop

    async def post_init(app, previous_post_init):
        if previous_post_init:
            await previous_post_init(app)
        import v94_admin_mode_cleanup as v94
        import v91_admin_categories_reporting_hub as v91
        classic_state["text"] = v94._admin_home_text
        classic_state["menu"] = v94.v94_admin_menu
        old_home_text, old_menu = v94._admin_home_text, v94.v94_admin_menu

        def menu():
            return panel.home()[1] if panel.enabled() else old_menu()

        def text():
            return home_text() if panel.enabled() else old_home_text()

        # Every legacy path that rendered the admin home now renders the single Home.
        v94.v94_admin_menu = menu
        v94._admin_home_text = text
        v91.v91_admin_menu = menu
        bot.admin_menu = menu

        # PhonePe test leaves Reward Center (now in Tools, behind a confirmation).
        mods = []
        for name in ("v89_rewards_mobile_ai", "v97_giftport_live_adapter"):
            try:
                mods.append(__import__(name))
            except Exception:
                pass
        for mod in mods:
            original = getattr(mod, "_reward_center_keyboard", None)
            if original and not getattr(original, "_adm2", False):
                def stripped(original=original):
                    return _strip_phonepe(original())
                stripped._adm2 = True
                mod._reward_center_keyboard = stripped

        app.add_handler(CommandHandler("admin", admin_command), group=GROUP)
        app.add_handler(CommandHandler("adminui", adminui), group=GROUP)
        app.add_handler(CallbackQueryHandler(adm2_callback, pattern=r"^adm2:"), group=GROUP)
        app.add_handler(CallbackQueryHandler(home_callback, pattern=r"^(admin_home|v94_admin_home)$"), group=GROUP)
        app.add_handler(CallbackQueryHandler(phonepe_guard, pattern=rf"^{PHONEPE_CB}$"), group=GROUP)
        wrapped = panel.wrap_commands(app, {
            "post_10am_now": (lambda args: "Post the locked <b>10 AM creative</b> to the public channel now?", False),
        })
        try:
            await asyncio.to_thread(panel.compute_status)
        except Exception:
            pass
        log.warning("BTX_ADMIN_HOME_V2_READY version=%s enabled=%s confirm=%s categories=%s",
                    VERSION, panel.enabled(), wrapped, len(panel.order))

    # production.main() imports more overlays (e.g. /post_10am_now) and wraps
    # post_init again, so hook in when the bot actually starts: runs last.
    previous_main = bot.main

    def main_with_admin_home_v2(*args, **kwargs):
        previous_post_init = bot.post_init

        async def admin_home_v2_post_init(app):
            await post_init(app, previous_post_init)

        bot.post_init = admin_home_v2_post_init
        return previous_main(*args, **kwargs)

    bot.main = main_with_admin_home_v2
    log.warning("BTX_ADMIN_HOME_V2_PREPARED version=%s", VERSION)
    return panel
