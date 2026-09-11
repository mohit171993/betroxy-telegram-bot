"""Locked BETROXY Telegram channel media library.

Four channel creatives are reviewed once in the admin chat, then the exact
Telegram file_id is reused for scheduled channel posts. Unapproved/rejected
media never goes public: channel delivery falls back to the existing text post.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
from pathlib import Path

import requests

import bot

ASSETS = {
    "quiz_open": {
        "label": "10:00 AM IST — Daily Prize Pool",
        "source": "channel_media/quiz_open.jpg.b64",
    },
    "quiz_afternoon": {
        "label": "4:00 PM IST — Top 3 Challenge",
        "source": "channel_media/quiz_afternoon.jpg.b64",
    },
    "quiz_last_chance": {
        "label": "7:00 PM IST — Final Call",
        "source": "channel_media/quiz_last_chance.jpg.b64",
    },
    "quiz_result": {
        "label": "9:05 PM IST — Winners / Results",
        "source": "channel_media/quiz_result.jpg.b64",
    },
}

_previous_callback = None
_original_send_text = None
_v110 = None
_schedule = None
_installed = False


def _source_bytes(asset_key: str) -> bytes:
    spec = ASSETS[asset_key]
    path = Path(__file__).resolve().parent / spec["source"]
    raw = path.read_text(encoding="ascii").strip()
    data = base64.b64decode(raw, validate=True)
    if not data.startswith(b"\xff\xd8\xff"):
        raise ValueError(f"{asset_key} source is not a JPEG")
    return data


def _source_sha(asset_key: str) -> str:
    return hashlib.sha256(_source_bytes(asset_key)).hexdigest()


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


def _prepare_asset_rows():
    """Register source hashes and invalidate approval only if source changed."""
    _ensure_schema()
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            for asset_key, spec in ASSETS.items():
                sha = _source_sha(asset_key)
                cur.execute(
                    "SELECT * FROM channel_media_assets WHERE asset_key=%s",
                    (asset_key,),
                )
                row = cur.fetchone()
                if not row:
                    cur.execute(
                        """
                        INSERT INTO channel_media_assets(asset_key,label,source_sha,status)
                        VALUES (%s,%s,%s,'needs_review')
                        """,
                        (asset_key, spec["label"], sha),
                    )
                    continue
                if str(row.get("source_sha") or "") != sha:
                    cur.execute(
                        """
                        UPDATE channel_media_assets
                        SET label=%s, source_sha=%s, pending_file_id=NULL,
                            preview_message_id=NULL, status='needs_review',
                            approved_by=NULL, approved_at=NULL, updated_at=NOW()
                        WHERE asset_key=%s
                        """,
                        (spec["label"], sha, asset_key),
                    )
                elif str(row.get("label") or "") != spec["label"]:
                    cur.execute(
                        "UPDATE channel_media_assets SET label=%s,updated_at=NOW() WHERE asset_key=%s",
                        (spec["label"], asset_key),
                    )
        conn.commit()


def _asset_row(asset_key: str):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM channel_media_assets WHERE asset_key=%s",
                (asset_key,),
            )
            return cur.fetchone()


def _approved_file_id(asset_key: str):
    row = _asset_row(asset_key) or {}
    if str(row.get("status") or "") != "approved":
        return None
    file_id = str(row.get("approved_file_id") or "").strip()
    return file_id or None


def _api(method: str, *, data=None, files=None, timeout=25):
    url = f"{_v110.TG_API}/{method}"
    r = requests.post(url, data=data, files=files, timeout=timeout)
    try:
        payload = r.json() if r.content else {}
    except Exception:
        payload = {}
    return bool(r.ok and payload.get("ok")), payload


def _preview_markup(asset_key: str):
    return json.dumps(
        {
            "inline_keyboard": [
                [
                    {"text": "✅ Approve & Lock", "callback_data": f"channel_media:approve:{asset_key}"},
                    {"text": "❌ Reject", "callback_data": f"channel_media:reject:{asset_key}"},
                ]
            ]
        },
        separators=(",", ":"),
    )


def _send_preview(asset_key: str, force=False):
    row = _asset_row(asset_key) or {}
    if not force:
        if str(row.get("status") or "") in {"approved", "rejected", "pending"}:
            return False
        if row.get("pending_file_id"):
            return False

    spec = ASSETS[asset_key]
    photo = _source_bytes(asset_key)
    caption = (
        "🖼 <b>BETROXY CHANNEL MEDIA PREVIEW</b>\n\n"
        f"Slot: <b>{spec['label']}</b>\n\n"
        "This image is <b>NOT public yet</b>.\n"
        "Approve it once to lock the exact Telegram <code>file_id</code> for automatic reuse."
    )
    data = {
        "chat_id": str(bot.ADMIN_ID),
        "caption": caption,
        "parse_mode": "HTML",
        "reply_markup": _preview_markup(asset_key),
    }
    files = {"photo": (f"{asset_key}.jpg", photo, "image/jpeg")}
    ok, payload = _api("sendPhoto", data=data, files=files)
    if not ok:
        bot.logger.error(
            "CHANNEL_MEDIA_PREVIEW_FAILED asset=%s detail=%s",
            asset_key,
            payload.get("description") if isinstance(payload, dict) else payload,
        )
        return False

    result = payload.get("result") or {}
    photos = result.get("photo") or []
    file_id = str((photos[-1] or {}).get("file_id") or "") if photos else ""
    message_id = result.get("message_id")
    if not file_id:
        bot.logger.error("CHANNEL_MEDIA_PREVIEW_FAILED asset=%s detail=no_file_id", asset_key)
        return False

    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE channel_media_assets
                SET pending_file_id=%s,preview_message_id=%s,status='pending',updated_at=NOW()
                WHERE asset_key=%s
                """,
                (file_id, message_id, asset_key),
            )
        conn.commit()
    bot.logger.warning(
        "CHANNEL_MEDIA_PREVIEW_SENT asset=%s message_id=%s public=off",
        asset_key,
        message_id,
    )
    return True


def _preview_worker():
    time.sleep(7)
    try:
        _prepare_asset_rows()
        for asset_key in ASSETS:
            _send_preview(asset_key)
            time.sleep(0.5)
        _check_channel_permission()
    except Exception:
        bot.logger.exception("CHANNEL_MEDIA_PREVIEW_WORKER_FAILED")


def _check_channel_permission():
    """Log channel admin/posting capability; never blocks the rest of the bot."""
    try:
        ok_me, me = _api("getMe", data={})
        if not ok_me:
            raise RuntimeError((me or {}).get("description") or "getMe failed")
        bot_id = int(((me.get("result") or {}).get("id")))
        ok_member, member = _api(
            "getChatMember",
            data={"chat_id": str(_v110.CHANNEL_CHAT), "user_id": str(bot_id)},
        )
        if not ok_member:
            bot.logger.warning(
                "CHANNEL_MEDIA_CHANNEL_CHECK channel=%s admin=unknown can_post=unknown detail=%s",
                _v110.CHANNEL_CHAT,
                (member or {}).get("description"),
            )
            return False
        info = member.get("result") or {}
        status = str(info.get("status") or "")
        can_post = status == "creator" or (status == "administrator" and bool(info.get("can_post_messages")))
        bot.logger.warning(
            "CHANNEL_MEDIA_CHANNEL_CHECK channel=%s status=%s can_post=%s",
            _v110.CHANNEL_CHAT,
            status,
            can_post,
        )
        return can_post
    except Exception:
        bot.logger.exception("CHANNEL_MEDIA_CHANNEL_CHECK_FAILED channel=%s", getattr(_v110, "CHANNEL_CHAT", None))
        return False


def _classify_channel_text(text: str):
    plain = str(text or "")
    if "BETROXY DAILY CHALLENGE — FINAL RESULTS" in plain:
        return "quiz_result"
    if "Only 2 Hours Left — Final Call" in plain:
        return "quiz_last_chance"
    if "Can You Reach Today's Top 3?" in plain:
        return "quiz_afternoon"
    if "Today's BETROXY Daily Quiz is OPEN" in plain:
        return "quiz_open"
    return None


def _send_photo_by_file_id(chat_id, file_id, caption, rows=None):
    data = {
        "chat_id": str(chat_id),
        "photo": str(file_id),
        "caption": str(caption),
        "parse_mode": "HTML",
    }
    if rows:
        data["reply_markup"] = json.dumps({"inline_keyboard": rows}, separators=(",", ":"))
    return _api("sendPhoto", data=data)


def _install_channel_send_wrapper():
    global _original_send_text
    _original_send_text = _schedule._send_text

    def _locked_channel_send(chat_id, text, rows=None):
        if str(chat_id) != str(_v110.CHANNEL_CHAT):
            return _original_send_text(chat_id, text, rows)
        asset_key = _classify_channel_text(text)
        if not asset_key:
            return _original_send_text(chat_id, text, rows)
        file_id = _approved_file_id(asset_key)
        if not file_id:
            bot.logger.warning(
                "CHANNEL_MEDIA_POST asset=%s mode=text_fallback reason=not_approved",
                asset_key,
            )
            return _original_send_text(chat_id, text, rows)

        ok, payload = _send_photo_by_file_id(chat_id, file_id, text, rows)
        if ok:
            bot.logger.warning("CHANNEL_MEDIA_POST asset=%s mode=locked_file_id", asset_key)
            return ok, payload

        # Fail safe: never substitute another image. Preserve the alert as text.
        bot.logger.error(
            "CHANNEL_MEDIA_POST_FAILED asset=%s mode=locked_file_id detail=%s fallback=text",
            asset_key,
            (payload or {}).get("description") if isinstance(payload, dict) else payload,
        )
        return _original_send_text(chat_id, text, rows)

    _schedule._send_text = _locked_channel_send
    _schedule._channel_media_installed = True


def _approve(asset_key: str, admin_id: int):
    row = _asset_row(asset_key) or {}
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
                (int(admin_id), asset_key),
            )
            changed = cur.rowcount > 0
        conn.commit()
    return changed


def _reject(asset_key: str):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE channel_media_assets
                SET status='rejected',pending_file_id=NULL,updated_at=NOW()
                WHERE asset_key=%s
                """,
                (asset_key,),
            )
            changed = cur.rowcount > 0
        conn.commit()
    return changed


def _install_admin_callback():
    global _previous_callback
    _previous_callback = bot.callback_handler

    async def channel_media_callback(update, context):
        q = getattr(update, "callback_query", None)
        data = str(getattr(q, "data", "") or "") if q else ""
        if not q or not data.startswith("channel_media:"):
            return await _previous_callback(update, context)
        if int(q.from_user.id) != int(bot.ADMIN_ID):
            await q.answer("Admin only", show_alert=True)
            return

        parts = data.split(":", 2)
        if len(parts) != 3 or parts[2] not in ASSETS:
            await q.answer("Invalid media action", show_alert=True)
            return
        action, asset_key = parts[1], parts[2]
        label = ASSETS[asset_key]["label"]

        if action == "approve":
            if not _approve(asset_key, q.from_user.id):
                await q.answer("No pending preview to approve", show_alert=True)
                return
            await q.answer("Approved & locked ✅")
            try:
                await q.edit_message_caption(
                    caption=(
                        "✅ <b>APPROVED & LOCKED</b>\n\n"
                        f"Slot: <b>{label}</b>\n\n"
                        "Automatic channel posts will reuse this exact Telegram <code>file_id</code>."
                    ),
                    parse_mode=bot.ParseMode.HTML,
                    reply_markup=None,
                )
            except Exception:
                bot.logger.exception("CHANNEL_MEDIA_APPROVAL_EDIT_FAILED asset=%s", asset_key)
            bot.logger.warning("CHANNEL_MEDIA_APPROVED asset=%s admin=%s", asset_key, q.from_user.id)
            return

        if action == "reject":
            _reject(asset_key)
            await q.answer("Rejected")
            try:
                await q.edit_message_caption(
                    caption=(
                        "❌ <b>REJECTED — NOT PUBLIC</b>\n\n"
                        f"Slot: <b>{label}</b>\n\n"
                        "This image will not be used. Tap Preview Again only when you want to review the bundled creative again."
                    ),
                    parse_mode=bot.ParseMode.HTML,
                    reply_markup=bot.InlineKeyboardMarkup([
                        [bot.InlineKeyboardButton("🔁 Preview Again", callback_data=f"channel_media:retry:{asset_key}")]
                    ]),
                )
            except Exception:
                bot.logger.exception("CHANNEL_MEDIA_REJECT_EDIT_FAILED asset=%s", asset_key)
            bot.logger.warning("CHANNEL_MEDIA_REJECTED asset=%s admin=%s", asset_key, q.from_user.id)
            return

        if action == "retry":
            await q.answer("Sending a new preview…")
            try:
                with bot.get_db() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE channel_media_assets
                            SET status='needs_review',pending_file_id=NULL,preview_message_id=NULL,updated_at=NOW()
                            WHERE asset_key=%s
                            """,
                            (asset_key,),
                        )
                    conn.commit()
                sent = _send_preview(asset_key, force=True)
                if not sent:
                    await q.message.reply_text("⚠️ Could not send the preview. Check production logs.")
            except Exception:
                bot.logger.exception("CHANNEL_MEDIA_RETRY_FAILED asset=%s", asset_key)
            return

        await q.answer("Unsupported media action", show_alert=True)

    bot.callback_handler = channel_media_callback
    return channel_media_callback


def install(v110, schedule):
    global _v110, _schedule, _installed
    if _installed:
        return bot.callback_handler
    _v110 = v110
    _schedule = schedule
    _prepare_asset_rows()
    _install_channel_send_wrapper()
    handler = _install_admin_callback()
    _installed = True
    threading.Thread(target=_preview_worker, name="betroxy-channel-media-preview", daemon=True).start()
    bot.logger.warning(
        "CHANNEL_MEDIA_LIBRARY active=on assets=4 approval_required=on exact_file_id_reuse=on "
        "wrong_image_substitution=off text_fallback=on"
    )
    return handler
