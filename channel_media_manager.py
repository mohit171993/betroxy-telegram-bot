"""Locked BETROXY Telegram channel media library.

The four channel creatives are rendered deterministically in BETROXY's approved
black/red/gold sports style. They are previewed privately to the admin once; an
approved Telegram file_id is then reused exactly for every scheduled post.
Unapproved media can never be published: the bot safely falls back to text.
"""
from __future__ import annotations

import hashlib
import io
import json
import threading
import time

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

import bot

ASSETS = {
    "quiz_open": "10:00 AM IST — Daily Prize Pool",
    "quiz_afternoon": "4:00 PM IST — Top 3 Challenge",
    "quiz_last_chance": "7:00 PM IST — Final Call",
    "quiz_result": "9:05 PM IST — Winners / Results",
}

_previous_callback = None
_original_send_text = None
_v110 = None
_schedule = None
_installed = False


def _font(size, bold=False):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
    ]
    for path in paths:
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            pass
    return ImageFont.load_default()


def _fit_text(draw, text, max_width, start_size, bold=True, min_size=24):
    size = start_size
    while size > min_size:
        font = _font(size, bold)
        box = draw.textbbox((0, 0), text, font=font)
        if box[2] - box[0] <= max_width:
            return font
        size -= 2
    return _font(min_size, bold)


def _center(draw, text, y, font, fill, width=900, x0=0, stroke=0, stroke_fill=None):
    box = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
    tw = box[2] - box[0]
    draw.text((x0 + (width - tw) / 2, y), text, font=font, fill=fill,
              stroke_width=stroke, stroke_fill=stroke_fill or fill)


def _gold_trophy(draw):
    # Large stylized trophy, deliberately generic and rights-safe.
    gold = (247, 188, 38)
    dark_gold = (139, 82, 8)
    glow = (255, 219, 83)
    draw.ellipse((325, 405, 575, 615), fill=dark_gold, outline=glow, width=7)
    draw.pieslice((350, 385, 550, 610), 0, 180, fill=gold, outline=glow, width=5)
    draw.rectangle((430, 570, 470, 730), fill=gold)
    draw.rounded_rectangle((350, 710, 550, 760), 18, fill=dark_gold, outline=glow, width=5)
    draw.rounded_rectangle((315, 750, 585, 805), 18, fill=(45, 18, 12), outline=gold, width=5)
    # Handles
    draw.arc((245, 425, 385, 610), 65, 285, fill=gold, width=18)
    draw.arc((515, 425, 655, 610), -105, 115, fill=gold, width=18)
    # Crown / R-like mark without copying a logo asset.
    draw.polygon([(417, 500), (450, 463), (483, 500), (470, 538), (430, 538)], fill=(82, 20, 18))


def _stadium_background():
    w, h = 900, 1125
    base = Image.new("RGB", (w, h), (4, 5, 8))
    # Red glow layer.
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((-220, 230, 520, 1050), fill=(220, 0, 20, 120))
    gd.ellipse((380, 170, 1120, 980), fill=(175, 0, 0, 130))
    glow = glow.filter(ImageFilter.GaussianBlur(80))
    base = Image.alpha_composite(base.convert("RGBA"), glow)
    d = ImageDraw.Draw(base)
    # Stadium light banks.
    for side in (0, 1):
        x0 = 15 if side == 0 else 735
        for r in range(3):
            for c in range(6):
                x = x0 + c * 25
                y = 210 + r * 24 + (c * 5 if side == 0 else -c * 5)
                d.ellipse((x, y, x + 11, y + 11), fill=(255, 245, 205, 245))
    # Crowd/stadium arcs and red energy lines.
    d.arc((-180, 235, 1080, 950), 190, 350, fill=(160, 30, 20, 230), width=8)
    d.arc((-90, 310, 990, 900), 188, 352, fill=(255, 80, 25, 180), width=4)
    for y in (860, 900, 940):
        d.line((45, y, 855, y - 28), fill=(140, 20, 20, 100), width=3)
    return base.convert("RGB")


def _render_poster(asset_key: str) -> bytes:
    img = _stadium_background()
    d = ImageDraw.Draw(img)
    white = (249, 249, 246)
    gold = (246, 190, 44)
    red = (218, 20, 34)
    pale = (255, 226, 122)

    # Brand header.
    _center(d, "BETROXY", 35, _font(72, True), white, stroke=2, stroke_fill=(0, 0, 0))
    d.rounded_rectangle((385, 112, 515, 122), 5, fill=red)

    if asset_key == "quiz_open":
        headline1, headline2 = "DAILY QUIZ.", "DAILY REWARDS."
        strap = "₹1,000 PRIZE POOL • TOP 3 WIN"
        cta = "PLAY TODAY'S QUIZ"
        footer = "7 QUESTIONS  •  30 SECONDS EACH  •  ONE ATTEMPT"
    elif asset_key == "quiz_afternoon":
        headline1, headline2 = "CAN YOU REACH", "TODAY'S TOP 3?"
        strap = "LEADERBOARD IS STILL OPEN"
        cta = "PLAY NOW"
        footer = "ACCURACY + HARD QUESTIONS + SPEED DECIDE RANK"
    elif asset_key == "quiz_last_chance":
        headline1, headline2 = "ONLY 2 HOURS LEFT", "FINAL CALL"
        strap = "₹1,000 PRIZE POOL • TOP 3 WIN"
        cta = "PLAY BEFORE IT CLOSES"
        footer = "LAST REMINDER • CLOSES 9:00 PM IST"
    else:
        headline1, headline2 = "TODAY'S", "WINNERS"
        strap = "DAILY QUIZ RESULTS"
        cta = "TOMORROW • 10:00 AM IST"
        footer = "NEW QUIZ • NEW CHALLENGE • EVERY DAY"

    f1 = _fit_text(d, headline1, 820, 78)
    f2 = _fit_text(d, headline2, 830, 90)
    _center(d, headline1, 145, f1, white, stroke=3, stroke_fill=(10, 10, 10))
    _center(d, headline2, 225, f2, gold, stroke=3, stroke_fill=(70, 35, 0))

    d.rounded_rectangle((115, 330, 785, 390), 22, fill=(115, 5, 10), outline=(255, 65, 30), width=3)
    _center(d, strap, 341, _fit_text(d, strap, 620, 36), pale, width=670, x0=115)

    _gold_trophy(d)

    # Prize podium strip.
    d.rounded_rectangle((100, 820, 800, 900), 22, fill=(12, 10, 10), outline=red, width=3)
    if asset_key == "quiz_result":
        prizes = [("2ND", "₹300"), ("1ST", "₹500"), ("3RD", "₹200")]
    else:
        prizes = [("🥇", "₹500"), ("🥈", "₹300"), ("🥉", "₹200")]
    x_positions = [150, 370, 590]
    for (label, amount), x in zip(prizes, x_positions):
        # Use text-safe rank labels if emoji glyph is unavailable.
        if label.startswith("🥇"): label = "1ST"
        if label.startswith("🥈"): label = "2ND"
        if label.startswith("🥉"): label = "3RD"
        d.text((x, 832), label, font=_font(24, True), fill=(210, 210, 210))
        d.text((x + 55, 825), amount, font=_font(38, True), fill=gold)

    _center(d, footer, 922, _fit_text(d, footer, 820, 27), white)
    d.rounded_rectangle((170, 980, 730, 1050), 32, fill=(167, 8, 20), outline=gold, width=4)
    _center(d, cta, 993, _fit_text(d, cta, 500, 35), white, width=560, x0=170)
    _center(d, "FREE TO PARTICIPATE • NO DEPOSIT OR WAGER REQUIRED", 1070,
            _fit_text(d, "FREE TO PARTICIPATE • NO DEPOSIT OR WAGER REQUIRED", 810, 20), (220, 220, 220))

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=91, optimize=True, progressive=True)
    return out.getvalue()


def _source_bytes(asset_key: str) -> bytes:
    return _render_poster(asset_key)


def _source_sha(asset_key: str) -> str:
    return hashlib.sha256(_source_bytes(asset_key)).hexdigest()


def _ensure_schema():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS channel_media_assets (
                    asset_key TEXT PRIMARY KEY,label TEXT NOT NULL,source_sha TEXT NOT NULL,
                    approved_file_id TEXT,pending_file_id TEXT,preview_message_id BIGINT,
                    status TEXT NOT NULL DEFAULT 'needs_review',approved_by BIGINT,
                    approved_at TIMESTAMPTZ,updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
        conn.commit()


def _prepare_asset_rows():
    _ensure_schema()
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            for asset_key, label in ASSETS.items():
                sha = _source_sha(asset_key)
                cur.execute("SELECT * FROM channel_media_assets WHERE asset_key=%s", (asset_key,))
                row = cur.fetchone()
                if not row:
                    cur.execute("INSERT INTO channel_media_assets(asset_key,label,source_sha,status) VALUES (%s,%s,%s,'needs_review')", (asset_key, label, sha))
                elif str(row.get("source_sha") or "") != sha:
                    cur.execute("""
                        UPDATE channel_media_assets SET label=%s,source_sha=%s,
                            pending_file_id=NULL,preview_message_id=NULL,status='needs_review',
                            approved_by=NULL,approved_at=NULL,updated_at=NOW()
                        WHERE asset_key=%s
                    """, (label, sha, asset_key))
                elif str(row.get("label") or "") != label:
                    cur.execute("UPDATE channel_media_assets SET label=%s,updated_at=NOW() WHERE asset_key=%s", (label, asset_key))
        conn.commit()


def _asset_row(asset_key):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM channel_media_assets WHERE asset_key=%s", (asset_key,))
            return cur.fetchone()


def _approved_file_id(asset_key):
    row = _asset_row(asset_key) or {}
    return str(row.get("approved_file_id") or "").strip() if str(row.get("status") or "") == "approved" else None


def _api(method, *, data=None, files=None, timeout=25):
    r = requests.post(f"{_v110.TG_API}/{method}", data=data, files=files, timeout=timeout)
    try:
        payload = r.json() if r.content else {}
    except Exception:
        payload = {}
    return bool(r.ok and payload.get("ok")), payload


def _preview_markup(asset_key):
    return json.dumps({"inline_keyboard": [[
        {"text": "✅ Approve & Lock", "callback_data": f"channel_media:approve:{asset_key}"},
        {"text": "❌ Reject", "callback_data": f"channel_media:reject:{asset_key}"},
    ]]}, separators=(",", ":"))


def _send_preview(asset_key, force=False):
    row = _asset_row(asset_key) or {}
    if not force and (str(row.get("status") or "") in {"approved", "rejected", "pending"} or row.get("pending_file_id")):
        return False
    photo = _source_bytes(asset_key)
    data = {
        "chat_id": str(bot.ADMIN_ID),
        "caption": (
            "🖼 <b>BETROXY CHANNEL MEDIA PREVIEW</b>\n\n"
            f"Slot: <b>{ASSETS[asset_key]}</b>\n\n"
            "This image is <b>NOT public yet</b>.\n"
            "Approve it once to lock the exact Telegram <code>file_id</code> for automatic reuse."
        ),
        "parse_mode": "HTML",
        "reply_markup": _preview_markup(asset_key),
    }
    ok, payload = _api("sendPhoto", data=data, files={"photo": (f"{asset_key}.jpg", photo, "image/jpeg")})
    if not ok:
        bot.logger.error("CHANNEL_MEDIA_PREVIEW_FAILED asset=%s detail=%s", asset_key, (payload or {}).get("description"))
        return False
    result = payload.get("result") or {}
    photos = result.get("photo") or []
    file_id = str((photos[-1] or {}).get("file_id") or "") if photos else ""
    if not file_id:
        return False
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE channel_media_assets SET pending_file_id=%s,preview_message_id=%s,
                    status='pending',updated_at=NOW() WHERE asset_key=%s
            """, (file_id, result.get("message_id"), asset_key))
        conn.commit()
    bot.logger.warning("CHANNEL_MEDIA_PREVIEW_SENT asset=%s message_id=%s public=off", asset_key, result.get("message_id"))
    return True


def _check_channel_permission():
    try:
        ok, me = _api("getMe", data={})
        if not ok:
            return False
        bot_id = int((me.get("result") or {}).get("id"))
        ok, member = _api("getChatMember", data={"chat_id": str(_v110.CHANNEL_CHAT), "user_id": str(bot_id)})
        if not ok:
            bot.logger.warning("CHANNEL_MEDIA_CHANNEL_CHECK channel=%s admin=unknown can_post=unknown detail=%s", _v110.CHANNEL_CHAT, (member or {}).get("description"))
            return False
        info = member.get("result") or {}
        status = str(info.get("status") or "")
        can_post = status == "creator" or (status == "administrator" and bool(info.get("can_post_messages")))
        can_edit = status == "creator" or (status == "administrator" and bool(info.get("can_edit_messages")))
        bot.logger.warning("CHANNEL_MEDIA_CHANNEL_CHECK channel=%s status=%s can_post=%s can_edit=%s", _v110.CHANNEL_CHAT, status, can_post, can_edit)
        return can_post
    except Exception:
        bot.logger.exception("CHANNEL_MEDIA_CHANNEL_CHECK_FAILED")
        return False


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


def _classify_channel_text(text):
    plain = str(text or "")
    if "BETROXY DAILY CHALLENGE — FINAL RESULTS" in plain: return "quiz_result"
    if "Only 2 Hours Left — Final Call" in plain: return "quiz_last_chance"
    if "Can You Reach Today's Top 3?" in plain: return "quiz_afternoon"
    if "Today's BETROXY Daily Quiz is OPEN" in plain: return "quiz_open"
    return None


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
            bot.logger.warning("CHANNEL_MEDIA_POST asset=%s mode=text_fallback reason=not_approved", asset_key)
            return _original_send_text(chat_id, text, rows)
        data = {"chat_id": str(chat_id), "photo": file_id, "caption": str(text), "parse_mode": "HTML"}
        if rows:
            data["reply_markup"] = json.dumps({"inline_keyboard": rows}, separators=(",", ":"))
        ok, payload = _api("sendPhoto", data=data)
        if ok:
            bot.logger.warning("CHANNEL_MEDIA_POST asset=%s mode=locked_file_id", asset_key)
            return ok, payload
        bot.logger.error("CHANNEL_MEDIA_POST_FAILED asset=%s fallback=text detail=%s", asset_key, (payload or {}).get("description"))
        return _original_send_text(chat_id, text, rows)

    _schedule._send_text = _locked_channel_send
    _schedule._channel_media_installed = True


def _approve(asset_key, admin_id):
    row = _asset_row(asset_key) or {}
    if not str(row.get("pending_file_id") or "").strip():
        return False
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE channel_media_assets SET approved_file_id=pending_file_id,status='approved',
                    approved_by=%s,approved_at=NOW(),updated_at=NOW()
                WHERE asset_key=%s AND pending_file_id IS NOT NULL
            """, (int(admin_id), asset_key))
            changed = cur.rowcount > 0
        conn.commit()
    return changed


def _reject(asset_key):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE channel_media_assets SET status='rejected',pending_file_id=NULL,updated_at=NOW() WHERE asset_key=%s", (asset_key,))
        conn.commit()


def _install_admin_callback():
    global _previous_callback
    _previous_callback = bot.callback_handler

    async def channel_media_callback(update, context):
        q = getattr(update, "callback_query", None)
        data = str(getattr(q, "data", "") or "") if q else ""
        if not q or not data.startswith("channel_media:"):
            return await _previous_callback(update, context)
        if int(q.from_user.id) != int(bot.ADMIN_ID):
            await q.answer("Admin only", show_alert=True); return
        parts = data.split(":", 2)
        if len(parts) != 3 or parts[2] not in ASSETS:
            await q.answer("Invalid media action", show_alert=True); return
        action, asset_key = parts[1], parts[2]
        label = ASSETS[asset_key]
        if action == "approve":
            if not _approve(asset_key, q.from_user.id):
                await q.answer("No pending preview to approve", show_alert=True); return
            await q.answer("Approved & locked ✅")
            try:
                await q.edit_message_caption(
                    caption=f"✅ <b>APPROVED & LOCKED</b>\n\nSlot: <b>{label}</b>\n\nAutomatic posts will reuse this exact Telegram <code>file_id</code>.",
                    parse_mode=bot.ParseMode.HTML, reply_markup=None)
            except Exception:
                bot.logger.exception("CHANNEL_MEDIA_APPROVAL_EDIT_FAILED asset=%s", asset_key)
            bot.logger.warning("CHANNEL_MEDIA_APPROVED asset=%s admin=%s", asset_key, q.from_user.id); return
        if action == "reject":
            _reject(asset_key); await q.answer("Rejected")
            try:
                await q.edit_message_caption(
                    caption=f"❌ <b>REJECTED — NOT PUBLIC</b>\n\nSlot: <b>{label}</b>",
                    parse_mode=bot.ParseMode.HTML,
                    reply_markup=bot.InlineKeyboardMarkup([[bot.InlineKeyboardButton("🔁 Preview Again", callback_data=f"channel_media:retry:{asset_key}")]]))
            except Exception:
                pass
            return
        if action == "retry":
            await q.answer("Sending a new preview…")
            with bot.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE channel_media_assets SET status='needs_review',pending_file_id=NULL,preview_message_id=NULL,updated_at=NOW() WHERE asset_key=%s", (asset_key,))
                conn.commit()
            _send_preview(asset_key, force=True); return
        await q.answer("Unsupported media action", show_alert=True)

    bot.callback_handler = channel_media_callback
    return channel_media_callback


def install(v110, schedule):
    global _v110, _schedule, _installed
    if _installed:
        return bot.callback_handler
    _v110, _schedule = v110, schedule
    _prepare_asset_rows()
    _install_channel_send_wrapper()
    handler = _install_admin_callback()
    _installed = True
    threading.Thread(target=_preview_worker, name="betroxy-channel-media-preview", daemon=True).start()
    bot.logger.warning("CHANNEL_MEDIA_LIBRARY active=on assets=4 source=deterministic_black_red_gold approval_required=on exact_file_id_reuse=on wrong_image_substitution=off text_fallback=on")
    return handler
