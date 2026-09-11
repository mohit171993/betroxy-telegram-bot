"""BETROXY fixed four-image channel media rollout.

Uses only the four repository-stored quiz creatives. Runtime creative generation is
not used. Each asset is uploaded privately to ADMIN_ID for approval, its Telegram
file_id is saved, and the approved file_id is reused for the matching scheduled
channel post.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
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
    "quiz_open": {
        "label": "10:00 AM IST — Quiz Open",
        "path": ROOT / "channel_media" / "quiz_open.jpg.b64",
        "filename": "quiz_open.jpg",
    },
    "quiz_afternoon": {
        "label": "4:00 PM IST — Afternoon Reminder",
        "path": ROOT / "channel_media" / "quiz_afternoon.jpg.b64",
        "filename": "quiz_afternoon.jpg",
    },
    "quiz_last_chance": {
        "label": "7:00 PM IST — Last Chance",
        "path": ROOT / "channel_media" / "quiz_last_chance.jpg.b64",
        "filename": "quiz_last_chance.jpg",
    },
    "quiz_result": {
        "label": "9:05 PM IST — Final Result",
        "path": ROOT / "channel_media" / "quiz_result.jpg.b64",
        "filename": "quiz_result.jpg",
    },
}

_previous_callback = None
_previous_send_text = None
_installed = False


def _source_bytes(asset_key: str) -> bytes:
    cfg = ASSETS[asset_key]
    raw = b"".join(cfg["path"].read_bytes().split())
    data = base64.b64decode(raw, validate=True)
    if not data.startswith(b"\xff\xd8\xff"):
        raise ValueError(f"{asset_key} source is not JPEG data")
    return data


def _source_sha(asset_key: str) -> str:
    return hashlib.sha256(_source_bytes(asset_key)).hexdigest()


def _telegram_safe_jpeg(asset_key: str) -> tuple[bytes, int, int]:
    """Re-encode the same stored visual only when Telegram needs a clean JPEG."""
    source = _source_bytes(asset_key)
    with Image.open(io.BytesIO(source)) as im:
        im = ImageOps.exif_transpose(im)
        im.load()
        if im.mode != "RGB":
            im = im.convert("RGB")
        width, height = im.size
        if width < 1 or height < 1:
            raise ValueError(f"{asset_key} has invalid dimensions {im.size}")
        # Telegram photo limits are generous; resize only if a source exceeds them.
        if width > 10000 or height > 10000 or max(width, height) / max(1, min(width, height)) > 20:
            scale = min(10000 / width, 10000 / height, 1.0)
            width = max(1, int(width * scale))
            height = max(1, int(height * scale))
            im = im.resize((width, height), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        im.save(out, format="JPEG", quality=95, subsampling=0, optimize=True)
        data = out.getvalue()
    if not data.startswith(b"\xff\xd8\xff"):
        raise ValueError(f"{asset_key} normalized output is not JPEG")
    return data, width, height


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
            for asset_key, cfg in ASSETS.items():
                sha = _source_sha(asset_key)
                cur.execute(
                    "SELECT * FROM channel_media_fixed_assets WHERE asset_key=%s",
                    (asset_key,),
                )
                row = cur.fetchone()
                if not row:
                    cur.execute(
                        """
                        INSERT INTO channel_media_fixed_assets(asset_key,label,source_sha,status)
                        VALUES (%s,%s,%s,'needs_review')
                        """,
                        (asset_key, cfg["label"], sha),
                    )
                elif str(row.get("source_sha") or "") != sha:
                    cur.execute(
                        """
                        UPDATE channel_media_fixed_assets
                        SET label=%s,source_sha=%s,telegram_file_id=NULL,preview_message_id=NULL,
                            upload_mode=NULL,status='needs_review',approved_by=NULL,
                            approved_at=NULL,updated_at=NOW()
                        WHERE asset_key=%s
                        """,
                        (cfg["label"], sha, asset_key),
                    )
                else:
                    cur.execute(
                        "UPDATE channel_media_fixed_assets SET label=%s,updated_at=NOW() WHERE asset_key=%s",
                        (cfg["label"], asset_key),
                    )
        conn.commit()


def _row(asset_key: str):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM channel_media_fixed_assets WHERE asset_key=%s",
                (asset_key,),
            )
            return cur.fetchone()


def _approved_count() -> int:
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS n FROM channel_media_fixed_assets WHERE status='approved'"
            )
            row = cur.fetchone() or {}
            return int(row.get("n") or 0)


def _tg(method: str, *, data=None, files=None, timeout=30):
    r = requests.post(f"{v110.TG_API}/{method}", data=data, files=files, timeout=timeout)
    try:
        payload = r.json() if r.content else {}
    except Exception:
        payload = {}
    return bool(r.ok and payload.get("ok")), payload


def _preview_markup(asset_key: str) -> str:
    return json.dumps(
        {"inline_keyboard": [[
            {"text": "✅ Approve & Lock", "callback_data": f"fixed_media:approve:{asset_key}"},
            {"text": "❌ Reject", "callback_data": f"fixed_media:reject:{asset_key}"},
        ]]},
        separators=(",", ":"),
    )


def _send_photo_upload(asset_key: str, image_bytes: bytes, mode: str):
    cfg = ASSETS[asset_key]
    data = {
        "chat_id": str(bot.ADMIN_ID),
        "caption": (
            "🖼 <b>BETROXY FIXED CHANNEL IMAGE</b>\n\n"
            f"Slot: <b>{cfg['label']}</b>\n"
            f"File: <code>{cfg['filename']}</code>\n\n"
            "This is the repository-stored final creative. Runtime image generation is OFF.\n"
            "Approve it once and Telegram's exact file_id will be reused for scheduled channel posts."
        ),
        "parse_mode": "HTML",
        "reply_markup": _preview_markup(asset_key),
    }
    files = {"photo": (cfg["filename"], image_bytes, "image/jpeg")}
    ok, payload = _tg("sendPhoto", data=data, files=files)
    return ok, payload, mode


def _send_preview(asset_key: str):
    row = _row(asset_key) or {}
    status = str(row.get("status") or "")
    if status in {"pending", "approved"} and row.get("telegram_file_id"):
        bot.logger.warning(
            "FIXED_MEDIA_PREVIEW skip=already_%s asset=%s",
            status, asset_key,
        )
        return False
    if status == "rejected":
        bot.logger.warning("FIXED_MEDIA_PREVIEW skip=rejected asset=%s", asset_key)
        return False

    # First try the exact decoded repository bytes. If Telegram rejects the JPEG
    # container, re-encode the same pixels with Pillow and retry once.
    raw = _source_bytes(asset_key)
    ok, payload, mode = _send_photo_upload(asset_key, raw, "exact_source")
    if not ok:
        first_error = (payload or {}).get("description") if isinstance(payload, dict) else str(payload)
        safe, width, height = _telegram_safe_jpeg(asset_key)
        bot.logger.warning(
            "FIXED_MEDIA_REENCODE asset=%s reason=%s dimensions=%sx%s",
            asset_key, first_error, width, height,
        )
        ok, payload, mode = _send_photo_upload(asset_key, safe, "pillow_same_visual")

    if not ok:
        bot.logger.error(
            "FIXED_MEDIA_PREVIEW_FAILED asset=%s detail=%s",
            asset_key,
            (payload or {}).get("description") if isinstance(payload, dict) else payload,
        )
        return False

    result = payload.get("result") or {}
    photos = result.get("photo") or []
    file_id = str((photos[-1] or {}).get("file_id") or "") if photos else ""
    message_id = result.get("message_id")
    if not file_id:
        bot.logger.error("FIXED_MEDIA_PREVIEW_FAILED asset=%s detail=no_file_id", asset_key)
        return False

    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE channel_media_fixed_assets
                SET telegram_file_id=%s,preview_message_id=%s,upload_mode=%s,
                    status='pending',updated_at=NOW()
                WHERE asset_key=%s
                """,
                (file_id, message_id, mode, asset_key),
            )
        conn.commit()

    bot.logger.warning(
        "FIXED_MEDIA_PREVIEW_SENT asset=%s mode=%s message_id=%s private_admin=on",
        asset_key, mode, message_id,
    )
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


def _plain_text_send(chat_id, text, rows=None):
    payload = {
        "chat_id": str(chat_id),
        "text": str(text),
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }
    if rows:
        payload["reply_markup"] = json.dumps({"inline_keyboard": rows}, separators=(",", ":"))
    return _tg("sendMessage", data=payload)


def _install_channel_wrapper():
    global _previous_send_text
    _previous_send_text = schedule._send_text

    def _fixed_media_send(chat_id, text, rows=None):
        asset_key = _asset_for_post(chat_id, text)
        if not asset_key:
            return _previous_send_text(chat_id, text, rows)

        row = _row(asset_key) or {}
        file_id = str(row.get("telegram_file_id") or "").strip()
        if str(row.get("status") or "") == "approved" and file_id:
            data = {
                "chat_id": str(chat_id),
                "photo": file_id,
                "caption": str(text),
                "parse_mode": "HTML",
            }
            if rows:
                data["reply_markup"] = json.dumps({"inline_keyboard": rows}, separators=(",", ":"))
            ok, payload = _tg("sendPhoto", data=data)
            if ok:
                bot.logger.warning("FIXED_MEDIA_CHANNEL_SENT asset=%s file_id_reuse=on", asset_key)
                return ok, payload
            bot.logger.error(
                "FIXED_MEDIA_CHANNEL_FAILED asset=%s detail=%s fallback=text",
                asset_key,
                (payload or {}).get("description") if isinstance(payload, dict) else payload,
            )

        # Approval gate: no generated substitute. Until the exact file_id is
        # approved, use text only so the wrong creative can never go public.
        bot.logger.warning(
            "FIXED_MEDIA_CHANNEL_TEXT_FALLBACK asset=%s status=%s runtime_renderer=off",
            asset_key, row.get("status"),
        )
        return _plain_text_send(chat_id, text, rows)

    schedule._send_text = _fixed_media_send


def _approve(asset_key: str, admin_id: int) -> bool:
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
            cur.execute(
                """
                UPDATE channel_media_fixed_assets
                SET status='rejected',approved_by=NULL,approved_at=NULL,updated_at=NOW()
                WHERE asset_key=%s
                """,
                (asset_key,),
            )
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
            await q.answer("Invalid fixed-media action", show_alert=True)
            return
        action, asset_key = parts[1], parts[2]
        cfg = ASSETS[asset_key]

        if action == "approve":
            if not _approve(asset_key, q.from_user.id):
                await q.answer("No uploaded image is pending", show_alert=True)
                return
            approved = _approved_count()
            await q.answer(f"Approved & locked ✅ ({approved}/4)")
            try:
                await q.edit_message_caption(
                    caption=(
                        "✅ <b>FIXED IMAGE APPROVED & LOCKED</b>\n\n"
                        f"Slot: <b>{cfg['label']}</b>\n"
                        f"File: <code>{cfg['filename']}</code>\n\n"
                        "Telegram file_id is saved and will be reused automatically at this scheduled slot."
                    ),
                    parse_mode=bot.ParseMode.HTML,
                    reply_markup=None,
                )
            except Exception:
                bot.logger.exception("FIXED_MEDIA_APPROVE_EDIT_FAILED asset=%s", asset_key)
            bot.logger.warning(
                "FIXED_MEDIA_APPROVED asset=%s admin=%s approved_count=%s channel_mapping=live",
                asset_key, q.from_user.id, approved,
            )
            if approved == 4:
                try:
                    await context.bot.send_message(
                        chat_id=bot.ADMIN_ID,
                        text=(
                            "✅ <b>BETROXY FIXED CHANNEL MEDIA — 4/4 LOCKED</b>\n\n"
                            "10:00 AM IST → quiz_open.jpg\n"
                            "4:00 PM IST → quiz_afternoon.jpg\n"
                            "7:00 PM IST → quiz_last_chance.jpg\n"
                            "9:05 PM IST → quiz_result.jpg\n\n"
                            "Runtime image generation is OFF. Scheduled posts now reuse the approved Telegram file_id for each slot."
                        ),
                        parse_mode=bot.ParseMode.HTML,
                    )
                except Exception:
                    bot.logger.exception("FIXED_MEDIA_ALL_APPROVED_NOTICE_FAILED")
            return

        if action == "reject":
            _reject(asset_key)
            await q.answer("Rejected")
            try:
                await q.edit_message_caption(
                    caption=(
                        "❌ <b>FIXED IMAGE REJECTED</b>\n\n"
                        f"Slot: <b>{cfg['label']}</b>\n"
                        f"File: <code>{cfg['filename']}</code>\n\n"
                        "This slot will remain text-only; no generated substitute will be posted."
                    ),
                    parse_mode=bot.ParseMode.HTML,
                    reply_markup=None,
                )
            except Exception:
                bot.logger.exception("FIXED_MEDIA_REJECT_EDIT_FAILED asset=%s", asset_key)
            bot.logger.warning("FIXED_MEDIA_REJECTED asset=%s admin=%s", asset_key, q.from_user.id)
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
        "FIXED_CHANNEL_MEDIA active=on assets=4 slots=10:00/16:00/19:00/21:05_IST "
        "approval_gate=on file_id_reuse=on runtime_renderer=off wrong_image_substitution=off"
    )
    return handler
