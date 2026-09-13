"""Bulk-upload all five Sunday Mega Quiz channel banners in one Telegram batch.

This is additive weekly-only functionality. It does not touch the locked Daily Quiz
banner library, timing, rotation or code paths.

Admin flow:
- open /mega_banners and tap "Bulk Upload All 5 Weekly Banners", or /mega_bulk_banners
- send exactly 5 Telegram Photos together (album) or consecutively
- the bot sorts them by Telegram message order and assigns:
  1 Saturday preview
  2 Sunday open
  3 Sunday afternoon
  4 Sunday last chance
  5 Sunday results
- review all five assignments
- nothing changes live until "Approve All 5"
- approval replaces the five weekly file_ids in one DB transaction
"""
from __future__ import annotations

from telegram.ext import ApplicationHandlerStop

import bot

_installed = False
_additions = None
_previous_callback = None
_previous_post_init = None

BULK_COUNT = 5


def _ensure_schema():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS mega_quiz_bulk_sessions (
                    admin_id BIGINT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'active',
                    status_message_id BIGINT,
                    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS mega_quiz_bulk_items (
                    id BIGSERIAL PRIMARY KEY,
                    admin_id BIGINT NOT NULL,
                    message_id BIGINT NOT NULL,
                    file_id TEXT NOT NULL,
                    file_unique_id TEXT,
                    media_group_id TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(admin_id, message_id),
                    UNIQUE(admin_id, file_unique_id)
                )
            """)
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_mega_quiz_bulk_items_admin "
                "ON mega_quiz_bulk_items(admin_id,message_id)"
            )
        conn.commit()


def _session(admin_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM mega_quiz_bulk_sessions WHERE admin_id=%s", (int(admin_id),))
            return cur.fetchone() or {}


def _items(admin_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT * FROM mega_quiz_bulk_items
                WHERE admin_id=%s
                ORDER BY message_id,id
            """, (int(admin_id),))
            return cur.fetchall() or []


def _set_session_status(admin_id, status):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE mega_quiz_bulk_sessions
                SET status=%s,updated_at=NOW()
                WHERE admin_id=%s
            """, (str(status), int(admin_id)))
        conn.commit()


def _set_status_message(admin_id, message_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE mega_quiz_bulk_sessions
                SET status_message_id=%s,updated_at=NOW()
                WHERE admin_id=%s
            """, (int(message_id), int(admin_id)))
        conn.commit()


def _start_session(admin_id):
    # Prevent the guided one-at-a-time weekly uploader from consuming the same images.
    try:
        _additions._clear_upload_state(admin_id)
    except Exception:
        pass
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM mega_quiz_bulk_items WHERE admin_id=%s", (int(admin_id),))
            cur.execute("""
                INSERT INTO mega_quiz_bulk_sessions(admin_id,status,status_message_id,started_at,updated_at)
                VALUES (%s,'active',NULL,NOW(),NOW())
                ON CONFLICT(admin_id) DO UPDATE SET
                    status='active',status_message_id=NULL,started_at=NOW(),updated_at=NOW()
            """, (int(admin_id),))
        conn.commit()


def _insert_item(admin_id, message_id, file_id, file_unique_id, media_group_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO mega_quiz_bulk_items(
                    admin_id,message_id,file_id,file_unique_id,media_group_id
                ) VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING
            """, (
                int(admin_id), int(message_id), str(file_id),
                str(file_unique_id or "") or None,
                str(media_group_id or "") or None,
            ))
            changed = cur.rowcount > 0
        conn.commit()
    return changed


def _prompt_text():
    return (
        "📦 <b>SUNDAY MEGA QUIZ — BULK 5-BANNER UPLOAD</b>\n\n"
        "Select and send <b>all 5 weekly banners together</b> as Telegram Photos. "
        "You can also send them consecutively.\n\n"
        "I will segregate them automatically by upload order:\n"
        "1️⃣ <b>Saturday Preview</b>\n"
        "2️⃣ <b>Sunday Quiz Open</b>\n"
        "3️⃣ <b>Sunday Afternoon / Still Open</b>\n"
        "4️⃣ <b>Sunday Last Chance</b>\n"
        "5️⃣ <b>Sunday Final Results</b>\n\n"
        "Recommended: the same <b>4:5 portrait</b> format/theme as your Daily Quiz banners.\n\n"
        "Your existing weekly images remain live while you upload. "
        "<b>Nothing changes until you review and tap Approve All 5.</b>\n\n"
        "Daily Quiz banners are completely untouched."
    )


def _progress_text(count):
    labels = list(_additions.MEGA_MEDIA.values())
    lines = [
        "📥 <b>WEEKLY MEGA BULK UPLOAD</b>",
        "",
        f"Received: <b>{count}/{BULK_COUNT}</b>",
        "",
    ]
    for idx, label in enumerate(labels, 1):
        mark = "✅" if count >= idx else "⬜"
        lines.append(f"{mark} {idx}. {label}")
    if count < BULK_COUNT:
        lines += ["", f"Waiting for banner <b>{count + 1}</b>."]
    else:
        lines += ["", "All 5 received — preparing your review."]
    return "\n".join(lines)


def _cancel_markup():
    return bot.InlineKeyboardMarkup([
        [bot.InlineKeyboardButton("❌ Cancel Weekly Bulk Upload", callback_data="mega_bulk:cancel")]
    ])


async def _update_progress(context, admin_id, count):
    session = _session(admin_id)
    message_id = session.get("status_message_id")
    if message_id:
        try:
            await context.bot.edit_message_text(
                chat_id=int(admin_id),
                message_id=int(message_id),
                text=_progress_text(count),
                parse_mode=bot.ParseMode.HTML,
                reply_markup=_cancel_markup(),
            )
            return
        except Exception:
            bot.logger.exception("MEGA_BULK_PROGRESS_EDIT_FAILED admin=%s", admin_id)
    sent = await context.bot.send_message(
        chat_id=int(admin_id),
        text=_progress_text(count),
        parse_mode=bot.ParseMode.HTML,
        reply_markup=_cancel_markup(),
    )
    _set_status_message(admin_id, sent.message_id)


async def _show_review(context, admin_id):
    rows = _items(admin_id)
    if len(rows) != BULK_COUNT:
        return False, f"Exactly {BULK_COUNT} banners are required. Received {len(rows)}."

    _set_session_status(admin_id, "review")
    try:
        _additions._clear_upload_state(admin_id)
    except Exception:
        pass

    await context.bot.send_message(
        chat_id=int(admin_id),
        text=(
            "👁 <b>SUNDAY MEGA QUIZ — BULK REVIEW</b>\n\n"
            "I segregated the 5 banners by their Telegram upload order. "
            "Review each assignment below.\n\n"
            "<b>No weekly banner has been changed yet.</b>"
        ),
        parse_mode=bot.ParseMode.HTML,
    )

    for idx, (key, row) in enumerate(zip(_additions.MEGA_MEDIA_ORDER, rows), 1):
        try:
            await context.bot.send_photo(
                chat_id=int(admin_id),
                photo=str(row.get("file_id")),
                caption=f"#{idx} → <b>{_additions.MEGA_MEDIA[key]}</b>",
                parse_mode=bot.ParseMode.HTML,
            )
        except Exception:
            bot.logger.exception("MEGA_BULK_REVIEW_PHOTO_FAILED admin=%s slot=%s", admin_id, key)

    await context.bot.send_message(
        chat_id=int(admin_id),
        text=(
            "✅ <b>5 WEEKLY BANNERS ASSIGNED</b>\n\n"
            "1. Saturday Preview\n"
            "2. Sunday Open\n"
            "3. Sunday Afternoon\n"
            "4. Sunday Last Chance\n"
            "5. Sunday Results\n\n"
            "Tap <b>Approve All 5</b> only if the order above is correct."
        ),
        parse_mode=bot.ParseMode.HTML,
        reply_markup=bot.InlineKeyboardMarkup([
            [bot.InlineKeyboardButton("✅ Approve All 5", callback_data="mega_bulk:approve")],
            [bot.InlineKeyboardButton("❌ Cancel All", callback_data="mega_bulk:cancel")],
        ]),
    )
    bot.logger.warning("MEGA_BULK_REVIEW_READY admin=%s count=5", admin_id)
    return True, "Review ready"


def _approve_all(admin_id):
    session = _session(admin_id)
    if str(session.get("status") or "") != "review":
        return False, "Review the 5 banners first."
    rows = _items(admin_id)
    if len(rows) != BULK_COUNT:
        return False, "Exactly 5 banners are required."

    # One transaction means the five weekly assets switch together. Daily tables are
    # not referenced here at all.
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            for key, row in zip(_additions.MEGA_MEDIA_ORDER, rows):
                cur.execute("""
                    UPDATE mega_quiz_media_assets
                    SET approved_file_id=%s,
                        approved_file_unique_id=%s,
                        pending_file_id=NULL,
                        pending_file_unique_id=NULL,
                        status='approved',
                        approved_by=%s,
                        approved_at=NOW(),
                        updated_at=NOW()
                    WHERE asset_key=%s
                """, (
                    str(row.get("file_id")),
                    str(row.get("file_unique_id") or "") or None,
                    int(admin_id),
                    str(key),
                ))
                if cur.rowcount != 1:
                    raise RuntimeError(f"Weekly media slot missing: {key}")
            cur.execute("""
                UPDATE mega_quiz_bulk_sessions
                SET status='approved',updated_at=NOW()
                WHERE admin_id=%s
            """, (int(admin_id),))
        conn.commit()
    return True, "Approved"


def _cancel(admin_id):
    _set_session_status(admin_id, "cancelled")
    try:
        _additions._clear_upload_state(admin_id)
    except Exception:
        pass


def _patch_weekly_menu():
    old_menu = _additions._media_menu
    if getattr(old_menu, "_weekly_bulk_patched", False):
        return

    def menu_with_bulk():
        markup = old_menu()
        rows = [list(r) for r in (getattr(markup, "inline_keyboard", None) or [])]
        rows.insert(0, [
            bot.InlineKeyboardButton(
                "📦 Bulk Upload All 5 Weekly Banners",
                callback_data="mega_bulk:start",
            )
        ])
        return bot.InlineKeyboardMarkup(rows)

    menu_with_bulk._weekly_bulk_patched = True
    _additions._media_menu = menu_with_bulk


def _install_callback():
    global _previous_callback
    _previous_callback = bot.callback_handler

    async def mega_bulk_callback(update, context):
        q = getattr(update, "callback_query", None)
        data = str(getattr(q, "data", "") or "") if q else ""
        if not q or not data.startswith("mega_bulk:"):
            return await _previous_callback(update, context)
        if not bot.is_admin(q.from_user.id):
            await q.answer("Admin only", show_alert=True)
            return

        admin_id = int(q.from_user.id)
        action = data.split(":", 1)[1] if ":" in data else ""

        if action == "start":
            _start_session(admin_id)
            await q.answer("Send all 5 weekly banners")
            await context.bot.send_message(
                chat_id=admin_id,
                text=_prompt_text(),
                parse_mode=bot.ParseMode.HTML,
                reply_markup=_cancel_markup(),
            )
            bot.logger.warning("MEGA_BULK_START admin=%s expected=5", admin_id)
            return

        if action == "approve":
            ok, detail = _approve_all(admin_id)
            await q.answer("All 5 weekly banners approved ✅" if ok else detail, show_alert=not ok)
            if ok:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=(
                        "✅ <b>ALL 5 WEEKLY MEGA QUIZ BANNERS ARE LIVE</b>\n\n"
                        "They are now assigned to the approved weekly Mega Quiz schedule.\n"
                        "Daily Quiz banners and locked Daily Quiz code were not changed."
                    ),
                    parse_mode=bot.ParseMode.HTML,
                    reply_markup=_additions._media_menu(),
                )
                bot.logger.warning("MEGA_BULK_APPROVED admin=%s count=5 daily_untouched=on", admin_id)
            return

        if action == "cancel":
            _cancel(admin_id)
            await q.answer("Weekly bulk upload cancelled")
            await context.bot.send_message(
                chat_id=admin_id,
                text=(
                    "❌ Weekly bulk upload cancelled.\n\n"
                    "No weekly banner was replaced. Daily Quiz banners were untouched."
                ),
                reply_markup=_additions._media_menu(),
            )
            bot.logger.warning("MEGA_BULK_CANCELLED admin=%s", admin_id)
            return

        await q.answer("Unsupported weekly bulk action", show_alert=True)

    bot.callback_handler = mega_bulk_callback


def _install_handlers():
    global _previous_post_init
    _previous_post_init = bot.post_init

    async def mega_bulk_post_init(application):
        if _previous_post_init:
            await _previous_post_init(application)

        async def mega_bulk_command(update, context):
            user = getattr(update, "effective_user", None)
            message = getattr(update, "effective_message", None)
            if not user or not message or not bot.is_admin(user.id):
                return
            _start_session(user.id)
            await message.reply_text(
                _prompt_text(),
                parse_mode=bot.ParseMode.HTML,
                reply_markup=_cancel_markup(),
            )

        async def mega_bulk_photo(update, context):
            user = getattr(update, "effective_user", None)
            message = getattr(update, "effective_message", None)
            if not user or not message or not bot.is_admin(user.id):
                return
            session = _session(user.id)
            if str(session.get("status") or "") != "active":
                return
            photos = list(getattr(message, "photo", None) or [])
            if not photos:
                return

            current = _items(user.id)
            if len(current) >= BULK_COUNT:
                raise ApplicationHandlerStop

            photo = photos[-1]
            inserted = _insert_item(
                user.id,
                message.message_id,
                photo.file_id,
                getattr(photo, "file_unique_id", None),
                getattr(message, "media_group_id", None),
            )
            if not inserted:
                raise ApplicationHandlerStop

            count = len(_items(user.id))
            bot.logger.warning(
                "MEGA_BULK_CAPTURED admin=%s count=%s message_id=%s media_group=%s",
                user.id, count, message.message_id, getattr(message, "media_group_id", None),
            )
            await _update_progress(context, user.id, count)

            if count == BULK_COUNT:
                await _show_review(context, user.id)

            # Weekly bulk owns this photo while active; no other banner handler may consume it.
            raise ApplicationHandlerStop

        application.add_handler(bot.CommandHandler("mega_bulk_banners", mega_bulk_command), group=-95)
        application.add_handler(bot.MessageHandler(bot.filters.PHOTO, mega_bulk_photo), group=-95)
        bot.logger.warning(
            "MEGA_BULK_HANDLERS active=on command=/mega_bulk_banners photos=5 "
            "album_or_consecutive=on auto_segregate=upload_order approve_all=on admin_only=on"
        )

    bot.post_init = mega_bulk_post_init


def install(additions_module):
    global _installed, _additions
    _additions = additions_module
    if _installed:
        return
    _ensure_schema()
    _patch_weekly_menu()
    _install_callback()
    _install_handlers()
    _installed = True
    bot.logger.warning(
        "MEGA_BULK_UPLOAD active=on weekly_only=on count=5 auto_segregate=upload_order "
        "approve_all=on existing_weekly_live_until_approval=on daily_quiz_untouched=on"
    )
