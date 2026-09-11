"""BETROXY fixed channel media test rollout.

Runtime rendering is OFF. This phase uses only the exact repository-stored
10:00 AM JPG and sends it privately to the admin for approval. Channel
connection is deliberately deferred until the image is approved.
"""
from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
from pathlib import Path

import requests

import bot

ASSET_KEY = "quiz_open"
ASSET_LABEL = "10:00 AM IST — Daily Prize Pool"
ASSET_SOURCE = "channel_media/quiz_open.jpg.b64"

_v110 = None
_schedule = None
_previous_callback = None
_original_send_text = None
_installed = False


def _source_bytes() -> bytes:
    path = Path(__file__).resolve().parent / ASSET_SOURCE
    raw = b"".join(path.read_bytes().split())
    data = base64.b64decode(raw, validate=True)
    if not data.startswith(b"\xff\xd8\xff"):
        raise ValueError("quiz_open fixed asset is not a valid JPEG")
    return data


def _source_sha() -> str:
    return hashlib.sha256(_source_bytes()).hexdigest()


def _ensure_schema():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS channel_media_assets (
                    asset_key TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    source_sha TEXT NOT NULL,
                    approved_file_id TEXT,
                    pending_file_id TEXT,
                    preview_message_id BIGINT,
                    status TEXT NOT NULL DEFAULT 'needs_review',
                    approved_by BIGINT,
                    approved_at TIMESTAMPTZ,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
        conn.commit()


def _prepare_row():
    _ensure_schema()
    sha = _source_sha()
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM channel_media_assets WHERE asset_key=%s", (ASSET_KEY,))
            row = cur.fetchone()
            if not row:
                cur.execute(
                    "INSERT INTO channel_media_assets(asset_key,label,source_sha,status) VALUES (%s,%s,%s,'needs_review')",
                    (ASSET_KEY, ASSET_LABEL, sha),
                )
            elif str(row.get("source_sha") or "") != sha:
                cur.execute(
                    """
                    UPDATE channel_media_assets
                    SET label=%s,source_sha=%s,approved_file_id=NULL,pending_file_id=NULL,
                        preview_message_id=NULL,status='needs_review',approved_by=NULL,
                        approved_at=NULL,updated_at=NOW()
                    WHERE asset_key=%s
                    """,
                    (ASSET_LABEL, sha, ASSET_KEY),
                )
            else:
                cur.execute(
                    "UPDATE channel_media_assets SET label=%s,updated_at=NOW() WHERE asset_key=%s",
                    (ASSET_LABEL, ASSET_KEY),
                )
        conn.commit()


def _row():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM channel_media_assets WHERE asset_key=%s", (ASSET_KEY,))
            return cur.fetchone()


def _api(method: str, *, data=None, files=None, timeout=25):
    r = requests.post(f"{_v110.TG_API}/{method}", data=data, files=files, timeout=timeout)
    try:
        payload = r.json() if r.content else {}
    except Exception:
        payload = {}
    return bool(r.ok and payload.get("ok")), payload


def _preview_markup():
    return json.dumps(
        {"inline_keyboard": [[
            {"text": "✅ Approve & Lock", "callback_data": "channel_media_test:approve"},
            {"text": "❌ Reject", "callback_data": "channel_media_test:reject"},
        ]]},
        separators=(",", ":"),
    )


def _send_test_preview():
    photo = _source_bytes()
    data = {
        "chat_id": str(bot.ADMIN_ID),
        "caption": (
            "🖼 <b>BETROXY FIXED IMAGE TEST</b>\n\n"
            f"Slot: <b>{ASSET_LABEL}</b>\n\n"
            "This is the fixed image file — <b>runtime rendering is OFF</b>.\n"
            "It is NOT public and the channel is NOT connected yet.\n\n"
            "Approve only if this is the exact image you want us to lock."
        ),
        "parse_mode": "HTML",
        "reply_markup": _preview_markup(),
    }
    files = {"photo": ("quiz_open.jpg", photo, "image/jpeg")}
    ok, payload = _api("sendPhoto", data=data, files=files)
    if not ok:
        bot.logger.error(
            "CHANNEL_FIXED_TEST_PREVIEW_FAILED detail=%s",
            (payload or {}).get("description") if isinstance(payload, dict) else payload,
        )
        return False

    result = payload.get("result") or {}
    photos = result.get("photo") or []
    file_id = str((photos[-1] or {}).get("file_id") or "") if photos else ""
    message_id = result.get("message_id")
    if not file_id:
        bot.logger.error("CHANNEL_FIXED_TEST_PREVIEW_FAILED detail=no_file_id")
        return False

    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE channel_media_assets
                SET pending_file_id=%s,preview_message_id=%s,status='pending',updated_at=NOW()
                WHERE asset_key=%s
                """,
                (file_id, message_id, ASSET_KEY),
            )
        conn.commit()
    bot.logger.warning(
        "CHANNEL_FIXED_TEST_PREVIEW_SENT asset=quiz_open message_id=%s source=fixed_repo_jpg public=off channel_connected=off",
        message_id,
    )
    return True


def _preview_worker():
    time.sleep(7)
    try:
        _prepare_row()
        _send_test_preview()
    except Exception:
        bot.logger.exception("CHANNEL_FIXED_TEST_PREVIEW_WORKER_FAILED")


def _approved_file_id():
    row = _row() or {}
    if str(row.get("status") or "") != "approved":
        return None
    return str(row.get("approved_file_id") or "").strip() or None


def _install_channel_wrapper():
    global _original_send_text
    _original_send_text = _schedule._send_text

    def _fixed_send(chat_id, text, rows=None):
        # Only the 10 AM slot is enabled in this test phase. Other channel posts
        # remain on their existing text path until all four fixed images are approved.
        if str(chat_id) != str(_v110.CHANNEL_CHAT) or "Today's BETROXY Daily Quiz is OPEN" not in str(text or ""):
            return _original_send_text(chat_id, text, rows)
        file_id = _approved_file_id()
        if not file_id:
            return _original_send_text(chat_id, text, rows)
        data = {
            "chat_id": str(chat_id),
            "photo": file_id,
            "caption": str(text),
            "parse_mode": "HTML",
        }
        if rows:
            data["reply_markup"] = json.dumps({"inline_keyboard": rows}, separators=(",", ":"))
        ok, payload = _api("sendPhoto", data=data)
        if ok:
            return ok, payload
        bot.logger.warning("CHANNEL_FIXED_TEST_POST_FAILED fallback=text")
        return _original_send_text(chat_id, text, rows)

    _schedule._send_text = _fixed_send
    _schedule._channel_media_installed = True


def _approve(admin_id: int):
    row = _row() or {}
    pending = str(row.get("pending_file_id") or "").strip()
    if not pending:
        return False
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE channel_media_assets
                SET approved_file_id=pending_file_id,status='approved',approved_by=%s,
                    approved_at=NOW(),updated_at=NOW()
                WHERE asset_key=%s AND pending_file_id IS NOT NULL
                """,
                (int(admin_id), ASSET_KEY),
            )
            changed = cur.rowcount > 0
        conn.commit()
    return changed


def _reject():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE channel_media_assets
                SET status='rejected',pending_file_id=NULL,approved_file_id=NULL,updated_at=NOW()
                WHERE asset_key=%s
                """,
                (ASSET_KEY,),
            )
        conn.commit()


def _install_callback():
    global _previous_callback
    _previous_callback = bot.callback_handler

    async def channel_fixed_test_callback(update, context):
        q = getattr(update, "callback_query", None)
        data = str(getattr(q, "data", "") or "") if q else ""
        if not q or not data.startswith("channel_media_test:"):
            return await _previous_callback(update, context)
        if int(q.from_user.id) != int(bot.ADMIN_ID):
            await q.answer("Admin only", show_alert=True)
            return

        if data == "channel_media_test:approve":
            if not _approve(q.from_user.id):
                await q.answer("No pending fixed image to approve", show_alert=True)
                return
            await q.answer("Approved & locked ✅")
            try:
                await q.edit_message_caption(
                    caption=(
                        "✅ <b>FIXED IMAGE APPROVED & LOCKED</b>\n\n"
                        f"Slot: <b>{ASSET_LABEL}</b>\n\n"
                        "The exact Telegram <code>file_id</code> is saved.\n"
                        "The channel is still NOT connected; we will do that next."
                    ),
                    parse_mode=bot.ParseMode.HTML,
                    reply_markup=None,
                )
            except Exception:
                bot.logger.exception("CHANNEL_FIXED_TEST_APPROVAL_EDIT_FAILED")
            bot.logger.warning("CHANNEL_FIXED_TEST_APPROVED admin=%s", q.from_user.id)
            return

        if data == "channel_media_test:reject":
            _reject()
            await q.answer("Rejected")
            try:
                await q.edit_message_caption(
                    caption=(
                        "❌ <b>FIXED IMAGE REJECTED — NOT PUBLIC</b>\n\n"
                        f"Slot: <b>{ASSET_LABEL}</b>"
                    ),
                    parse_mode=bot.ParseMode.HTML,
                    reply_markup=None,
                )
            except Exception:
                bot.logger.exception("CHANNEL_FIXED_TEST_REJECT_EDIT_FAILED")
            bot.logger.warning("CHANNEL_FIXED_TEST_REJECTED admin=%s", q.from_user.id)
            return

    bot.callback_handler = channel_fixed_test_callback
    return channel_fixed_test_callback


def install(v110, schedule):
    global _v110, _schedule, _installed
    if _installed:
        return bot.callback_handler
    _v110 = v110
    _schedule = schedule
    _prepare_row()
    _install_channel_wrapper()
    handler = _install_callback()
    _installed = True
    threading.Thread(target=_preview_worker, name="betroxy-fixed-image-test", daemon=True).start()
    bot.logger.warning(
        "CHANNEL_MEDIA_FIXED_TEST active=on asset=quiz_open runtime_renderer=off public=off channel_connection=deferred"
    )
    return handler
