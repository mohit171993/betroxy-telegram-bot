"""BETROXY fixed four-image channel media rollout.

Only the four repository-stored quiz creatives are used. Runtime creative
generation is disabled for these scheduled posts. Each creative is sent to the
admin for approval, its Telegram file_id is stored, then that exact file_id is
reused at its scheduled channel slot.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import threading
import time
from pathlib import Path

import requests
from PIL import Image, ImageFile, ImageOps

import bot
import daily_quiz_schedule as schedule

v110 = schedule.v110
ROOT = Path(__file__).resolve().parent
ImageFile.LOAD_TRUNCATED_IMAGES = True

ASSETS = {
    "quiz_open": ("10:00 AM IST — Quiz Open", "quiz_open.jpg.b64", "quiz_open.jpg"),
    "quiz_afternoon": ("4:00 PM IST — Afternoon Reminder", "quiz_afternoon.jpg.b64", "quiz_afternoon.jpg"),
    "quiz_last_chance": ("7:00 PM IST — Last Chance", "quiz_last_chance.jpg.b64", "quiz_last_chance.jpg"),
    "quiz_result": ("9:05 PM IST — Final Result", "quiz_result.jpg.b64", "quiz_result.jpg"),
}

_previous_callback = None
_previous_send_text = None
_installed = False


def _decode_source(asset_key: str) -> bytes:
    """Decode legacy .b64 storage while tolerating old separator characters."""
    _, stored_name, _ = ASSETS[asset_key]
    raw = (ROOT / "channel_media" / stored_name).read_bytes()
    raw = b"".join(raw.split())
    # Old stored assets contain a few non-base64 separator characters. Remove
    # only those separators; do not alter the decoded image payload.
    cleaned = re.sub(rb"[^A-Za-z0-9+/=]", b"", raw)
    cleaned += b"=" * ((4 - len(cleaned) % 4) % 4)
    data = base64.b64decode(cleaned, validate=False)
    if not data.startswith(b"\xff\xd8\xff"):
        raise ValueError(f"{asset_key}: decoded source is not JPEG")
    return data


def _source_sha(asset_key: str) -> str:
    return hashlib.sha256(_decode_source(asset_key)).hexdigest()


def _safe_jpeg(asset_key: str) -> tuple[bytes, int, int]:
    """Re-containerize the same pixels only if Telegram rejects source bytes."""
    source = _decode_source(asset_key)
    with Image.open(io.BytesIO(source)) as im:
        im = ImageOps.exif_transpose(im)
        im.load()
        if im.mode != "RGB":
            im = im.convert("RGB")
        width, height = im.size
        if width < 1 or height < 1:
            raise ValueError(f"{asset_key}: invalid dimensions")
        if width > 10000 or height > 10000:
            scale = min(10000 / width, 10000 / height)
            width, height = max(1, int(width * scale)), max(1, int(height * scale))
            im = im.resize((width, height), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        im.save(out, "JPEG", quality=95, subsampling=0, optimize=True)
        return out.getvalue(), width, height


def _tg(method: str, *, data=None, files=None, timeout=30):
    r = requests.post(f"{v110.TG_API}/{method}", data=data, files=files, timeout=timeout)
    try:
        payload = r.json() if r.content else {}
    except Exception:
        payload = {}
    return bool(r.ok and payload.get("ok")), payload


def _ensure_schema():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS channel_media_fixed_assets (
                    asset_key TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    source_sha TEXT NOT NULL,
                    telegram_file_id TEXT,
                    preview_message_id BIGINT,
                    upload_mode TEXT,
                    status TEXT NOT NULL DEFAULT 'needs_review',
                    approved_by BIGINT,
                    approved_at TIMESTAMPTZ,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            for asset_key, (label, _, _) in ASSETS.items():
                sha = _source_sha(asset_key)
                cur.execute("SELECT source_sha FROM channel_media_fixed_assets WHERE asset_key=%s", (asset_key,))
                row = cur.fetchone()
                if not row:
                    cur.execute(
                        "INSERT INTO channel_media_fixed_assets(asset_key,label,source_sha,status) VALUES (%s,%s,%s,'needs_review')",
                        (asset_key, label, sha),
                    )
                elif str(row.get("source_sha") or "") != sha:
                    cur.execute(
                        """
                        UPDATE channel_media_fixed_assets
                        SET label=%s,source_sha=%s,telegram_file_id=NULL,preview_message_id=NULL,
                            upload_mode=NULL,status='needs_review',approved_by=NULL,approved_at=NULL,updated_at=NOW()
                        WHERE asset_key=%s
                        """,
                        (label, sha, asset_key),
                    )
                else:
                    cur.execute("UPDATE channel_media_fixed_assets SET label=%s WHERE asset_key=%s", (label, asset_key))
        conn.commit()


def _row(asset_key: str):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM channel_media_fixed_assets WHERE asset_key=%s", (asset_key,))
            return cur.fetchone()


def _approved_count() -> int:
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM channel_media_fixed_assets WHERE status='approved'")
            return int((cur.fetchone() or {}).get("n") or 0)


def _markup(asset_key: str):
    return json.dumps({"inline_keyboard": [[
        {"text": "✅ Approve & Lock", "callback_data": f"fixed_media:approve:{asset_key}"},
        {"text": "❌ Reject", "callback_data": f"fixed_media:reject:{asset_key}"},
    ]]}, separators=(",", ":"))


def _upload_preview(asset_key: str, image_bytes: bytes, mode: str):
    label, _, filename = ASSETS[asset_key]
    data = {
        "chat_id": str(bot.ADMIN_ID),
        "caption": (
            "🖼 <b>BETROXY FIXED CHANNEL IMAGE</b>\n\n"
            f"Slot: <b>{label}</b>\nFile: <code>{filename}</code>\n\n"
            "This is the stored final creative. Runtime image generation is OFF.\n"
            "Approve once; the saved Telegram file_id will be reused automatically."
        ),
        "parse_mode": "HTML",
        "reply_markup": _markup(asset_key),
    }
    return (*_tg("sendPhoto", data=data, files={"photo": (filename, image_bytes, "image/jpeg")}), mode)


def _send_preview(asset_key: str):
    row = _row(asset_key) or {}
    if str(row.get("status") or "") in {"pending", "approved"} and row.get("telegram_file_id"):
        bot.logger.warning("FIXED_MEDIA_PREVIEW skip=%s asset=%s", row.get("status"), asset_key)
        return False
    if str(row.get("status") or "") == "rejected":
        bot.logger.warning("FIXED_MEDIA_PREVIEW skip=rejected asset=%s", asset_key)
        return False

    ok, payload, mode = _upload_preview(asset_key, _decode_source(asset_key), "exact_source")
    if not ok:
        reason = (payload or {}).get("description") if isinstance(payload, dict) else str(payload)
        safe, width, height = _safe_jpeg(asset_key)
        bot.logger.warning("FIXED_MEDIA_REENCODE asset=%s reason=%s dimensions=%sx%s", asset_key, reason, width, height)
        ok, payload, mode = _upload_preview(asset_key, safe, "same_visual_reencoded")
    if not ok:
        bot.logger.error("FIXED_MEDIA_PREVIEW_FAILED asset=%s detail=%s", asset_key, (payload or {}).get("description"))
        return False

    result = payload.get("result") or {}
    photos = result.get("photo") or []
    file_id = str((photos[-1] or {}).get("file_id") or "") if photos else ""
    if not file_id:
        bot.logger.error("FIXED_MEDIA_PREVIEW_FAILED asset=%s detail=no_file_id", asset_key)
        return False
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE channel_media_fixed_assets
                SET telegram_file_id=%s,preview_message_id=%s,upload_mode=%s,status='pending',updated_at=NOW()
                WHERE asset_key=%s
                """,
                (file_id, result.get("message_id"), mode, asset_key),
            )
        conn.commit()
    bot.logger.warning("FIXED_MEDIA_PREVIEW_SENT asset=%s mode=%s message_id=%s", asset_key, mode, result.get("message_id"))
    return True


def _preview_worker():
    time.sleep(7)
    for asset_key in ASSETS:
        try:
            _send_preview(asset_key)
        except Exception:
            bot.logger.exception("FIXED_MEDIA_PREVIEW_WORKER_FAILED asset=%s", asset_key)
        time.sleep(0.8)


def _asset_for_post(chat_id, text):
    if str(chat_id) != str(v110.CHANNEL_CHAT):
        return None
    t = str(text or "")
    if "Today's BETROXY Daily Quiz is OPEN" in t:
        return "quiz_open"
    if "Can You Reach Today's Top 3?" in t:
        return "quiz_afternoon"
    if "Only 2 Hours Left" in t:
        return "quiz_last_chance"
    if "BETROXY DAILY CHALLENGE" in t and "FINAL RESULTS" in t:
        return "quiz_result"
    return None


def _text_only(chat_id, text, rows=None):
    data = {"chat_id": str(chat_id), "text": str(text), "parse_mode": "HTML", "disable_web_page_preview": "true"}
    if rows:
        data["reply_markup"] = json.dumps({"inline_keyboard": rows}, separators=(",", ":"))
    return _tg("sendMessage", data=data)


def _install_channel_wrapper():
    global _previous_send_text
    _previous_send_text = schedule._send_text

    def fixed_send(chat_id, text, rows=None):
        asset_key = _asset_for_post(chat_id, text)
        if not asset_key:
            return _previous_send_text(chat_id, text, rows)
        row = _row(asset_key) or {}
        file_id = str(row.get("telegram_file_id") or "").strip()
        if str(row.get("status") or "") == "approved" and file_id:
            data = {"chat_id": str(chat_id), "photo": file_id, "caption": str(text), "parse_mode": "HTML"}
            if rows:
                data["reply_markup"] = json.dumps({"inline_keyboard": rows}, separators=(",", ":"))
            ok, payload = _tg("sendPhoto", data=data)
            if ok:
                bot.logger.warning("FIXED_MEDIA_CHANNEL_SENT asset=%s file_id_reuse=on", asset_key)
                return ok, payload
            bot.logger.error("FIXED_MEDIA_CHANNEL_FAILED asset=%s fallback=text detail=%s", asset_key, (payload or {}).get("description"))
        bot.logger.warning("FIXED_MEDIA_CHANNEL_TEXT_FALLBACK asset=%s status=%s runtime_renderer=off", asset_key, row.get("status"))
        return _text_only(chat_id, text, rows)

    schedule._send_text = fixed_send


def _approve(asset_key: str, admin_id: int):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE channel_media_fixed_assets
                SET status='approved',approved_by=%s,approved_at=NOW(),updated_at=NOW()
                WHERE asset_key=%s AND telegram_file_id IS NOT NULL
                """,
                (int(admin_id), asset_key),
            )
            changed = cur.rowcount > 0
        conn.commit()
    return changed


def _reject(asset_key: str):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE channel_media_fixed_assets SET status='rejected',approved_by=NULL,approved_at=NULL,updated_at=NOW() WHERE asset_key=%s", (asset_key,))
        conn.commit()


def _install_callback():
    global _previous_callback
    _previous_callback = bot.callback_handler

    async def fixed_media_callback(update, context):
        q = getattr(update, "callback_query", None)
        data = str(getattr(q, "data", "") or "") if q else ""
        if not q or not data.startswith("fixed_media:"):
            return await _previous_callback(update, context)
        if int(q.from_user.id) != int(bot.ADMIN_ID):
            await q.answer("Admin only", show_alert=True)
            return
        parts = data.split(":", 2)
        if len(parts) != 3 or parts[2] not in ASSETS:
            await q.answer("Invalid action", show_alert=True)
            return
        action, asset_key = parts[1], parts[2]
        label, _, filename = ASSETS[asset_key]
        if action == "approve":
            if not _approve(asset_key, q.from_user.id):
                await q.answer("No uploaded image pending", show_alert=True)
                return
            n = _approved_count()
            await q.answer(f"Approved & locked ✅ ({n}/4)")
            try:
                await q.edit_message_caption(
                    caption=(
                        "✅ <b>FIXED IMAGE APPROVED & LOCKED</b>\n\n"
                        f"Slot: <b>{label}</b>\nFile: <code>{filename}</code>\n\n"
                        "The Telegram file_id is saved and this scheduled slot is now live."
                    ),
                    parse_mode=bot.ParseMode.HTML,
                    reply_markup=None,
                )
            except Exception:
                bot.logger.exception("FIXED_MEDIA_APPROVE_EDIT_FAILED asset=%s", asset_key)
            bot.logger.warning("FIXED_MEDIA_APPROVED asset=%s approved_count=%s channel_mapping=live", asset_key, n)
            if n == 4:
                await context.bot.send_message(
                    chat_id=bot.ADMIN_ID,
                    text=(
                        "✅ <b>BETROXY FIXED CHANNEL MEDIA — 4/4 LOCKED</b>\n\n"
                        "10:00 AM IST → quiz_open.jpg\n4:00 PM IST → quiz_afternoon.jpg\n"
                        "7:00 PM IST → quiz_last_chance.jpg\n9:05 PM IST → quiz_result.jpg\n\n"
                        "Runtime image generation is OFF; scheduled posts reuse the approved file_ids."
                    ),
                    parse_mode=bot.ParseMode.HTML,
                )
            return
        if action == "reject":
            _reject(asset_key)
            await q.answer("Rejected")
            try:
                await q.edit_message_caption(
                    caption=(
                        "❌ <b>FIXED IMAGE REJECTED</b>\n\n"
                        f"Slot: <b>{label}</b>\nFile: <code>{filename}</code>\n\n"
                        "This slot remains text-only. No generated substitute will be used."
                    ),
                    parse_mode=bot.ParseMode.HTML,
                    reply_markup=None,
                )
            except Exception:
                bot.logger.exception("FIXED_MEDIA_REJECT_EDIT_FAILED asset=%s", asset_key)
            return
        await q.answer("Unsupported action", show_alert=True)

    bot.callback_handler = fixed_media_callback
    return fixed_media_callback


def install():
    global _installed
    if _installed:
        return bot.callback_handler
    _ensure_schema()
    _install_channel_wrapper()
    handler = _install_callback()
    threading.Thread(target=_preview_worker, name="betroxy-fixed-media-previews", daemon=True).start()
    _installed = True
    bot.logger.warning(
        "FIXED_CHANNEL_MEDIA active=on assets=4 slots=10:00/16:00/19:00/21:05_IST approval_gate=on file_id_reuse=on runtime_renderer=off"
    )
    return handler
