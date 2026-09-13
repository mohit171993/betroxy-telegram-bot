"""Additive Wednesday/Friday banner manager for BETROXY Sunday Mega Quiz.

Weekly-only feature. It does not modify the locked Daily Quiz implementation,
Daily Quiz banner library, Daily Quiz schedule, private reminder pacing, or the
12/09 Closing branch.

Admin command: /mega_weekday_banners
- Wednesday 19:30 IST — This Sunday teaser
- Friday 19:30 IST — 2 Days To Go

Each image is uploaded and approved explicitly, so there is no upload-order
segregation risk. If a midweek image is not approved, the existing text post is
used as a safe fallback.
"""
from __future__ import annotations

from telegram.ext import ApplicationHandlerStop

import bot

MIDWEEK_MEDIA = {
    "mega_wednesday": "Wednesday 7:30 PM IST — This Sunday Teaser",
    "mega_friday": "Friday 7:30 PM IST — 2 Days To Go",
}
MIDWEEK_ORDER = tuple(MIDWEEK_MEDIA)

_weekly = None
_media = None
_installed = False
_previous_callback = None
_previous_post_init = None


def _ensure_schema():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS mega_quiz_midweek_upload_state (
                    admin_id BIGINT PRIMARY KEY,
                    asset_key TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            for key, label in MIDWEEK_MEDIA.items():
                cur.execute("""
                    INSERT INTO mega_quiz_media_assets(asset_key,label,status)
                    VALUES (%s,%s,'needs_upload')
                    ON CONFLICT(asset_key) DO UPDATE
                    SET label=EXCLUDED.label,updated_at=NOW()
                """, (key, label))
        conn.commit()


def _row(key):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM mega_quiz_media_assets WHERE asset_key=%s", (str(key),))
            return cur.fetchone() or {}


def approved_file_id(key):
    row = _row(key)
    if str(row.get("status") or "") != "approved":
        return None
    value = str(row.get("approved_file_id") or "").strip()
    return value or None


def _set_state(admin_id, key):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO mega_quiz_midweek_upload_state(admin_id,asset_key,updated_at)
                VALUES (%s,%s,NOW())
                ON CONFLICT(admin_id) DO UPDATE
                SET asset_key=EXCLUDED.asset_key,updated_at=NOW()
            """, (int(admin_id), str(key)))
        conn.commit()


def _get_state(admin_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT asset_key FROM mega_quiz_midweek_upload_state WHERE admin_id=%s",
                (int(admin_id),),
            )
            row = cur.fetchone() or {}
    key = str(row.get("asset_key") or "")
    return key if key in MIDWEEK_MEDIA else None


def _clear_state(admin_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM mega_quiz_midweek_upload_state WHERE admin_id=%s", (int(admin_id),))
        conn.commit()


def _save_pending(key, file_id, unique_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE mega_quiz_media_assets
                SET pending_file_id=%s,pending_file_unique_id=%s,
                    status='pending',updated_at=NOW()
                WHERE asset_key=%s
            """, (str(file_id), str(unique_id or "") or None, str(key)))
        conn.commit()


def _approve(key, admin_id):
    row = _row(key)
    if not str(row.get("pending_file_id") or "").strip():
        return False
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE mega_quiz_media_assets
                SET approved_file_id=pending_file_id,
                    approved_file_unique_id=pending_file_unique_id,
                    pending_file_id=NULL,pending_file_unique_id=NULL,
                    status='approved',approved_by=%s,approved_at=NOW(),updated_at=NOW()
                WHERE asset_key=%s AND pending_file_id IS NOT NULL
            """, (int(admin_id), str(key)))
            changed = cur.rowcount > 0
        conn.commit()
    return changed


def _reset_pending(key):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE mega_quiz_media_assets
                SET pending_file_id=NULL,pending_file_unique_id=NULL,
                    status=CASE WHEN approved_file_id IS NULL THEN 'needs_upload' ELSE 'approved' END,
                    updated_at=NOW()
                WHERE asset_key=%s
            """, (str(key),))
        conn.commit()


def _status_text():
    lines = [
        "🗓 <b>MEGA QUIZ — MIDWEEK BANNERS</b>",
        "",
        "Upload these two banners separately so the image can never be assigned to the wrong day.",
        "",
    ]
    for key in MIDWEEK_ORDER:
        row = _row(key)
        if approved_file_id(key):
            status = "✅ Ready"
        elif str(row.get("status") or "") == "pending":
            status = "🟡 Pending approval"
        else:
            status = "⬜ Upload needed"
        lines.append(f"{status} — <b>{MIDWEEK_MEDIA[key]}</b>")
    lines += [
        "",
        "If either banner is missing, that day's existing text-only post remains the fallback.",
        "Daily Quiz and the existing 5 weekly banners are untouched.",
    ]
    return "\n".join(lines)


def _menu():
    rows = []
    for key in MIDWEEK_ORDER:
        ready = "✅" if approved_file_id(key) else "📤"
        rows.append([
            bot.InlineKeyboardButton(
                f"{ready} {MIDWEEK_MEDIA[key]}",
                callback_data=f"mega_midweek:slot:{key}",
            )
        ])
    rows += [
        [bot.InlineKeyboardButton("👁 Preview Uploaded", callback_data="mega_midweek:preview")],
        [bot.InlineKeyboardButton("🔄 Refresh", callback_data="mega_midweek:home")],
    ]
    return bot.InlineKeyboardMarkup(rows)


def _approval_markup(key):
    return bot.InlineKeyboardMarkup([
        [
            bot.InlineKeyboardButton("✅ Approve", callback_data=f"mega_midweek:approve:{key}"),
            bot.InlineKeyboardButton("🔁 Upload Again", callback_data=f"mega_midweek:retry:{key}"),
        ],
        [bot.InlineKeyboardButton("🏠 Midweek Banners", callback_data="mega_midweek:home")],
    ])


def _prompt(key):
    return (
        "📤 <b>UPLOAD MIDWEEK MEGA QUIZ BANNER</b>\n\n"
        f"Target: <b>{MIDWEEK_MEDIA[key]}</b>\n\n"
        "Send the image as a normal Telegram <b>Photo</b>.\n"
        "Use the same 4:5 portrait red/black/gold BETROXY theme as the other channel banners.\n\n"
        "Nothing changes until you approve the preview."
    )


def channel_post(campaign, delivery_type, asset_key, text, rows):
    """Publish midweek image when approved; otherwise preserve text fallback."""
    if _weekly._delivery_exists(campaign["id"], _weekly.CHANNEL, delivery_type):
        return True
    file_id = approved_file_id(asset_key)
    if file_id:
        ok, detail = _media._api_send_photo(_weekly.CHANNEL, file_id, text, rows)
        mode = "approved_midweek_banner"
    else:
        ok, detail = _weekly.quiz._tg_send_text(_weekly.CHANNEL, text, rows)
        mode = "text_fallback"
    if ok:
        _weekly._mark_delivery(campaign["id"], _weekly.CHANNEL, delivery_type)
    bot.logger.warning(
        "MEGA_MIDWEEK_CHANNEL_POST type=%s asset=%s mode=%s sent=%s campaign=%s detail=%s",
        delivery_type, asset_key, mode, ok, campaign["id"], "ok" if ok else detail,
    )
    return bool(ok)


def _install_callback():
    global _previous_callback
    _previous_callback = bot.callback_handler

    async def callback(update, context):
        q = getattr(update, "callback_query", None)
        data = str(getattr(q, "data", "") or "") if q else ""
        if not q or not data.startswith("mega_midweek:"):
            return await _previous_callback(update, context)
        if not bot.is_admin(q.from_user.id):
            await q.answer("Admin only", show_alert=True)
            return

        parts = data.split(":")
        action = parts[1] if len(parts) > 1 else ""
        key = parts[2] if len(parts) > 2 else ""

        if action == "home":
            await q.answer()
            try:
                await q.edit_message_text(_status_text(), parse_mode=bot.ParseMode.HTML, reply_markup=_menu())
            except Exception:
                await q.message.reply_text(_status_text(), parse_mode=bot.ParseMode.HTML, reply_markup=_menu())
            return

        if action == "slot" and key in MIDWEEK_MEDIA:
            _set_state(q.from_user.id, key)
            await q.answer("Send this banner as a Photo")
            await context.bot.send_message(chat_id=q.from_user.id, text=_prompt(key), parse_mode=bot.ParseMode.HTML)
            return

        if action == "retry" and key in MIDWEEK_MEDIA:
            _reset_pending(key)
            _set_state(q.from_user.id, key)
            await q.answer("Send replacement Photo")
            await context.bot.send_message(chat_id=q.from_user.id, text=_prompt(key), parse_mode=bot.ParseMode.HTML)
            return

        if action == "approve" and key in MIDWEEK_MEDIA:
            ok = _approve(key, q.from_user.id)
            await q.answer("Approved ✅" if ok else "No pending upload", show_alert=not ok)
            if ok:
                await context.bot.send_message(
                    chat_id=q.from_user.id,
                    text=f"✅ <b>{MIDWEEK_MEDIA[key]}</b> approved.",
                    parse_mode=bot.ParseMode.HTML,
                    reply_markup=_menu(),
                )
                bot.logger.warning("MEGA_MIDWEEK_BANNER_APPROVED asset=%s admin=%s", key, q.from_user.id)
            return

        if action == "preview":
            await q.answer("Sending previews")
            for slot in MIDWEEK_ORDER:
                file_id = approved_file_id(slot)
                if file_id:
                    await context.bot.send_photo(
                        chat_id=q.from_user.id,
                        photo=file_id,
                        caption=f"👁 <b>{MIDWEEK_MEDIA[slot]}</b>",
                        parse_mode=bot.ParseMode.HTML,
                    )
            return

        await q.answer("Unsupported action", show_alert=True)

    bot.callback_handler = callback


def _install_handlers():
    global _previous_post_init
    _previous_post_init = bot.post_init

    async def post_init(application):
        if _previous_post_init:
            await _previous_post_init(application)

        async def command(update, context):
            user = getattr(update, "effective_user", None)
            msg = getattr(update, "effective_message", None)
            if not user or not msg or not bot.is_admin(user.id):
                return
            await msg.reply_text(_status_text(), parse_mode=bot.ParseMode.HTML, reply_markup=_menu())

        async def photo_upload(update, context):
            user = getattr(update, "effective_user", None)
            msg = getattr(update, "effective_message", None)
            if not user or not msg or not bot.is_admin(user.id):
                return
            key = _get_state(user.id)
            if not key:
                return
            photos = list(getattr(msg, "photo", None) or [])
            if not photos:
                return
            photo = photos[-1]
            _save_pending(key, photo.file_id, photo.file_unique_id)
            _clear_state(user.id)
            await context.bot.send_photo(
                chat_id=user.id,
                photo=photo.file_id,
                caption=(
                    "👁 <b>MIDWEEK BANNER PREVIEW</b>\n\n"
                    f"Target: <b>{MIDWEEK_MEDIA[key]}</b>\n\n"
                    "Verify the words on the image match this target before approving."
                ),
                parse_mode=bot.ParseMode.HTML,
                reply_markup=_approval_markup(key),
            )
            bot.logger.warning("MEGA_MIDWEEK_UPLOAD_CAPTURED asset=%s admin=%s", key, user.id)
            raise ApplicationHandlerStop

        application.add_handler(bot.CommandHandler("mega_weekday_banners", command), group=-92)
        application.add_handler(bot.MessageHandler(bot.filters.PHOTO, photo_upload), group=-92)
        bot.logger.warning(
            "MEGA_MIDWEEK_BANNER_HANDLERS active=on command=/mega_weekday_banners slots=2 explicit_assignment=on"
        )

    bot.post_init = post_init


def install(weekly_module, media_module):
    global _weekly, _media, _installed
    _weekly = weekly_module
    _media = media_module
    if _installed:
        return
    _ensure_schema()
    _install_callback()
    _install_handlers()
    _installed = True
    bot.logger.warning(
        "MEGA_MIDWEEK_BANNERS active=on wed=19:30 fri=19:30 slots=2 text_fallback=on "
        "explicit_manual_assignment=on daily_quiz_untouched=on existing_5_weekly_untouched=on"
    )
