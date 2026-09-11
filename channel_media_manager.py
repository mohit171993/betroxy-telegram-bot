"""BETROXY fixed Telegram channel media library.

Production policy:
- runtime creative rendering is OFF
- the admin uploads the four final creatives once in Telegram
- each upload is immediately re-sent by Telegram file_id for verification
- the admin approves & locks each slot
- scheduled channel posts reuse only the approved Telegram file_id
- if a slot is not approved, the post safely falls back to text
"""
from __future__ import annotations

import json
import threading
import time

import requests

import bot

ASSETS = {
    "quiz_open": "10:00 AM IST — Quiz Open",
    "quiz_afternoon": "4:00 PM IST — Top 3 / Afternoon",
    "quiz_last_chance": "7:00 PM IST — 2 Hours Left / Final Call",
    "quiz_result": "9:05 PM IST — Winners / Results",
}
ASSET_ORDER = tuple(ASSETS)
MODE_VERSION = "telegram_admin_upload_v2"

_previous_callback = None
_previous_post_init = None
_original_send_text = None
_v110 = None
_schedule = None
_installed = False


def _ensure_schema():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
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
            """)
            cur.execute("ALTER TABLE channel_media_assets ADD COLUMN IF NOT EXISTS pending_file_unique_id TEXT")
            cur.execute("ALTER TABLE channel_media_assets ADD COLUMN IF NOT EXISTS approved_file_unique_id TEXT")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS channel_media_upload_state (
                    admin_id BIGINT PRIMARY KEY,
                    asset_key TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
        conn.commit()


def _prepare_asset_rows():
    _ensure_schema()
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            for asset_key, label in ASSETS.items():
                cur.execute("SELECT * FROM channel_media_assets WHERE asset_key=%s", (asset_key,))
                row = cur.fetchone()
                if not row:
                    cur.execute(
                        """INSERT INTO channel_media_assets(asset_key,label,source_sha,status)
                           VALUES (%s,%s,%s,'needs_upload')""",
                        (asset_key, label, MODE_VERSION),
                    )
                elif str(row.get("source_sha") or "") != MODE_VERSION:
                    # Any approval from the retired runtime renderer is invalid for
                    # this rollout. Force a real admin-uploaded Telegram image.
                    cur.execute("""
                        UPDATE channel_media_assets
                        SET label=%s,source_sha=%s,approved_file_id=NULL,pending_file_id=NULL,
                            preview_message_id=NULL,status='needs_upload',approved_by=NULL,
                            approved_at=NULL,pending_file_unique_id=NULL,
                            approved_file_unique_id=NULL,updated_at=NOW()
                        WHERE asset_key=%s
                    """, (label, MODE_VERSION, asset_key))
                else:
                    cur.execute(
                        "UPDATE channel_media_assets SET label=%s,updated_at=NOW() WHERE asset_key=%s",
                        (label, asset_key),
                    )
        conn.commit()


def _asset_row(asset_key):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM channel_media_assets WHERE asset_key=%s", (asset_key,))
            return cur.fetchone()


def _approved_file_id(asset_key):
    row = _asset_row(asset_key) or {}
    if str(row.get("status") or "") != "approved":
        return None
    value = str(row.get("approved_file_id") or "").strip()
    return value or None


def _approved_count():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS n FROM channel_media_assets WHERE source_sha=%s AND status='approved' AND approved_file_id IS NOT NULL",
                (MODE_VERSION,),
            )
            row = cur.fetchone() or {}
            return int(row.get("n") or 0)


def _first_missing():
    for key in ASSET_ORDER:
        if not _approved_file_id(key):
            return key
    return None


def _set_upload_state(admin_id, asset_key):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO channel_media_upload_state(admin_id,asset_key,updated_at)
                VALUES (%s,%s,NOW())
                ON CONFLICT(admin_id) DO UPDATE
                SET asset_key=EXCLUDED.asset_key,updated_at=NOW()
            """, (int(admin_id), asset_key))
        conn.commit()


def _get_upload_state(admin_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT asset_key FROM channel_media_upload_state WHERE admin_id=%s", (int(admin_id),))
            row = cur.fetchone()
    key = str((row or {}).get("asset_key") or "")
    return key if key in ASSETS else None


def _clear_upload_state(admin_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM channel_media_upload_state WHERE admin_id=%s", (int(admin_id),))
        conn.commit()


def _api(method: str, *, data=None, files=None, timeout=25):
    r = requests.post(f"{_v110.TG_API}/{method}", data=data, files=files, timeout=timeout)
    try:
        payload = r.json() if r.content else {}
    except Exception:
        payload = {}
    return bool(r.ok and payload.get("ok")), payload


def _approval_markup(asset_key):
    return json.dumps({"inline_keyboard": [[
        {"text": "✅ Approve & Lock", "callback_data": f"channel_media:approve:{asset_key}"},
        {"text": "🔁 Upload Again", "callback_data": f"channel_media:retry:{asset_key}"},
    ]]}, separators=(",", ":"))


def _setup_markup():
    return json.dumps({"inline_keyboard": [[
        {"text": "📤 Start 4-Image Setup", "callback_data": "channel_media:setup:first"}
    ]]}, separators=(",", ":"))


def _prompt_text(asset_key):
    pos = ASSET_ORDER.index(asset_key) + 1
    return (
        f"📤 <b>BETROXY FIXED MEDIA — {pos}/4</b>\n\n"
        f"Now send the image for:\n<b>{ASSETS[asset_key]}</b>\n\n"
        "Send it as a normal Telegram <b>Photo</b> (not as a file/document).\n"
        "I will capture Telegram's file_id, re-send that exact file_id for verification, then you tap <b>Approve & Lock</b>."
    )


def _start_setup(admin_id):
    key = _first_missing()
    if not key:
        _clear_upload_state(admin_id)
        return None
    _set_upload_state(admin_id, key)
    return key


def _save_pending(asset_key, file_id, file_unique_id, message_id=None):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE channel_media_assets
                SET pending_file_id=%s,pending_file_unique_id=%s,preview_message_id=%s,
                    status='pending',updated_at=NOW()
                WHERE asset_key=%s AND source_sha=%s
            """, (file_id, file_unique_id, message_id, asset_key, MODE_VERSION))
        conn.commit()


def _approve(asset_key, admin_id):
    row = _asset_row(asset_key) or {}
    if not str(row.get("pending_file_id") or "").strip():
        return False
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE channel_media_assets
                SET approved_file_id=pending_file_id,
                    approved_file_unique_id=pending_file_unique_id,
                    status='approved',approved_by=%s,approved_at=NOW(),updated_at=NOW()
                WHERE asset_key=%s AND source_sha=%s AND pending_file_id IS NOT NULL
            """, (int(admin_id), asset_key, MODE_VERSION))
            changed = cur.rowcount > 0
        conn.commit()
    return changed


def _reset_for_upload(asset_key):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE channel_media_assets
                SET pending_file_id=NULL,pending_file_unique_id=NULL,preview_message_id=NULL,
                    status='needs_upload',updated_at=NOW()
                WHERE asset_key=%s AND source_sha=%s
            """, (asset_key, MODE_VERSION))
        conn.commit()


def _classify_channel_text(text):
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


def _install_channel_send_wrapper():
    global _original_send_text
    _original_send_text = _schedule._send_text

    def _fixed_channel_send(chat_id, text, rows=None):
        if str(chat_id) != str(_v110.CHANNEL_CHAT):
            return _original_send_text(chat_id, text, rows)
        asset_key = _classify_channel_text(text)
        if not asset_key:
            return _original_send_text(chat_id, text, rows)
        file_id = _approved_file_id(asset_key)
        if not file_id:
            bot.logger.warning(
                "CHANNEL_MEDIA_POST asset=%s mode=text_fallback reason=real_image_not_approved runtime_renderer=off",
                asset_key,
            )
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
            bot.logger.warning("CHANNEL_MEDIA_POST asset=%s mode=approved_telegram_file_id runtime_renderer=off", asset_key)
            return ok, payload
        bot.logger.error(
            "CHANNEL_MEDIA_POST_FAILED asset=%s fallback=text detail=%s",
            asset_key,
            (payload or {}).get("description") if isinstance(payload, dict) else payload,
        )
        return _original_send_text(chat_id, text, rows)

    _schedule._send_text = _fixed_channel_send
    _schedule._channel_media_installed = True


def _check_channel_permission():
    try:
        ok, me = _api("getMe", data={})
        if not ok:
            return False
        bot_id = int((me.get("result") or {}).get("id"))
        ok, member = _api(
            "getChatMember",
            data={"chat_id": str(_v110.CHANNEL_CHAT), "user_id": str(bot_id)},
        )
        if not ok:
            bot.logger.warning(
                "CHANNEL_MEDIA_CHANNEL_CHECK channel=%s admin=unknown can_post=unknown detail=%s",
                _v110.CHANNEL_CHAT,
                (member or {}).get("description") if isinstance(member, dict) else member,
            )
            return False
        info = member.get("result") or {}
        status = str(info.get("status") or "")
        can_post = status == "creator" or (status == "administrator" and bool(info.get("can_post_messages")))
        bot.logger.warning("CHANNEL_MEDIA_CHANNEL_CHECK channel=%s status=%s can_post=%s", _v110.CHANNEL_CHAT, status, can_post)
        return can_post
    except Exception:
        bot.logger.exception("CHANNEL_MEDIA_CHANNEL_CHECK_FAILED")
        return False


def _send_setup_notice():
    time.sleep(7)
    try:
        count = _approved_count()
        if count >= 4:
            bot.logger.warning("CHANNEL_MEDIA_READY approved=4/4 runtime_renderer=off")
            _check_channel_permission()
            return
        missing = [ASSETS[k] for k in ASSET_ORDER if not _approved_file_id(k)]
        data = {
            "chat_id": str(bot.ADMIN_ID),
            "text": (
                "🖼 <b>BETROXY FIXED CHANNEL MEDIA</b>\n\n"
                f"Locked: <b>{count}/4</b>\n"
                "Runtime image generation is <b>OFF</b>.\n\n"
                "Tap below and send the four final creatives once. After approval, Telegram file_ids are reused automatically.\n\n"
                "Pending:\n• " + "\n• ".join(missing)
            ),
            "parse_mode": "HTML",
            "reply_markup": _setup_markup(),
        }
        ok, payload = _api("sendMessage", data=data)
        bot.logger.warning(
            "CHANNEL_MEDIA_SETUP_NOTICE sent=%s approved=%s/4 detail=%s",
            ok,
            count,
            "ok" if ok else (payload or {}).get("description"),
        )
        _check_channel_permission()
    except Exception:
        bot.logger.exception("CHANNEL_MEDIA_SETUP_NOTICE_FAILED")


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
        if len(parts) != 3:
            await q.answer("Invalid media action", show_alert=True)
            return
        action, asset_key = parts[1], parts[2]

        if action == "setup":
            key = _start_setup(q.from_user.id)
            if not key:
                await q.answer("All four images are already locked ✅", show_alert=True)
                return
            await q.answer("Ready for image 1")
            await context.bot.send_message(
                chat_id=q.from_user.id,
                text=_prompt_text(key),
                parse_mode=bot.ParseMode.HTML,
            )
            return

        if asset_key not in ASSETS:
            await q.answer("Invalid media slot", show_alert=True)
            return
        label = ASSETS[asset_key]

        if action == "approve":
            if not _approve(asset_key, q.from_user.id):
                await q.answer("No pending image to approve", show_alert=True)
                return
            await q.answer("Approved & locked ✅")
            try:
                await q.edit_message_caption(
                    caption=(
                        f"✅ <b>APPROVED & LOCKED</b>\n\n"
                        f"Slot: <b>{label}</b>\n\n"
                        "This exact Telegram file_id will be reused automatically."
                    ),
                    parse_mode=bot.ParseMode.HTML,
                    reply_markup=None,
                )
            except Exception:
                bot.logger.exception("CHANNEL_MEDIA_APPROVAL_EDIT_FAILED asset=%s", asset_key)
            bot.logger.warning("CHANNEL_MEDIA_APPROVED asset=%s admin=%s", asset_key, q.from_user.id)

            next_key = _first_missing()
            if next_key:
                _set_upload_state(q.from_user.id, next_key)
                await context.bot.send_message(
                    chat_id=q.from_user.id,
                    text=_prompt_text(next_key),
                    parse_mode=bot.ParseMode.HTML,
                )
            else:
                _clear_upload_state(q.from_user.id)
                await context.bot.send_message(
                    chat_id=q.from_user.id,
                    text=(
                        "✅ <b>ALL 4 BETROXY CHANNEL IMAGES ARE LOCKED</b>\n\n"
                        "10:00 AM IST → Quiz Open\n"
                        "4:00 PM IST → Top 3 / Afternoon\n"
                        "7:00 PM IST → 2 Hours Left / Final Call\n"
                        "9:05 PM IST → Winners / Results\n\n"
                        "Runtime renderer: <b>OFF</b>\n"
                        "Wrong-image substitution: <b>OFF</b>\n"
                        "Scheduled posts now use only these approved Telegram file_ids."
                    ),
                    parse_mode=bot.ParseMode.HTML,
                )
                bot.logger.warning("CHANNEL_MEDIA_ALL_LOCKED approved=4/4 runtime_renderer=off")
            return

        if action == "retry":
            _reset_for_upload(asset_key)
            _set_upload_state(q.from_user.id, asset_key)
            await q.answer("Send the replacement image")
            try:
                await q.edit_message_caption(
                    caption=f"🔁 <b>RE-UPLOAD REQUESTED</b>\n\nSlot: <b>{label}</b>",
                    parse_mode=bot.ParseMode.HTML,
                    reply_markup=None,
                )
            except Exception:
                pass
            await context.bot.send_message(
                chat_id=q.from_user.id,
                text=_prompt_text(asset_key),
                parse_mode=bot.ParseMode.HTML,
            )
            return

        await q.answer("Unsupported media action", show_alert=True)

    bot.callback_handler = channel_media_callback
    return channel_media_callback


def _install_upload_handlers():
    global _previous_post_init
    _previous_post_init = bot.post_init

    async def channel_media_post_init(application):
        if _previous_post_init:
            await _previous_post_init(application)

        async def setup_command(update, context):
            user = getattr(update, "effective_user", None)
            message = getattr(update, "effective_message", None)
            if not user or int(user.id) != int(bot.ADMIN_ID):
                return
            requested = str(context.args[0]).strip() if getattr(context, "args", None) else ""
            aliases = {
                "open": "quiz_open", "10": "quiz_open", "10am": "quiz_open", "quiz_open": "quiz_open",
                "afternoon": "quiz_afternoon", "4": "quiz_afternoon", "4pm": "quiz_afternoon", "quiz_afternoon": "quiz_afternoon",
                "last": "quiz_last_chance", "7": "quiz_last_chance", "7pm": "quiz_last_chance", "quiz_last_chance": "quiz_last_chance",
                "result": "quiz_result", "905": "quiz_result", "quiz_result": "quiz_result",
            }
            key = aliases.get(requested.lower()) if requested else _first_missing()
            if not key:
                _clear_upload_state(user.id)
                await message.reply_text(
                    "✅ All four fixed channel images are already approved and locked.\n\n"
                    "To replace one later, use /setup_channel_media open, afternoon, last, or result."
                )
                return
            if requested:
                _reset_for_upload(key)
            _set_upload_state(user.id, key)
            await message.reply_text(_prompt_text(key), parse_mode=bot.ParseMode.HTML)

        async def photo_upload(update, context):
            user = getattr(update, "effective_user", None)
            message = getattr(update, "effective_message", None)
            if not user or int(user.id) != int(bot.ADMIN_ID) or not message:
                return
            asset_key = _get_upload_state(user.id)
            if not asset_key:
                return
            photos = list(getattr(message, "photo", None) or [])
            if not photos:
                return
            photo = photos[-1]
            file_id = str(photo.file_id)
            file_unique_id = str(photo.file_unique_id)
            _save_pending(asset_key, file_id, file_unique_id, message.message_id)
            _clear_upload_state(user.id)

            # Re-send by file_id. If Telegram displays this successfully, it is
            # the exact identifier production will reuse for the scheduled post.
            try:
                sent = await context.bot.send_photo(
                    chat_id=user.id,
                    photo=file_id,
                    caption=(
                        "🖼 <b>VERIFY & LOCK</b>\n\n"
                        f"Slot: <b>{ASSETS[asset_key]}</b>\n\n"
                        "This copy was re-sent using the captured Telegram file_id.\n"
                        "If it is the correct creative, tap <b>Approve & Lock</b>."
                    ),
                    parse_mode=bot.ParseMode.HTML,
                    reply_markup=bot.InlineKeyboardMarkup([[
                        bot.InlineKeyboardButton("✅ Approve & Lock", callback_data=f"channel_media:approve:{asset_key}"),
                        bot.InlineKeyboardButton("🔁 Upload Again", callback_data=f"channel_media:retry:{asset_key}"),
                    ]]),
                )
                with bot.get_db() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE channel_media_assets SET preview_message_id=%s,updated_at=NOW() WHERE asset_key=%s",
                            (sent.message_id, asset_key),
                        )
                    conn.commit()
                bot.logger.warning(
                    "CHANNEL_MEDIA_UPLOAD_CAPTURED asset=%s unique=%s preview_message_id=%s",
                    asset_key, file_unique_id, sent.message_id,
                )
            except Exception:
                _set_upload_state(user.id, asset_key)
                bot.logger.exception("CHANNEL_MEDIA_FILE_ID_VERIFY_FAILED asset=%s", asset_key)
                await message.reply_text("❌ Telegram could not re-send that photo. Please send it again as a normal Photo.")

        application.add_handler(bot.CommandHandler("setup_channel_media", setup_command), group=-100)
        application.add_handler(bot.MessageHandler(bot.filters.PHOTO, photo_upload), group=-100)
        bot.logger.warning("CHANNEL_MEDIA_UPLOAD_HANDLERS active=on command=/setup_channel_media photo_capture=admin_only")

    bot.post_init = channel_media_post_init


def install(v110, schedule):
    global _v110, _schedule, _installed
    if _installed:
        return bot.callback_handler
    _v110, _schedule = v110, schedule
    _prepare_asset_rows()
    _install_channel_send_wrapper()
    handler = _install_admin_callback()
    _install_upload_handlers()
    _installed = True
    threading.Thread(target=_send_setup_notice, name="betroxy-channel-media-setup", daemon=True).start()
    bot.logger.warning(
        "CHANNEL_MEDIA_LIBRARY active=on assets=4 source=admin_telegram_upload "
        "runtime_renderer=off approval_required=on exact_file_id_reuse=on "
        "wrong_image_substitution=off text_fallback=on"
    )
    return handler
