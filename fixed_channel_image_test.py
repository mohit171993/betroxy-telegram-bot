"""Admin-only approval test for the fixed BETROXY 10AM channel creative.

This module does NOT connect to or publish in the Telegram channel. It uploads
one fixed JPG privately to ADMIN_ID, records Telegram's file_id in an isolated
test table, and lets the admin approve/reject it. Channel integration happens
only in a later explicit step.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

import requests

import bot
import v110_betroxy_daily_challenge as v110

ASSET_KEY = "quiz_open_fixed"
ASSET_LABEL = "10:00 AM IST — Daily Prize Pool"
ASSET_PATH = Path(__file__).resolve().parent / "channel_media" / "quiz_open_fixed.jpg"
_previous_callback = None
_installed = False


def _asset_bytes() -> bytes:
    data = ASSET_PATH.read_bytes()
    if not data.startswith(b"\xff\xd8\xff"):
        raise ValueError("Fixed channel test asset is not a valid JPEG")
    return data


def _asset_sha() -> str:
    return hashlib.sha256(_asset_bytes()).hexdigest()


def _ensure_schema():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS channel_media_fixed_test (
                    asset_key TEXT PRIMARY KEY,
                    source_sha TEXT NOT NULL,
                    telegram_file_id TEXT,
                    preview_message_id BIGINT,
                    status TEXT NOT NULL DEFAULT 'needs_review',
                    approved_by BIGINT,
                    approved_at TIMESTAMPTZ,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            sha = _asset_sha()
            cur.execute("SELECT source_sha FROM channel_media_fixed_test WHERE asset_key=%s", (ASSET_KEY,))
            row = cur.fetchone()
            if not row:
                cur.execute(
                    "INSERT INTO channel_media_fixed_test(asset_key,source_sha,status) VALUES (%s,%s,'needs_review')",
                    (ASSET_KEY, sha),
                )
            elif str(row.get("source_sha") or "") != sha:
                cur.execute(
                    """
                    UPDATE channel_media_fixed_test
                    SET source_sha=%s,telegram_file_id=NULL,preview_message_id=NULL,
                        status='needs_review',approved_by=NULL,approved_at=NULL,updated_at=NOW()
                    WHERE asset_key=%s
                    """,
                    (sha, ASSET_KEY),
                )
        conn.commit()


def _row():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM channel_media_fixed_test WHERE asset_key=%s", (ASSET_KEY,))
            return cur.fetchone()


def _tg(method: str, *, data=None, files=None, timeout=25):
    r = requests.post(f"{v110.TG_API}/{method}", data=data, files=files, timeout=timeout)
    try:
        payload = r.json() if r.content else {}
    except Exception:
        payload = {}
    return bool(r.ok and payload.get("ok")), payload


def _send_preview_once():
    row = _row() or {}
    if str(row.get("status") or "") in {"pending", "approved"} and row.get("telegram_file_id"):
        bot.logger.warning(
            "FIXED_CHANNEL_TEST_PREVIEW skip=already_%s source=fixed_jpg channel_publish=off",
            row.get("status"),
        )
        return False

    markup = json.dumps(
        {
            "inline_keyboard": [[
                {"text": "✅ Approve & Lock", "callback_data": "fixed_channel_test:approve"},
                {"text": "❌ Reject", "callback_data": "fixed_channel_test:reject"},
            ]]
        },
        separators=(",", ":"),
    )
    data = {
        "chat_id": str(bot.ADMIN_ID),
        "caption": (
            "🖼 <b>BETROXY FIXED IMAGE TEST</b>\n\n"
            f"Slot: <b>{ASSET_LABEL}</b>\n\n"
            "This is the fixed approved-style JPG — <b>runtime rendering is OFF for this test</b>.\n"
            "It is private to you and is <b>NOT posted to the channel</b>.\n\n"
            "Approve only if this is the exact creative you want us to use."
        ),
        "parse_mode": "HTML",
        "reply_markup": markup,
    }
    files = {"photo": ("quiz_open_fixed.jpg", _asset_bytes(), "image/jpeg")}
    ok, payload = _tg("sendPhoto", data=data, files=files)
    if not ok:
        bot.logger.error(
            "FIXED_CHANNEL_TEST_PREVIEW_FAILED detail=%s",
            (payload or {}).get("description") if isinstance(payload, dict) else payload,
        )
        return False

    result = payload.get("result") or {}
    photos = result.get("photo") or []
    file_id = str((photos[-1] or {}).get("file_id") or "") if photos else ""
    message_id = result.get("message_id")
    if not file_id:
        bot.logger.error("FIXED_CHANNEL_TEST_PREVIEW_FAILED detail=no_file_id")
        return False

    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE channel_media_fixed_test
                SET telegram_file_id=%s,preview_message_id=%s,status='pending',updated_at=NOW()
                WHERE asset_key=%s
                """,
                (file_id, message_id, ASSET_KEY),
            )
        conn.commit()

    bot.logger.warning(
        "FIXED_CHANNEL_TEST_PREVIEW_SENT asset=%s sha=%s message_id=%s private_admin=on channel_publish=off",
        ASSET_KEY, _asset_sha(), message_id,
    )
    return True


def _worker():
    time.sleep(8)
    try:
        _send_preview_once()
    except Exception:
        bot.logger.exception("FIXED_CHANNEL_TEST_PREVIEW_WORKER_FAILED")


def _install_callback():
    global _previous_callback
    _previous_callback = bot.callback_handler

    async def fixed_channel_test_callback(update, context):
        q = getattr(update, "callback_query", None)
        data = str(getattr(q, "data", "") or "") if q else ""
        if not q or not data.startswith("fixed_channel_test:"):
            return await _previous_callback(update, context)

        if int(q.from_user.id) != int(bot.ADMIN_ID):
            await q.answer("Admin only", show_alert=True)
            return

        action = data.split(":", 1)[1]
        if action == "approve":
            with bot.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE channel_media_fixed_test
                        SET status='approved',approved_by=%s,approved_at=NOW(),updated_at=NOW()
                        WHERE asset_key=%s AND telegram_file_id IS NOT NULL
                        """,
                        (int(q.from_user.id), ASSET_KEY),
                    )
                    changed = cur.rowcount > 0
                conn.commit()
            if not changed:
                await q.answer("No fixed image is pending", show_alert=True)
                return
            await q.answer("Fixed image approved ✅")
            try:
                await q.edit_message_caption(
                    caption=(
                        "✅ <b>FIXED IMAGE APPROVED</b>\n\n"
                        f"Slot: <b>{ASSET_LABEL}</b>\n\n"
                        "Telegram file_id has been captured for the next integration step.\n"
                        "The channel is still <b>NOT connected/publishing</b>."
                    ),
                    parse_mode=bot.ParseMode.HTML,
                    reply_markup=None,
                )
            except Exception:
                bot.logger.exception("FIXED_CHANNEL_TEST_APPROVE_EDIT_FAILED")
            bot.logger.warning(
                "FIXED_CHANNEL_TEST_APPROVED asset=%s admin=%s channel_publish=off",
                ASSET_KEY, q.from_user.id,
            )
            return

        if action == "reject":
            with bot.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE channel_media_fixed_test
                        SET status='rejected',approved_by=NULL,approved_at=NULL,updated_at=NOW()
                        WHERE asset_key=%s
                        """,
                        (ASSET_KEY,),
                    )
                conn.commit()
            await q.answer("Rejected")
            try:
                await q.edit_message_caption(
                    caption=(
                        "❌ <b>FIXED IMAGE REJECTED</b>\n\n"
                        f"Slot: <b>{ASSET_LABEL}</b>\n\n"
                        "Nothing has been published to the channel."
                    ),
                    parse_mode=bot.ParseMode.HTML,
                    reply_markup=None,
                )
            except Exception:
                bot.logger.exception("FIXED_CHANNEL_TEST_REJECT_EDIT_FAILED")
            bot.logger.warning(
                "FIXED_CHANNEL_TEST_REJECTED asset=%s admin=%s channel_publish=off",
                ASSET_KEY, q.from_user.id,
            )
            return

        await q.answer("Unsupported action", show_alert=True)

    bot.callback_handler = fixed_channel_test_callback
    return fixed_channel_test_callback


def install():
    global _installed
    if _installed:
        return bot.callback_handler
    _ensure_schema()
    handler = _install_callback()
    threading.Thread(target=_worker, name="betroxy-fixed-channel-test", daemon=True).start()
    _installed = True
    bot.logger.warning(
        "FIXED_CHANNEL_IMAGE_TEST active=on asset=quiz_open_fixed source=repo_jpg runtime_renderer_for_test=off "
        "private_admin_preview=on channel_publish=off"
    )
    return handler
