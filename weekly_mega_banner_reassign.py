"""Admin-only remapping wizard for the five uploaded Sunday Mega Quiz banners.

Weekly-only corrective tooling. It does not touch Daily Quiz code, timing, banners,
rewards, reminders, or the 12/09 Closing branch.

Why this exists: Telegram Photo albums do not carry semantic slot labels. The bulk
uploader can preserve message order, but it cannot inspect the words printed inside
an image. This wizard snapshots the five currently approved Mega images and lets the
admin visually assign each image to the correct weekly slot before one atomic apply.
No re-upload is required.
"""
from __future__ import annotations

import time

from telegram.ext import ApplicationHandlerStop

import bot

_installed = False
_additions = None
_schedule = None
_previous_callback = None
_previous_post_init = None

TARGETS = (
    ("mega_preview", "🗓 Saturday Preview"),
    ("mega_open", "🟢 Sunday Open"),
    ("mega_afternoon", "🕓 Sunday Afternoon"),
    ("mega_last_chance", "⏰ Sunday Last Chance"),
    ("mega_result", "🏆 Sunday Results"),
)
TARGET_LABEL = dict(TARGETS)


def _ensure_schema():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS mega_quiz_banner_reassign_sessions (
                    admin_id BIGINT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'active',
                    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS mega_quiz_banner_reassign_items (
                    admin_id BIGINT NOT NULL,
                    source_no INTEGER NOT NULL,
                    source_file_id TEXT NOT NULL,
                    source_slot_key TEXT NOT NULL,
                    target_slot_key TEXT,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY(admin_id, source_no)
                )
            """)
        conn.commit()


def _rows(admin_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT * FROM mega_quiz_banner_reassign_items
                WHERE admin_id=%s ORDER BY source_no
            """, (int(admin_id),))
            return cur.fetchall() or []


def _start(admin_id):
    snapshots = []
    for source_no, key in enumerate(_additions.MEGA_MEDIA_ORDER, 1):
        file_id = _additions._approved_file_id(key)
        if not file_id:
            return False, f"Missing approved weekly banner for {key}. Upload all 5 first."
        snapshots.append((source_no, str(file_id), str(key)))
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM mega_quiz_banner_reassign_items WHERE admin_id=%s", (int(admin_id),))
            cur.execute("""
                INSERT INTO mega_quiz_banner_reassign_sessions(admin_id,status,started_at,updated_at)
                VALUES (%s,'active',NOW(),NOW())
                ON CONFLICT(admin_id) DO UPDATE SET status='active',started_at=NOW(),updated_at=NOW()
            """, (int(admin_id),))
            for source_no, file_id, source_key in snapshots:
                cur.execute("""
                    INSERT INTO mega_quiz_banner_reassign_items(
                        admin_id,source_no,source_file_id,source_slot_key,target_slot_key
                    ) VALUES (%s,%s,%s,%s,NULL)
                """, (int(admin_id), source_no, file_id, source_key))
        conn.commit()
    return True, "Ready"


def _assign(admin_id, source_no, target_key):
    if target_key not in TARGET_LABEL:
        return False, "Unknown target slot"
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            # Keep targets one-to-one. If this target was already chosen for another
            # image, clear that older choice rather than silently duplicating it.
            cur.execute("""
                UPDATE mega_quiz_banner_reassign_items
                SET target_slot_key=NULL,updated_at=NOW()
                WHERE admin_id=%s AND target_slot_key=%s AND source_no<>%s
            """, (int(admin_id), str(target_key), int(source_no)))
            cur.execute("""
                UPDATE mega_quiz_banner_reassign_items
                SET target_slot_key=%s,updated_at=NOW()
                WHERE admin_id=%s AND source_no=%s
            """, (str(target_key), int(admin_id), int(source_no)))
            changed = cur.rowcount == 1
        conn.commit()
    return changed, "Assigned" if changed else "Image not found"


def _mapping_complete(admin_id):
    rows = _rows(admin_id)
    targets = [str(r.get("target_slot_key") or "") for r in rows]
    return len(rows) == 5 and all(targets) and len(set(targets)) == 5


def _status_text(admin_id):
    rows = _rows(admin_id)
    lines = ["🧩 <b>WEEKLY BANNER REMAP</b>", ""]
    for row in rows:
        n = int(row["source_no"])
        target = str(row.get("target_slot_key") or "")
        lines.append(f"Image {n}: <b>{TARGET_LABEL.get(target, 'Not assigned')}</b>")
    if _mapping_complete(admin_id):
        lines += ["", "✅ All 5 slots are assigned. Review the corrected order before applying."]
    else:
        lines += ["", "Tap the correct slot under each image. Every slot can be used once."]
    return "\n".join(lines)


def _slot_buttons(source_no):
    return bot.InlineKeyboardMarkup([
        [bot.InlineKeyboardButton("🗓 Sat Preview", callback_data=f"mega_map:{source_no}:mega_preview")],
        [bot.InlineKeyboardButton("🟢 Sun Open", callback_data=f"mega_map:{source_no}:mega_open")],
        [bot.InlineKeyboardButton("🕓 Sun Afternoon", callback_data=f"mega_map:{source_no}:mega_afternoon")],
        [bot.InlineKeyboardButton("⏰ Sun Last Chance", callback_data=f"mega_map:{source_no}:mega_last_chance")],
        [bot.InlineKeyboardButton("🏆 Sun Results", callback_data=f"mega_map:{source_no}:mega_result")],
    ])


def _status_markup(admin_id):
    rows = []
    if _mapping_complete(admin_id):
        rows.append([bot.InlineKeyboardButton("👁 Review Corrected Mapping", callback_data="mega_map:review")])
    rows.append([bot.InlineKeyboardButton("❌ Cancel Remap", callback_data="mega_map:cancel")])
    return bot.InlineKeyboardMarkup(rows)


async def _send_assignment_cards(context, admin_id):
    await context.bot.send_message(
        chat_id=int(admin_id),
        text=(
            "🧩 <b>FIX WEEKLY MEGA BANNER MAPPING</b>\n\n"
            "The previous bulk upload assigned by Telegram album order, not by reading the image content.\n\n"
            "Below are the same 5 already-uploaded images. For each image, tap what it actually represents. "
            "No re-upload is needed and nothing changes live until final approval."
        ),
        parse_mode=bot.ParseMode.HTML,
    )
    for row in _rows(admin_id):
        n = int(row["source_no"])
        await context.bot.send_photo(
            chat_id=int(admin_id),
            photo=str(row["source_file_id"]),
            caption=f"<b>IMAGE {n}/5</b> — choose the correct weekly slot below",
            parse_mode=bot.ParseMode.HTML,
            reply_markup=_slot_buttons(n),
        )
    await context.bot.send_message(
        chat_id=int(admin_id),
        text=_status_text(admin_id),
        parse_mode=bot.ParseMode.HTML,
        reply_markup=_status_markup(admin_id),
    )


async def _review(context, admin_id):
    if not _mapping_complete(admin_id):
        return False, "Assign all 5 images first."
    rows = _rows(admin_id)
    by_target = {str(r["target_slot_key"]): r for r in rows}
    await context.bot.send_message(
        chat_id=int(admin_id),
        text="👁 <b>CORRECTED WEEKLY ORDER — FINAL REVIEW</b>\n\nCheck these five images carefully before applying.",
        parse_mode=bot.ParseMode.HTML,
    )
    for key, label in TARGETS:
        row = by_target[key]
        await context.bot.send_photo(
            chat_id=int(admin_id),
            photo=str(row["source_file_id"]),
            caption=f"<b>{label}</b>",
            parse_mode=bot.ParseMode.HTML,
        )
    await context.bot.send_message(
        chat_id=int(admin_id),
        text=(
            "If the five previews above are now correct, tap <b>Apply Correct Mapping</b>.\n\n"
            "This changes only the five Weekly Mega Quiz media file IDs. Daily Quiz remains untouched."
        ),
        parse_mode=bot.ParseMode.HTML,
        reply_markup=bot.InlineKeyboardMarkup([
            [bot.InlineKeyboardButton("✅ Apply Correct Mapping", callback_data="mega_map:approve")],
            [bot.InlineKeyboardButton("⬅️ Keep Editing", callback_data="mega_map:status")],
            [bot.InlineKeyboardButton("❌ Cancel", callback_data="mega_map:cancel")],
        ]),
    )
    return True, "Review ready"


def _approve(admin_id):
    if not _mapping_complete(admin_id):
        return False, "Assign all 5 images first."
    rows = _rows(admin_id)
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            for row in rows:
                target = str(row["target_slot_key"])
                cur.execute("""
                    UPDATE mega_quiz_media_assets
                    SET approved_file_id=%s,
                        approved_file_unique_id=NULL,
                        pending_file_id=NULL,
                        pending_file_unique_id=NULL,
                        status='approved',approved_by=%s,approved_at=NOW(),updated_at=NOW()
                    WHERE asset_key=%s
                """, (str(row["source_file_id"]), int(admin_id), target))
                if cur.rowcount != 1:
                    raise RuntimeError(f"Weekly target missing: {target}")
            cur.execute("""
                UPDATE mega_quiz_banner_reassign_sessions
                SET status='approved',updated_at=NOW() WHERE admin_id=%s
            """, (int(admin_id),))
        conn.commit()
    bot.logger.warning("MEGA_BANNER_REMAP_APPROVED admin=%s weekly_only=on daily_untouched=on", admin_id)
    return True, "Applied"


def _cancel(admin_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE mega_quiz_banner_reassign_sessions
                SET status='cancelled',updated_at=NOW() WHERE admin_id=%s
            """, (int(admin_id),))
        conn.commit()


def _patch_menu():
    old_menu = _additions._media_menu
    if getattr(old_menu, "_remap_patched", False):
        return

    def menu_with_remap():
        markup = old_menu()
        rows = [list(r) for r in (getattr(markup, "inline_keyboard", None) or [])]
        rows.insert(1, [bot.InlineKeyboardButton("🧩 Fix / Reassign Current 5", callback_data="mega_map:start")])
        return bot.InlineKeyboardMarkup(rows)

    menu_with_remap._remap_patched = True
    _additions._media_menu = menu_with_remap


def _install_callback():
    global _previous_callback
    _previous_callback = bot.callback_handler

    async def callback(update, context):
        q = getattr(update, "callback_query", None)
        data = str(getattr(q, "data", "") or "") if q else ""
        if not q or not data.startswith("mega_map:"):
            return await _previous_callback(update, context)
        if not bot.is_admin(q.from_user.id):
            await q.answer("Admin only", show_alert=True)
            return
        admin_id = int(q.from_user.id)
        parts = data.split(":")
        action = parts[1] if len(parts) > 1 else ""

        if action == "start":
            ok, detail = _start(admin_id)
            await q.answer("Remap ready" if ok else detail, show_alert=not ok)
            if ok:
                await _send_assignment_cards(context, admin_id)
            return

        if action.isdigit() and len(parts) == 3:
            ok, detail = _assign(admin_id, int(action), parts[2])
            await q.answer(TARGET_LABEL.get(parts[2], detail) if ok else detail, show_alert=not ok)
            await context.bot.send_message(
                chat_id=admin_id,
                text=_status_text(admin_id),
                parse_mode=bot.ParseMode.HTML,
                reply_markup=_status_markup(admin_id),
                disable_notification=True,
            )
            return

        if action == "review":
            ok, detail = await _review(context, admin_id)
            await q.answer("Review ready" if ok else detail, show_alert=not ok)
            return

        if action == "approve":
            ok, detail = _approve(admin_id)
            await q.answer("Correct mapping applied ✅" if ok else detail, show_alert=not ok)
            if ok:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=(
                        "✅ <b>WEEKLY MEGA BANNERS REMAPPED</b>\n\n"
                        "The five weekly banner file IDs are now in the slots you approved.\n"
                        "Daily Quiz code, banners and schedule were not changed."
                    ),
                    parse_mode=bot.ParseMode.HTML,
                    reply_markup=_additions._media_menu(),
                )
            return

        if action == "status":
            await q.answer()
            await context.bot.send_message(
                chat_id=admin_id,
                text=_status_text(admin_id),
                parse_mode=bot.ParseMode.HTML,
                reply_markup=_status_markup(admin_id),
            )
            return

        if action == "cancel":
            _cancel(admin_id)
            await q.answer("Remap cancelled")
            await context.bot.send_message(
                chat_id=admin_id,
                text="❌ Remap cancelled. Existing weekly banner assignments were left unchanged.",
                reply_markup=_additions._media_menu(),
            )
            return

        await q.answer("Unknown remap action", show_alert=True)

    bot.callback_handler = callback


def _install_command():
    global _previous_post_init
    _previous_post_init = bot.post_init

    async def post_init(application):
        if _previous_post_init:
            await _previous_post_init(application)

        async def command(update, context):
            user = getattr(update, "effective_user", None)
            message = getattr(update, "effective_message", None)
            if not user or not message or not bot.is_admin(user.id):
                return
            ok, detail = _start(user.id)
            if not ok:
                await message.reply_text("❌ " + detail)
                raise ApplicationHandlerStop
            await _send_assignment_cards(context, user.id)
            raise ApplicationHandlerStop

        application.add_handler(bot.CommandHandler("mega_fix_banners", command), group=-95)
        bot.logger.warning("MEGA_BANNER_REMAP_HANDLERS active=on command=/mega_fix_banners admin_only=on no_reupload=on")

    bot.post_init = post_init


def install(additions_module, schedule_module):
    global _installed, _additions, _schedule
    _additions = additions_module
    _schedule = schedule_module
    if _installed:
        return
    _ensure_schema()
    _patch_menu()
    _install_callback()
    _install_command()
    _installed = True
    bot.logger.warning(
        "MEGA_BANNER_REMAP active=on weekly_only=on visual_manual_assignment=on atomic_apply=on "
        "no_reupload=on daily_quiz_untouched=on"
    )
