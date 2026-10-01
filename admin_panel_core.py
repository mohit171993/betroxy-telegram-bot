"""Shared grouped-admin-panel core (v2) used by betroxy_admin_home_v2.

Copied from the fantzo-sports-bot admin_home_v2 core.

Replaces only the *home screen* shown by /admin (and /reports, /crm where
those open the dashboard) with six categories. Every existing callback_data
and command handler is kept and still reached from the new screens; new
navigation uses the separate ``adm2:`` prefix.

Kill switch (no Railway variable needed):
  * code default ``DEFAULT_ENABLED``;
  * DB setting ``admin_ui_v2`` ('0' = classic dashboard) toggled by an admin
    with ``/adminui off`` / ``/adminui on`` or Tools -> Classic menu.
Admin gating is delegated to the bot's existing admin checks.
"""
from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass, field
from html import escape
from typing import Callable

from telegram import InlineKeyboardButton as Button
from telegram import InlineKeyboardMarkup as Markup
from telegram import WebAppInfo
from telegram.ext import CommandHandler

log = logging.getLogger(__name__)

VERSION = "admin-home-v2-2026-10-01"
PREFIX = "adm2:"
DEFAULT_ENABLED = True
SETTING_KEY = "admin_ui_v2"
STATUS_TTL = 60
CONFIRM_TTL = 600
DASH = "-"


# ---------------------------------------------------------------- model
@dataclass
class Cmd:
    """A slash command surfaced as a button (opens a help card)."""
    name: str
    label: str
    desc: str
    usage: str = ""
    confirm: bool = False


@dataclass
class Act:
    """A button that runs an in-panel action (adm2:act:<key>)."""
    key: str
    label: str


@dataclass
class Web:
    label: str
    url_fn: Callable[[], str]


@dataclass
class Category:
    key: str
    label: str
    title: str
    items: list = field(default_factory=list)  # (label, callback_data) | Cmd | Act | Web
    note: str = ""


def rows2(buttons):
    """Max two buttons per row."""
    return [buttons[i:i + 2] for i in range(0, len(buttons), 2)]


def _not_modified(exc) -> bool:
    return "message is not modified" in str(exc).lower()


class Panel:
    def __init__(self, *, brand: str, is_admin, db, status_parts, categories,
                 classic=None, actions=None):
        self.brand = brand
        self.is_admin = is_admin
        self.db = db
        self.status_parts = status_parts
        self.categories = {c.key: c for c in categories}
        self.order = [c.key for c in categories]
        self.classic = classic
        self.actions = dict(actions or {})
        self.cmds = {}
        for c in categories:
            for it in c.items:
                if isinstance(it, Cmd):
                    self.cmds[it.name] = (c.key, it)
        self._status_cache = (0.0, "")
        self.confirm_wrapped = []

    # ------------------------------------------------------------ settings
    def enabled(self) -> bool:
        try:
            with self.db() as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
                row = conn.execute("SELECT value FROM settings WHERE key=?", (SETTING_KEY,)).fetchone()
            if row is None:
                return DEFAULT_ENABLED
            return str(row[0]).strip() != "0"
        except Exception:
            return DEFAULT_ENABLED

    def set_enabled(self, on: bool) -> None:
        with self.db() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",
                         (SETTING_KEY, "1" if on else "0"))

    # ------------------------------------------------------------ status
    def status_line(self, force: bool = False) -> str:
        ts, cached = self._status_cache
        if cached and not force and time.time() - ts < STATUS_TTL:
            return cached
        parts = []
        for label_fn in self.status_parts:
            try:
                value = label_fn()
            except Exception:
                log.warning("admin v2 status part failed", exc_info=False)
                value = None
            if value is not None:
                parts.append(value)
        line = " · ".join(parts)
        self._status_cache = (time.time(), line)
        return line

    # ------------------------------------------------------------ screens
    def home(self, force: bool = False):
        text = (f"🏠 <b>{escape(self.brand)} ADMIN</b> · {self.status_line(force)}\n"
                "<i>Choose a section 👇</i>")
        btns = [Button(self.categories[k].label, callback_data=f"{PREFIX}cat:{k}") for k in self.order]
        rows = rows2(btns) + [[Button("🔄 Refresh", callback_data=f"{PREFIX}home:r")]]
        return text, Markup(rows)

    def nav(self, back: str):
        return [Button("⬅️ Back", callback_data=back), Button("🏠 Home", callback_data=f"{PREFIX}home")]

    def _item_button(self, it):
        if isinstance(it, Cmd):
            return Button(it.label, callback_data=f"{PREFIX}cmd:{it.name}")
        if isinstance(it, Act):
            return Button(it.label, callback_data=f"{PREFIX}act:{it.key}")
        if isinstance(it, Web):
            try:
                url = it.url_fn()
            except Exception:
                url = ""
            return Button(it.label, web_app=WebAppInfo(url=url)) if url else None
        label, data = it
        return Button(label, callback_data=data)

    def category(self, key: str):
        cat = self.categories[key]
        btns = [b for b in (self._item_button(it) for it in self._items(cat)) if b is not None]
        text = f"<b>{escape(cat.title)}</b> · {self.status_line()}"
        if cat.note:
            text += f"\n<i>{escape(cat.note)}</i>"
        return text, Markup(rows2(btns) + [self.nav(f"{PREFIX}home")])

    def _items(self, cat):
        items = []
        for it in cat.items:
            items.extend(it() if callable(it) and not isinstance(it, (Cmd, Act, Web)) else [it])
        return items

    def cmd_card(self, name: str):
        key, cmd = self.cmds[name]
        lines = [f"<b>{escape(cmd.label)}</b>", escape(cmd.desc), "", f"👉 Tap to run: /{cmd.name}"]
        if cmd.usage:
            lines.append(f"Usage: <code>{escape(cmd.usage)}</code>")
        if cmd.confirm:
            lines.append("⚠️ You will be asked to confirm before it runs.")
        return "\n".join(lines), Markup([self.nav(f"{PREFIX}cat:{key}")])

    def text_screen(self, text: str, back: str):
        return text, Markup([self.nav(back)])

    # ------------------------------------------------------------ io
    async def show(self, update, text, markup, edit: bool = True):
        query = getattr(update, "callback_query", None)
        kwargs = dict(text=text, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)
        if edit and query is not None and query.message is not None:
            try:
                await query.edit_message_text(**kwargs)
                return
            except Exception as exc:
                if _not_modified(exc):
                    return
                log.info("admin v2 edit failed (%s); sending new message", type(exc).__name__)
        message = (query.message if query is not None else None) or update.effective_message
        await message.reply_text(**kwargs)

    async def send_home(self, update, context):
        text, markup = self.home()
        await self.show(update, text, markup, edit=False)

    # ------------------------------------------------------------ callbacks
    async def handle(self, update, context) -> bool:
        """Handle adm2:* callbacks. Returns True if consumed."""
        query = update.callback_query
        data = str(getattr(query, "data", "") or "")
        if not data.startswith(PREFIX):
            return False
        user = update.effective_user
        if not user or not self.is_admin(user.id):
            try:
                await query.answer("Restricted", show_alert=True)
            except Exception:
                pass
            return True
        parts = data[len(PREFIX):].split(":")
        kind = parts[0]
        arg = parts[1] if len(parts) > 1 else ""
        answered = False
        try:
            if kind == "home":
                text, markup = self.home(force=(arg == "r"))
                await self._answer(query, "Refreshed" if arg == "r" else None)
                answered = True
                await self.show(update, text, markup)
            elif kind == "cat" and arg in self.categories:
                await self._answer(query); answered = True
                text, markup = self.category(arg)
                await self.show(update, text, markup)
            elif kind == "cmd" and arg in self.cmds:
                await self._answer(query); answered = True
                text, markup = self.cmd_card(arg)
                await self.show(update, text, markup)
            elif kind == "act" and arg in self.actions:
                result = await self.actions[arg](self, update, context)
                answered = True
                if result:
                    text, markup = result
                    await self.show(update, text, markup)
            elif kind in {"ok", "no"}:
                answered = True
                await self._resolve_confirm(update, context, arg, kind == "ok")
            else:
                await self._answer(query, "This button has expired. Opening Home.")
                answered = True
                text, markup = self.home()
                await self.show(update, text, markup)
        finally:
            if not answered:
                await self._answer(query)
        return True

    @staticmethod
    async def _answer(query, text=None, alert=False):
        try:
            await query.answer(text, show_alert=alert) if text else await query.answer()
        except Exception:
            pass

    # ------------------------------------------------------------ confirm
    def confirm_wrap(self, name: str, original, describe: Callable, requires_args: bool = False):
        panel = self

        async def wrapped(update, context):
            user = update.effective_user
            message = update.effective_message
            if (not user or not message or not panel.is_admin(user.id)
                    or getattr(context, "_adm2_confirmed", False)):
                return await original(update, context)
            if requires_args and not list(getattr(context, "args", None) or []):
                return await original(update, context)
            pending = context.user_data.setdefault("adm2_pending", {})
            now = time.time()
            for tok in [t for t, v in pending.items() if now - v["ts"] > CONFIRM_TTL]:
                pending.pop(tok, None)
            token = secrets.token_hex(4)
            args = list(getattr(context, "args", None) or [])
            pending[token] = {"cmd": name, "update": update, "args": args, "ts": now,
                              "original": original}
            try:
                body = describe(args)
            except Exception:
                body = f"Run /{name}?"
            await message.reply_text(
                f"⚠️ <b>CONFIRM /{escape(name)}</b>\n{body}\n\n<i>Expires in {CONFIRM_TTL // 60} min.</i>",
                parse_mode="HTML",
                reply_markup=Markup([[Button("✅ Confirm", callback_data=f"{PREFIX}ok:{token}"),
                                      Button("✖️ Cancel", callback_data=f"{PREFIX}no:{token}")]]),
                disable_web_page_preview=True,
            )

        wrapped._adm2_wrapped = True
        wrapped._adm2_original = original
        return wrapped

    async def _resolve_confirm(self, update, context, token: str, ok: bool):
        query = update.callback_query
        pending = context.user_data.setdefault("adm2_pending", {})
        item = pending.pop(token, None)
        if not item or time.time() - item["ts"] > CONFIRM_TTL:
            await self._answer(query, "This confirmation expired. Run the command again.", alert=True)
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
            return
        if not ok:
            await self._answer(query, "Cancelled")
            try:
                await query.edit_message_text(f"✖️ /{item['cmd']} cancelled. Nothing was run.")
            except Exception:
                pass
            return
        await self._answer(query, "Running…")
        try:
            await query.edit_message_text(f"⏳ Running /{item['cmd']}…")
        except Exception:
            pass
        context.args = item["args"]
        context._adm2_confirmed = True
        try:
            await item["original"](item["update"], context)
        finally:
            context._adm2_confirmed = False

    def wrap_commands(self, application, specs) -> list:
        """specs: {command: (describe, requires_args)}; patches matching handlers in place."""
        done = []
        for group_handlers in application.handlers.values():
            for handler in group_handlers:
                if not isinstance(handler, CommandHandler):
                    continue
                for name, (describe, requires_args) in specs.items():
                    if name in handler.commands and not getattr(handler.callback, "_adm2_wrapped", False):
                        handler.callback = self.confirm_wrap(name, handler.callback, describe, requires_args)
                        done.append(name)
        self.confirm_wrapped = sorted(set(done))
        return self.confirm_wrapped

    def add_toggle_command(self, application, group: int = -6):
        panel = self

        async def adminui(update, context):
            user = update.effective_user
            message = update.effective_message
            if not user or not message or not panel.is_admin(user.id):
                return
            arg = (list(context.args or []) + [""])[0].lower()
            if arg in {"on", "off"}:
                panel.set_enabled(arg == "on")
            state = "ON (grouped menu)" if panel.enabled() else "OFF (classic dashboard)"
            await message.reply_text(f"Admin menu v2 is {state}.\nUse /adminui on or /adminui off.")

        application.add_handler(CommandHandler("adminui", adminui), group=group)


# ---------------------------------------------------------------- shared actions
async def _act_classic(panel, update, context):
    await Panel._answer(update.callback_query)
    if panel.classic is None:
        return None
    text, markup = panel.classic()
    message = update.callback_query.message or update.effective_message
    await message.reply_text(text, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)
    return None


async def _act_ui_off(panel, update, context):
    panel.set_enabled(False)
    await Panel._answer(update.callback_query, "Classic dashboard restored. /adminui on to switch back.", alert=True)
    return None


def _mode_label():
    import bot_mode_runtime as mode_rt
    mode = str(mode_rt._mode() or "").lower()
    return {"liveline": "🔀 Live Line", "full": "🔀 Full"}.get(mode, f"🔀 {mode or DASH}")


def _num(value):
    try:
        return f"{int(value):,}"
    except Exception:
        return DASH
