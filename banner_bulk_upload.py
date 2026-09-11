"""Bulk upload for BETROXY quiz-channel banner rotation.

Phase 1 target:
- first 2 photos -> 10:00 AM Quiz Open
- next 2 photos -> 4:00 PM Afternoon
- next 2 photos -> 7:00 PM Last Chance
- final 1-2 photos -> 9:05 PM Results

The admin can send all photos as one Telegram album or as consecutive Photos.
Nothing becomes active until a single Approve All action. Existing locked
channel creatives remain the permanent fallbacks and production rotation stays
round-robin by IST day.
"""
from __future__ import annotations

from telegram.ext import ApplicationHandlerStop

import bot
import banner_manager as bm

_installed = False
_previous_callback = None
_previous_post_init = None

BULK_MIN = 7
BULK_MAX = 8


def _slot_for_sequence(seq_no: int) -> str:
    if seq_no <= 2:
        return "quiz_open"
    if seq_no <= 4:
        return "quiz_afternoon"
    if seq_no <= 6:
        return "quiz_last_chance"
    return "quiz_result"


def _ensure_schema():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS betroxy_banner_bulk_sessions (
                    admin_id BIGINT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'active',
                    status_message_id BIGINT,
                    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS betroxy_banner_bulk_items (
                    id BIGSERIAL PRIMARY KEY,
                    admin_id BIGINT NOT NULL,
                    seq_no INTEGER NOT NULL,
                    slot_key TEXT NOT NULL,
                    banner_id BIGINT NOT NULL,
                    file_unique_id TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(admin_id, seq_no)
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_betroxy_banner_bulk_admin ON betroxy_banner_bulk_items(admin_id,seq_no)")
        conn.commit()


def _session(admin_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM betroxy_banner_bulk_sessions WHERE admin_id=%s", (int(admin_id),))
            return cur.fetchone()


def _items(admin_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT i.*, b.file_id, b.status AS banner_status
                FROM betroxy_banner_bulk_items i
                JOIN betroxy_banner_library b ON b.id=i.banner_id
                WHERE i.admin_id=%s
                ORDER BY i.seq_no
            """, (int(admin_id),))
            return cur.fetchall() or []


def _set_session_status(admin_id, status):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE betroxy_banner_bulk_sessions
                SET status=%s,updated_at=NOW()
                WHERE admin_id=%s
            """, (str(status), int(admin_id)))
        conn.commit()


def _set_status_message(admin_id, message_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE betroxy_banner_bulk_sessions
                SET status_message_id=%s,updated_at=NOW()
                WHERE admin_id=%s
            """, (int(message_id), int(admin_id)))
        conn.commit()


def _cancel_existing_pending(admin_id):
    for row in _items(admin_id):
        if str(row.get("banner_status") or "") == "pending":
            try:
                bm._cancel_pending(int(row["banner_id"]))
            except Exception:
                bot.logger.exception("BANNER_BULK_CANCEL_PENDING_FAILED banner_id=%s", row.get("banner_id"))


def _start_session(admin_id):
    _cancel_existing_pending(admin_id)
    try:
        bm._clear_upload_state(admin_id)
    except Exception:
        pass
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM betroxy_banner_bulk_items WHERE admin_id=%s", (int(admin_id),))
            cur.execute("""
                INSERT INTO betroxy_banner_bulk_sessions(admin_id,status,status_message_id,started_at,updated_at)
                VALUES (%s,'active',NULL,NOW(),NOW())
                ON CONFLICT(admin_id) DO UPDATE SET
                    status='active',status_message_id=NULL,started_at=NOW(),updated_at=NOW()
            """, (int(admin_id),))
        conn.commit()


def _insert_item(admin_id, seq_no, slot_key, banner_id, file_unique_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO betroxy_banner_bulk_items(admin_id,seq_no,slot_key,banner_id,file_unique_id)
                VALUES (%s,%s,%s,%s,%s)
            """, (int(admin_id), int(seq_no), str(slot_key), int(banner_id), str(file_unique_id or "") or None))
        conn.commit()


def _duplicate_in_session(admin_id, file_unique_id):
    if not file_unique_id:
        return False
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 1 FROM betroxy_banner_bulk_items
                WHERE admin_id=%s AND file_unique_id=%s LIMIT 1
            """, (int(admin_id), str(file_unique_id)))
            return bool(cur.fetchone())


def _bulk_prompt():
    return (
        "📦 <b>PHASE 1 — BULK CHANNEL BANNER UPLOAD</b>\n\n"
        "Send <b>7 or 8 banners</b> now. You can select all of them in one Telegram album.\n\n"
        "The bot will segregate them automatically in this order:\n"
        "1–2 → <b>10 AM — Quiz Open</b>\n"
        "3–4 → <b>4 PM — Afternoon</b>\n"
        "5–6 → <b>7 PM — Last Chance</b>\n"
        "7–8 → <b>9:05 PM — Results/Winners</b>\n\n"
        "Channel format: <b>4:5 portrait</b> (about 1122×1402).\n"
        "Existing locked banners stay as fallbacks.\n"
        "Nothing becomes live until <b>Approve All</b>.\n\n"
        "If you upload 8 images, review opens automatically after image 8.\n"
        "If you upload 7, tap <b>Finish Upload</b> after the seventh image."
    )


def _progress_text(count):
    open_n = min(count, 2)
    aft_n = min(max(count - 2, 0), 2)
    last_n = min(max(count - 4, 0), 2)
    result_n = min(max(count - 6, 0), 2)
    next_text = "Upload complete — preparing review." if count >= BULK_MAX else f"Waiting for banner {count + 1}."
    if count == 7:
        next_text = "You can Finish now with 1 Results banner, or add one more Results banner."
    return (
        "📥 <b>BULK BANNER UPLOAD</b>\n\n"
        f"Received: <b>{count}/{BULK_MAX}</b>\n\n"
        f"🕙 10 AM: <b>{open_n}/2</b>\n"
        f"🕓 4 PM: <b>{aft_n}/2</b>\n"
        f"🕖 7 PM: <b>{last_n}/2</b>\n"
        f"🏆 9:05 PM: <b>{result_n}/2</b>\n\n"
        f"{next_text}"
    )


def _progress_markup(count):
    rows = []
    if count >= BULK_MIN:
        rows.append([bot.InlineKeyboardButton(f"✅ Finish Upload ({count} banners)", callback_data="bm:bulk:finish")])
    rows.append([bot.InlineKeyboardButton("❌ Cancel Bulk Upload", callback_data="bm:bulk:cancel")])
    return bot.InlineKeyboardMarkup(rows)


async def _update_progress(context, admin_id, count):
    session = _session(admin_id) or {}
    message_id = session.get("status_message_id")
    if message_id:
        try:
            await context.bot.edit_message_text(
                chat_id=int(admin_id),
                message_id=int(message_id),
                text=_progress_text(count),
                parse_mode=bot.ParseMode.HTML,
                reply_markup=_progress_markup(count),
            )
            return
        except Exception:
            bot.logger.exception("BANNER_BULK_PROGRESS_EDIT_FAILED admin=%s", admin_id)
    sent = await context.bot.send_message(
        chat_id=int(admin_id),
        text=_progress_text(count),
        parse_mode=bot.ParseMode.HTML,
        reply_markup=_progress_markup(count),
    )
    _set_status_message(admin_id, sent.message_id)


async def _finish_review(context, admin_id):
    session = _session(admin_id) or {}
    if str(session.get("status") or "") not in {"active", "review"}:
        return False, "No active bulk upload session."
    rows = _items(admin_id)
    count = len(rows)
    if count < BULK_MIN:
        return False, f"Upload at least {BULK_MIN} banners first. You currently have {count}."
    if count > BULK_MAX:
        return False, "This Phase 1 batch supports at most 8 banners."

    _set_session_status(admin_id, "review")
    try:
        bm._clear_upload_state(admin_id)
    except Exception:
        pass

    await context.bot.send_message(
        chat_id=int(admin_id),
        text=(
            "👁 <b>BULK REVIEW</b>\n\n"
            "I segregated the banners by upload order. Review the previews below.\n"
            "Nothing is active yet."
        ),
        parse_mode=bot.ParseMode.HTML,
    )
    for row in rows:
        try:
            await context.bot.send_photo(
                chat_id=int(admin_id),
                photo=str(row.get("file_id")),
                caption=(
                    f"#{row.get('seq_no')} → <b>{bm._slot_label(str(row.get('slot_key')))}</b>\n"
                    f"Pending Banner #{row.get('banner_id')}"
                ),
                parse_mode=bot.ParseMode.HTML,
            )
        except Exception:
            bot.logger.exception("BANNER_BULK_REVIEW_PREVIEW_FAILED banner_id=%s", row.get("banner_id"))

    result_count = sum(1 for r in rows if str(r.get("slot_key")) == "quiz_result")
    await context.bot.send_message(
        chat_id=int(admin_id),
        text=(
            "✅ <b>PHASE 1 ASSIGNMENT READY</b>\n\n"
            "10 AM: <b>2 new banners</b>\n"
            "4 PM: <b>2 new banners</b>\n"
            "7 PM: <b>2 new banners</b>\n"
            f"9:05 PM: <b>{result_count} new banner{'s' if result_count != 1 else ''}</b>\n\n"
            "After approval, the existing locked banner plus these new banners will rotate "
            "<b>round-robin by IST day</b>."
        ),
        parse_mode=bot.ParseMode.HTML,
        reply_markup=bot.InlineKeyboardMarkup([
            [bot.InlineKeyboardButton("✅ Approve All & Add to Rotation", callback_data="bm:bulk:approve")],
            [bot.InlineKeyboardButton("❌ Cancel All", callback_data="bm:bulk:cancel")],
        ]),
    )
    bot.logger.warning("BANNER_BULK_REVIEW_READY admin=%s count=%s result_count=%s", admin_id, count, result_count)
    return True, "Review ready"


def _cancel_bulk(admin_id):
    _cancel_existing_pending(admin_id)
    _set_session_status(admin_id, "cancelled")
    try:
        bm._clear_upload_state(admin_id)
    except Exception:
        pass


def _approve_bulk(admin_id):
    session = _session(admin_id) or {}
    if str(session.get("status") or "") != "review":
        return False, "Finish the upload and review the banners first.", []
    rows = _items(admin_id)
    if len(rows) < BULK_MIN:
        return False, "Not enough banners in the batch.", []
    approved = []
    failures = []
    for row in rows:
        banner_id = int(row["banner_id"])
        current = bm._banner_row(banner_id) or {}
        if str(current.get("status") or "") == "approved":
            approved.append(banner_id)
            continue
        ok, detail = bm._approve_banner(banner_id, admin_id)
        if ok:
            approved.append(banner_id)
        else:
            failures.append((banner_id, detail))
    if failures:
        return False, "Some banners could not be approved: " + ", ".join(f"#{bid} {detail}" for bid, detail in failures), approved
    _set_session_status(admin_id, "approved")
    return True, "Approved", approved


def _patch_menus_and_prompts():
    old_channel_markup = bm._channel_markup
    if not getattr(old_channel_markup, "_bulk_upload_patched", False):
        def channel_markup_with_bulk():
            markup = old_channel_markup()
            rows = [list(r) for r in (getattr(markup, "inline_keyboard", None) or [])]
            rows.insert(0, [bot.InlineKeyboardButton("📦 Bulk Upload Phase 1 (7–8)", callback_data="bm:bulk:start")])
            return bot.InlineKeyboardMarkup(rows)
        channel_markup_with_bulk._bulk_upload_patched = True
        bm._channel_markup = channel_markup_with_bulk

    old_main_markup = bm._main_markup
    if not getattr(old_main_markup, "_bulk_upload_patched", False):
        def main_markup_with_bulk():
            markup = old_main_markup()
            rows = [list(r) for r in (getattr(markup, "inline_keyboard", None) or [])]
            insert_at = 2 if len(rows) >= 2 else len(rows)
            rows.insert(insert_at, [bot.InlineKeyboardButton("📦 Bulk Upload Channel Banners", callback_data="bm:bulk:start")])
            return bot.InlineKeyboardMarkup(rows)
        main_markup_with_bulk._bulk_upload_patched = True
        bm._main_markup = main_markup_with_bulk

    old_upload_prompt = bm._upload_prompt
    if not getattr(old_upload_prompt, "_channel_ratio_patched", False):
        def upload_prompt_with_correct_ratio(slot):
            if slot in bm.CHANNEL_SLOTS:
                return (
                    "📤 <b>UPLOAD CHANNEL BANNER</b>\n\n"
                    f"Target: <b>{bm._slot_label(slot)}</b>\n"
                    "Action: add a new rotating channel creative.\n\n"
                    "Send the banner now as a normal Telegram <b>Photo</b>.\n"
                    "Recommended channel format: <b>4:5 portrait</b> (about 1122×1402).\n\n"
                    "Nothing changes until you preview and tap <b>Approve & Apply</b>."
                )
            return old_upload_prompt(slot)
        upload_prompt_with_correct_ratio._channel_ratio_patched = True
        bm._upload_prompt = upload_prompt_with_correct_ratio


def _install_callback():
    global _previous_callback
    _previous_callback = bot.callback_handler

    async def bulk_callback(update, context):
        q = getattr(update, "callback_query", None)
        data = str(getattr(q, "data", "") or "") if q else ""
        if not q or not data.startswith("bm:bulk:"):
            return await _previous_callback(update, context)
        if not bot.is_admin(q.from_user.id):
            await q.answer("Admin only", show_alert=True)
            return

        action = data.split(":", 2)[2] if data.count(":") >= 2 else ""
        admin_id = q.from_user.id

        if action == "start":
            _start_session(admin_id)
            await q.answer("Bulk upload ready")
            await context.bot.send_message(
                chat_id=admin_id,
                text=_bulk_prompt(),
                parse_mode=bot.ParseMode.HTML,
                reply_markup=bot.InlineKeyboardMarkup([
                    [bot.InlineKeyboardButton("❌ Cancel Bulk Upload", callback_data="bm:bulk:cancel")]
                ]),
            )
            bot.logger.warning("BANNER_BULK_START admin=%s phase1=on max=%s", admin_id, BULK_MAX)
            return

        if action == "finish":
            ok, detail = await _finish_review(context, admin_id)
            await q.answer("Review ready ✅" if ok else detail, show_alert=not ok)
            return

        if action == "approve":
            ok, detail, approved = _approve_bulk(admin_id)
            await q.answer("All banners approved ✅" if ok else detail, show_alert=not ok)
            if ok:
                counts = {slot: bm._active_count(slot) for slot in bm.CHANNEL_SLOTS}
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=(
                        "✅ <b>BULK BANNERS APPROVED & ADDED TO ROTATION</b>\n\n"
                        f"10 AM: <b>{counts['quiz_open']} active</b>\n"
                        f"4 PM: <b>{counts['quiz_afternoon']} active</b>\n"
                        f"7 PM: <b>{counts['quiz_last_chance']} active</b>\n"
                        f"9:05 PM: <b>{counts['quiz_result']} active</b>\n\n"
                        "Rotation: <b>Round Robin • once per IST day</b>\n"
                        "Existing locked fallback banners: <b>kept</b>\n"
                        "No code edit or redeploy is needed for future uploads."
                    ),
                    parse_mode=bot.ParseMode.HTML,
                    reply_markup=bm._channel_markup(),
                )
                bot.logger.warning("BANNER_BULK_APPROVED admin=%s approved=%s", admin_id, len(approved))
            return

        if action == "cancel":
            _cancel_bulk(admin_id)
            await q.answer("Bulk upload cancelled")
            await context.bot.send_message(
                chat_id=admin_id,
                text="❌ Bulk upload cancelled. No pending banners were added to rotation.",
                reply_markup=bm._channel_markup(),
            )
            bot.logger.warning("BANNER_BULK_CANCELLED admin=%s", admin_id)
            return

        await q.answer("Unsupported bulk action", show_alert=True)

    bot.callback_handler = bulk_callback
    return bulk_callback


def _install_photo_handler():
    global _previous_post_init
    _previous_post_init = bot.post_init

    async def bulk_post_init(application):
        if _previous_post_init:
            await _previous_post_init(application)

        async def bulk_command(update, context):
            user = getattr(update, "effective_user", None)
            message = getattr(update, "effective_message", None)
            if not user or not message or not bot.is_admin(user.id):
                return
            _start_session(user.id)
            await message.reply_text(
                _bulk_prompt(),
                parse_mode=bot.ParseMode.HTML,
                reply_markup=bot.InlineKeyboardMarkup([
                    [bot.InlineKeyboardButton("❌ Cancel Bulk Upload", callback_data="bm:bulk:cancel")]
                ]),
            )

        async def bulk_photo_upload(update, context):
            user = getattr(update, "effective_user", None)
            message = getattr(update, "effective_message", None)
            if not user or not message or not bot.is_admin(user.id):
                return
            session = _session(user.id) or {}
            if str(session.get("status") or "") != "active":
                return
            photos = list(getattr(message, "photo", None) or [])
            if not photos:
                return

            existing = _items(user.id)
            if len(existing) >= BULK_MAX:
                await _update_progress(context, user.id, len(existing))
                raise ApplicationHandlerStop

            photo = photos[-1]
            unique_id = str(getattr(photo, "file_unique_id", "") or "")
            if _duplicate_in_session(user.id, unique_id):
                await context.bot.send_message(chat_id=user.id, text="ℹ️ Duplicate banner ignored in this bulk batch.")
                raise ApplicationHandlerStop

            seq_no = len(existing) + 1
            slot = _slot_for_sequence(seq_no)
            banner_id = bm._insert_pending(slot, str(photo.file_id), unique_id, user.id)
            try:
                _insert_item(user.id, seq_no, slot, banner_id, unique_id)
            except Exception:
                bm._cancel_pending(banner_id)
                raise

            new_count = seq_no
            bot.logger.warning(
                "BANNER_BULK_CAPTURED admin=%s seq=%s slot=%s banner_id=%s media_group=%s",
                user.id, seq_no, slot, banner_id, getattr(message, "media_group_id", None),
            )
            await _update_progress(context, user.id, new_count)

            if new_count >= BULK_MAX:
                await _finish_review(context, user.id)

            # Do not let the legacy single-photo Banner Manager consume the same photo.
            raise ApplicationHandlerStop

        application.add_handler(bot.CommandHandler("bulk_banners", bulk_command), group=-93)
        application.add_handler(bot.MessageHandler(bot.filters.PHOTO, bulk_photo_upload), group=-93)
        bot.logger.warning(
            "BANNER_BULK_HANDLERS active=on command=/bulk_banners photo_upload=admin_only "
            "phase1=2_open+2_afternoon+2_lastchance+1or2_results auto_finish_at=8"
        )

    bot.post_init = bulk_post_init


def install():
    global _installed
    if _installed:
        return bot.callback_handler
    _ensure_schema()
    _patch_menus_and_prompts()
    handler = _install_callback()
    _install_photo_handler()
    _installed = True
    bot.logger.warning(
        "BANNER_BULK_UPLOAD active=on phase1=7or8 auto_segregate=by_order "
        "channel_ratio=4x5 approve_all=on rotation=round_robin_IST fallbacks=preserved"
    )
    return handler
