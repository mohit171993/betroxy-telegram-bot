"""Admin-managed BETROXY banner library and quiz-channel rotation.

This module deliberately stores Telegram file_ids instead of image paths.
Banner replacement therefore does not require a code edit or Railway deploy.

Welcome banners:
- one active Start banner
- one active Telegram Business banner
- previous approved versions remain available for rollback

Quiz channel banners:
- multiple approved creatives per time slot
- round-robin rotation once per IST calendar day
- current four locked channel creatives are imported as permanent fallbacks
- if the rotating library is unavailable, channel_media_manager keeps its existing
  locked-file-id/text fallback behavior
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bot

IST = timezone(timedelta(hours=5, minutes=30))

WELCOME_SLOTS = {
    "welcome_start": "OfficialBot /start Banner",
    "welcome_business": "Business / Direct-DM Banner",
}
CHANNEL_SLOTS = {
    "quiz_open": "10:00 AM IST — Quiz Open",
    "quiz_afternoon": "4:00 PM IST — Afternoon / Top 3",
    "quiz_last_chance": "7:00 PM IST — Last Chance",
    "quiz_result": "9:05 PM IST — Results / Winners",
}
ALL_SLOTS = {**WELCOME_SLOTS, **CHANNEL_SLOTS}
SPECIAL_UPLOAD_SLOTS = {"welcome_both"}

_previous_callback = None
_previous_post_init = None
_installed = False


def _today_ist():
    return datetime.now(timezone.utc).astimezone(IST).date()


def _ensure_schema():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS betroxy_banner_library (
                    id BIGSERIAL PRIMARY KEY,
                    slot_key TEXT NOT NULL,
                    file_id TEXT NOT NULL,
                    file_unique_id TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    is_active BOOLEAN NOT NULL DEFAULT FALSE,
                    is_fallback BOOLEAN NOT NULL DEFAULT FALSE,
                    sort_order INTEGER NOT NULL DEFAULT 100,
                    uploaded_by BIGINT,
                    approved_by BIGINT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    approved_at TIMESTAMPTZ,
                    disabled_at TIMESTAMPTZ
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_betroxy_banner_slot ON betroxy_banner_library(slot_key,status,is_active,sort_order,id)")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS betroxy_banner_upload_state (
                    admin_id BIGINT PRIMARY KEY,
                    slot_key TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS betroxy_banner_rotation (
                    slot_key TEXT PRIMARY KEY,
                    last_banner_id BIGINT,
                    last_rotation_date DATE,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
        conn.commit()


def _seed_locked_channel_creatives():
    """Import the existing four approved Telegram file_ids as fallback variants."""
    try:
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT asset_key,approved_file_id,approved_file_unique_id
                    FROM channel_media_assets
                    WHERE status='approved' AND approved_file_id IS NOT NULL
                """)
                rows = cur.fetchall() or []
                seeded = 0
                for row in rows:
                    slot = str(row.get("asset_key") or "")
                    file_id = str(row.get("approved_file_id") or "").strip()
                    unique_id = str(row.get("approved_file_unique_id") or "").strip() or None
                    if slot not in CHANNEL_SLOTS or not file_id:
                        continue
                    cur.execute(
                        "SELECT id FROM betroxy_banner_library WHERE slot_key=%s AND file_id=%s LIMIT 1",
                        (slot, file_id),
                    )
                    if cur.fetchone():
                        continue
                    cur.execute("SELECT COALESCE(MAX(sort_order),0)+10 AS n FROM betroxy_banner_library WHERE slot_key=%s", (slot,))
                    order = int((cur.fetchone() or {}).get("n") or 10)
                    cur.execute("""
                        INSERT INTO betroxy_banner_library(
                            slot_key,file_id,file_unique_id,status,is_active,is_fallback,sort_order,
                            uploaded_by,approved_by,approved_at
                        ) VALUES (%s,%s,%s,'approved',TRUE,TRUE,%s,%s,%s,NOW())
                    """, (slot, file_id, unique_id, order, int(bot.ADMIN_ID), int(bot.ADMIN_ID)))
                    seeded += 1
                # Ensure every populated channel slot has a fallback.
                for slot in CHANNEL_SLOTS:
                    cur.execute("""
                        SELECT COUNT(*) AS n FROM betroxy_banner_library
                        WHERE slot_key=%s AND status='approved' AND is_active=TRUE AND is_fallback=TRUE
                    """, (slot,))
                    if int((cur.fetchone() or {}).get("n") or 0) == 0:
                        cur.execute("""
                            UPDATE betroxy_banner_library SET is_fallback=TRUE
                            WHERE id=(
                                SELECT id FROM betroxy_banner_library
                                WHERE slot_key=%s AND status='approved' AND is_active=TRUE
                                ORDER BY sort_order,id LIMIT 1
                            )
                        """, (slot,))
            conn.commit()
        if seeded:
            bot.logger.warning("BANNER_MANAGER seeded_locked_channel_creatives=%s", seeded)
    except Exception:
        # The old table may not exist in a fresh/test DB. This must never block startup.
        bot.logger.exception("BANNER_MANAGER_SEED_LOCKED_FAILED")


def _set_upload_state(admin_id, slot_key):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO betroxy_banner_upload_state(admin_id,slot_key,updated_at)
                VALUES (%s,%s,NOW())
                ON CONFLICT(admin_id) DO UPDATE SET slot_key=EXCLUDED.slot_key,updated_at=NOW()
            """, (int(admin_id), str(slot_key)))
        conn.commit()


def _get_upload_state(admin_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT slot_key FROM betroxy_banner_upload_state WHERE admin_id=%s", (int(admin_id),))
            row = cur.fetchone() or {}
    slot = str(row.get("slot_key") or "")
    return slot if slot in ALL_SLOTS or slot in SPECIAL_UPLOAD_SLOTS else None


def _clear_upload_state(admin_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM betroxy_banner_upload_state WHERE admin_id=%s", (int(admin_id),))
        conn.commit()


def _slot_label(slot):
    if slot == "welcome_both":
        return "Both Welcome Banners"
    return ALL_SLOTS.get(slot, slot)


def _active_count(slot):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) AS n FROM betroxy_banner_library
                WHERE slot_key=%s AND status='approved' AND is_active=TRUE
            """, (slot,))
            return int((cur.fetchone() or {}).get("n") or 0)


def _banner_row(banner_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM betroxy_banner_library WHERE id=%s", (int(banner_id),))
            return cur.fetchone()


def _slot_rows(slot, active_only=False):
    where = "AND status='approved' AND is_active=TRUE" if active_only else "AND status IN ('approved','disabled')"
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT * FROM betroxy_banner_library
                WHERE slot_key=%s {where}
                ORDER BY sort_order,id
            """, (slot,))
            return cur.fetchall() or []


def get_welcome_file_id(kind):
    """Return active Telegram file_id for start/business, or None for legacy fallback."""
    slot = "welcome_start" if str(kind) in {"start", "welcome_start"} else "welcome_business"
    try:
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT file_id FROM betroxy_banner_library
                    WHERE slot_key=%s AND status='approved' AND is_active=TRUE
                    ORDER BY approved_at DESC NULLS LAST,id DESC LIMIT 1
                """, (slot,))
                row = cur.fetchone() or {}
        value = str(row.get("file_id") or "").strip()
        return value or None
    except Exception:
        bot.logger.exception("BANNER_MANAGER_WELCOME_LOOKUP_FAILED slot=%s", slot)
        return None


def _rotation_candidate(slot, advance):
    """Choose today's round-robin creative. Preview calls use advance=False."""
    if slot not in CHANNEL_SLOTS:
        return None
    today = _today_ist()
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id,file_id,sort_order FROM betroxy_banner_library
                WHERE slot_key=%s AND status='approved' AND is_active=TRUE
                ORDER BY sort_order,id
            """, (slot,))
            rows = cur.fetchall() or []
            if not rows:
                return None
            cur.execute("SELECT last_banner_id,last_rotation_date FROM betroxy_banner_rotation WHERE slot_key=%s", (slot,))
            state = cur.fetchone() or {}
            last_id = state.get("last_banner_id")
            last_date = state.get("last_rotation_date")

            if last_date == today and last_id:
                for row in rows:
                    if int(row["id"]) == int(last_id):
                        return row

            next_row = rows[0]
            if last_id:
                ids = [int(r["id"]) for r in rows]
                if int(last_id) in ids:
                    idx = (ids.index(int(last_id)) + 1) % len(rows)
                    next_row = rows[idx]

            if advance:
                cur.execute("""
                    INSERT INTO betroxy_banner_rotation(slot_key,last_banner_id,last_rotation_date,updated_at)
                    VALUES (%s,%s,%s,NOW())
                    ON CONFLICT(slot_key) DO UPDATE SET
                        last_banner_id=EXCLUDED.last_banner_id,
                        last_rotation_date=EXCLUDED.last_rotation_date,
                        updated_at=NOW()
                """, (slot, int(next_row["id"]), today))
                conn.commit()
            return next_row


def get_channel_file_id(slot):
    """Production selector used by channel_media_manager for actual sends."""
    try:
        row = _rotation_candidate(slot, advance=True) or {}
        value = str(row.get("file_id") or "").strip()
        if value:
            bot.logger.warning("BANNER_ROTATION_SELECT slot=%s banner_id=%s date=%s", slot, row.get("id"), _today_ist())
        return value or None
    except Exception:
        bot.logger.exception("BANNER_ROTATION_SELECT_FAILED slot=%s", slot)
        return None


def _next_channel_row(slot):
    try:
        return _rotation_candidate(slot, advance=False)
    except Exception:
        bot.logger.exception("BANNER_ROTATION_PREVIEW_FAILED slot=%s", slot)
        return None


def _main_markup():
    return bot.InlineKeyboardMarkup([
        [bot.InlineKeyboardButton("👋 Welcome Banners", callback_data="bm:welcome")],
        [bot.InlineKeyboardButton("🏆 Quiz Channel Banners", callback_data="bm:channel")],
        [bot.InlineKeyboardButton("📚 Banner Library", callback_data="bm:library")],
        [bot.InlineKeyboardButton("👁 Preview Next Quiz Banners", callback_data="bm:previewall")],
        [bot.InlineKeyboardButton("⬅️ Back to Admin", callback_data="v94_admin_home")],
    ])


def _welcome_markup():
    return bot.InlineKeyboardMarkup([
        [bot.InlineKeyboardButton("🖼 Update Start Banner", callback_data="bm:upload:welcome_start")],
        [bot.InlineKeyboardButton("💬 Update Business Banner", callback_data="bm:upload:welcome_business")],
        [bot.InlineKeyboardButton("🔄 Update Both Banners", callback_data="bm:upload:welcome_both")],
        [
            bot.InlineKeyboardButton("👁 Preview Start", callback_data="bm:preview:welcome_start"),
            bot.InlineKeyboardButton("👁 Preview Business", callback_data="bm:preview:welcome_business"),
        ],
        [
            bot.InlineKeyboardButton("↩ Restore Start", callback_data="bm:restore:welcome_start"),
            bot.InlineKeyboardButton("↩ Restore Business", callback_data="bm:restore:welcome_business"),
        ],
        [bot.InlineKeyboardButton("⬅️ Banner Manager", callback_data="bm:home")],
    ])


def _channel_markup():
    return bot.InlineKeyboardMarkup([
        [bot.InlineKeyboardButton("🕙 10 AM — Quiz Open", callback_data="bm:slot:quiz_open")],
        [bot.InlineKeyboardButton("🕓 4 PM — Afternoon", callback_data="bm:slot:quiz_afternoon")],
        [bot.InlineKeyboardButton("🕖 7 PM — Last Chance", callback_data="bm:slot:quiz_last_chance")],
        [bot.InlineKeyboardButton("🏆 9:05 PM — Results", callback_data="bm:slot:quiz_result")],
        [bot.InlineKeyboardButton("👁 Preview Next Rotation", callback_data="bm:previewall")],
        [bot.InlineKeyboardButton("⬅️ Banner Manager", callback_data="bm:home")],
    ])


def _slot_markup(slot):
    return bot.InlineKeyboardMarkup([
        [bot.InlineKeyboardButton("➕ Add Banner", callback_data=f"bm:upload:{slot}")],
        [bot.InlineKeyboardButton("📚 View Library", callback_data=f"bm:list:{slot}")],
        [bot.InlineKeyboardButton("👁 Preview Next", callback_data=f"bm:preview:{slot}")],
        [bot.InlineKeyboardButton("⬅️ Quiz Channel Banners", callback_data="bm:channel")],
    ])


def _manager_text():
    welcome_start = "✅ Custom" if get_welcome_file_id("start") else "↪ Legacy fallback"
    welcome_business = "✅ Custom" if get_welcome_file_id("business") else "↪ Legacy fallback"
    counts = {slot: _active_count(slot) for slot in CHANNEL_SLOTS}
    return (
        "🖼 <b>BETROXY BANNER MANAGER</b>\n\n"
        f"OfficialBot /start: <b>{welcome_start}</b>\n"
        f"Business DM: <b>{welcome_business}</b>\n\n"
        "<b>Quiz Channel Rotation</b>\n"
        f"10 AM: <b>{counts['quiz_open']}</b> active\n"
        f"4 PM: <b>{counts['quiz_afternoon']}</b> active\n"
        f"7 PM: <b>{counts['quiz_last_chance']}</b> active\n"
        f"9:05 PM: <b>{counts['quiz_result']}</b> active\n\n"
        "Rotation: <b>Round Robin • once per IST day</b>\n"
        "Storage: <b>Telegram file_id</b>\n"
        "Code edit / redeploy for banner changes: <b>NOT REQUIRED</b>"
    )


def _upload_prompt(slot):
    mode = "replace the active welcome banner" if slot.startswith("welcome") else "add a new rotating channel creative"
    return (
        "📤 <b>UPLOAD BANNER</b>\n\n"
        f"Target: <b>{_slot_label(slot)}</b>\n"
        f"Action: {mode}.\n\n"
        "Send the new banner now as a normal Telegram <b>Photo</b>.\n"
        "Recommended: <b>16:9</b>.\n\n"
        "Nothing changes until you preview and tap <b>Approve & Apply</b>."
    )


def _insert_pending(slot, file_id, unique_id, admin_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COALESCE(MAX(sort_order),0)+10 AS n FROM betroxy_banner_library WHERE slot_key=%s", (slot,))
            order = int((cur.fetchone() or {}).get("n") or 10)
            cur.execute("""
                INSERT INTO betroxy_banner_library(
                    slot_key,file_id,file_unique_id,status,is_active,is_fallback,sort_order,uploaded_by
                ) VALUES (%s,%s,%s,'pending',FALSE,FALSE,%s,%s)
                RETURNING id
            """, (slot, file_id, unique_id, order, int(admin_id)))
            row = cur.fetchone() or {}
        conn.commit()
    return int(row["id"])


def _approve_banner(banner_id, admin_id):
    row = _banner_row(banner_id) or {}
    if str(row.get("status") or "") != "pending":
        return False, "This upload is no longer pending."
    slot = str(row.get("slot_key") or "")
    file_id = str(row.get("file_id") or "")
    unique_id = row.get("file_unique_id")
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            if slot == "welcome_both":
                for target in ("welcome_start", "welcome_business"):
                    cur.execute("""
                        UPDATE betroxy_banner_library SET is_active=FALSE
                        WHERE slot_key=%s AND status='approved' AND is_active=TRUE
                    """, (target,))
                    cur.execute("SELECT COALESCE(MAX(sort_order),0)+10 AS n FROM betroxy_banner_library WHERE slot_key=%s", (target,))
                    order = int((cur.fetchone() or {}).get("n") or 10)
                    cur.execute("""
                        INSERT INTO betroxy_banner_library(
                            slot_key,file_id,file_unique_id,status,is_active,is_fallback,sort_order,
                            uploaded_by,approved_by,approved_at
                        ) VALUES (%s,%s,%s,'approved',TRUE,FALSE,%s,%s,%s,NOW())
                    """, (target, file_id, unique_id, order, int(admin_id), int(admin_id)))
                cur.execute("UPDATE betroxy_banner_library SET status='applied',approved_by=%s,approved_at=NOW() WHERE id=%s", (int(admin_id), int(banner_id)))
            elif slot in WELCOME_SLOTS:
                cur.execute("""
                    UPDATE betroxy_banner_library SET is_active=FALSE
                    WHERE slot_key=%s AND status='approved' AND is_active=TRUE
                """, (slot,))
                cur.execute("""
                    UPDATE betroxy_banner_library
                    SET status='approved',is_active=TRUE,approved_by=%s,approved_at=NOW()
                    WHERE id=%s
                """, (int(admin_id), int(banner_id)))
            elif slot in CHANNEL_SLOTS:
                cur.execute("SELECT COUNT(*) AS n FROM betroxy_banner_library WHERE slot_key=%s AND status='approved' AND is_active=TRUE AND is_fallback=TRUE", (slot,))
                has_fallback = int((cur.fetchone() or {}).get("n") or 0) > 0
                cur.execute("""
                    UPDATE betroxy_banner_library
                    SET status='approved',is_active=TRUE,is_fallback=%s,approved_by=%s,approved_at=NOW()
                    WHERE id=%s
                """, (not has_fallback, int(admin_id), int(banner_id)))
            else:
                return False, "Unknown banner slot."
        conn.commit()
    return True, "Approved"


def _cancel_pending(banner_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE betroxy_banner_library SET status='disabled',is_active=FALSE,disabled_at=NOW() WHERE id=%s AND status='pending'", (int(banner_id),))
            changed = cur.rowcount > 0
        conn.commit()
    return changed


def _restore_previous(slot):
    if slot not in WELCOME_SLOTS:
        return False
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id FROM betroxy_banner_library
                WHERE slot_key=%s AND status='approved'
                ORDER BY is_active DESC,approved_at DESC NULLS LAST,id DESC
            """, (slot,))
            rows = cur.fetchall() or []
            if len(rows) < 2:
                return False
            current_id = int(rows[0]["id"])
            previous_id = int(rows[1]["id"])
            cur.execute("UPDATE betroxy_banner_library SET is_active=FALSE WHERE id=%s", (current_id,))
            cur.execute("UPDATE betroxy_banner_library SET is_active=TRUE WHERE id=%s", (previous_id,))
        conn.commit()
    return True


def _set_fallback(banner_id):
    row = _banner_row(banner_id) or {}
    slot = str(row.get("slot_key") or "")
    if slot not in CHANNEL_SLOTS or str(row.get("status") or "") != "approved" or not row.get("is_active"):
        return False
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE betroxy_banner_library SET is_fallback=FALSE WHERE slot_key=%s", (slot,))
            cur.execute("UPDATE betroxy_banner_library SET is_fallback=TRUE WHERE id=%s", (int(banner_id),))
        conn.commit()
    return True


def _disable_banner(banner_id):
    row = _banner_row(banner_id) or {}
    slot = str(row.get("slot_key") or "")
    if slot not in CHANNEL_SLOTS or str(row.get("status") or "") != "approved" or not row.get("is_active"):
        return False, "Banner is not an active channel creative."
    if _active_count(slot) <= 1:
        return False, "At least one active banner must remain in this slot."
    if row.get("is_fallback"):
        return False, "Set another banner as fallback before disabling this one."
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE betroxy_banner_library SET status='disabled',is_active=FALSE,disabled_at=NOW() WHERE id=%s", (int(banner_id),))
        conn.commit()
    return True, "Disabled"


def _move_banner(banner_id, direction):
    row = _banner_row(banner_id) or {}
    slot = str(row.get("slot_key") or "")
    if slot not in CHANNEL_SLOTS or str(row.get("status") or "") != "approved":
        return False
    rows = _slot_rows(slot, active_only=True)
    ids = [int(r["id"]) for r in rows]
    if int(banner_id) not in ids:
        return False
    idx = ids.index(int(banner_id))
    swap_idx = idx - 1 if direction == "up" else idx + 1
    if swap_idx < 0 or swap_idx >= len(rows):
        return False
    other = rows[swap_idx]
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE betroxy_banner_library SET sort_order=%s WHERE id=%s", (int(other["sort_order"]), int(banner_id)))
            cur.execute("UPDATE betroxy_banner_library SET sort_order=%s WHERE id=%s", (int(row["sort_order"]), int(other["id"])))
        conn.commit()
    return True


async def _send_menu(message, text, markup):
    try:
        await message.edit_text(text, parse_mode=bot.ParseMode.HTML, reply_markup=markup, disable_web_page_preview=True)
    except Exception:
        await message.reply_text(text, parse_mode=bot.ParseMode.HTML, reply_markup=markup, disable_web_page_preview=True)


async def _preview_slot(context, chat_id, slot):
    if slot in WELCOME_SLOTS:
        kind = "start" if slot == "welcome_start" else "business"
        file_id = get_welcome_file_id(kind)
        if not file_id:
            await context.bot.send_message(chat_id=chat_id, text=f"ℹ️ {_slot_label(slot)} is still using the legacy built-in fallback banner.")
            return
        await context.bot.send_photo(chat_id=chat_id, photo=file_id, caption=f"👁 <b>Current: {_slot_label(slot)}</b>", parse_mode=bot.ParseMode.HTML)
        return
    row = _next_channel_row(slot) or {}
    file_id = str(row.get("file_id") or "").strip()
    if not file_id:
        await context.bot.send_message(chat_id=chat_id, text=f"No active banner found for {_slot_label(slot)}.")
        return
    await context.bot.send_photo(
        chat_id=chat_id,
        photo=file_id,
        caption=f"👁 <b>Next rotation preview</b>\n{_slot_label(slot)}\nBanner #{row.get('id')}",
        parse_mode=bot.ParseMode.HTML,
    )


async def _show_library(context, chat_id, slot):
    rows = _slot_rows(slot, active_only=False)
    active = [r for r in rows if str(r.get("status")) == "approved" and r.get("is_active")]
    if not rows:
        await context.bot.send_message(chat_id=chat_id, text=f"No saved banners yet for {_slot_label(slot)}.")
        return
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"📚 <b>{_slot_label(slot)}</b>\n\n"
            f"Active rotation: <b>{len(active)}</b>\n"
            "⭐ = fallback • ✅ = active • 🚫 = disabled\n\n"
            "Use the controls under each preview."
        ),
        parse_mode=bot.ParseMode.HTML,
    )
    for row in rows[:12]:
        status = str(row.get("status") or "")
        active_flag = bool(row.get("is_active")) and status == "approved"
        prefix = "✅" if active_flag else "🚫"
        fallback = " ⭐ FALLBACK" if row.get("is_fallback") else ""
        controls = []
        if active_flag:
            controls.append(bot.InlineKeyboardButton("⬆️", callback_data=f"bm:up:{row['id']}"))
            controls.append(bot.InlineKeyboardButton("⬇️", callback_data=f"bm:down:{row['id']}"))
            if not row.get("is_fallback"):
                controls.append(bot.InlineKeyboardButton("⭐ Fallback", callback_data=f"bm:fallback:{row['id']}"))
                controls.append(bot.InlineKeyboardButton("🚫 Disable", callback_data=f"bm:disable:{row['id']}"))
        try:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=str(row.get("file_id")),
                caption=f"{prefix} Banner #{row['id']}{fallback}\nOrder: {row.get('sort_order')}",
                reply_markup=bot.InlineKeyboardMarkup([controls]) if controls else None,
            )
        except Exception:
            bot.logger.exception("BANNER_MANAGER_LIBRARY_PREVIEW_FAILED id=%s", row.get("id"))


def _patch_admin_menu():
    """Add a visible Banner Manager button to the existing admin control center."""
    try:
        import v94_admin_mode_cleanup as v94
        old_menu = v94.v94_admin_menu
        if getattr(old_menu, "_banner_manager_patched", False):
            return

        def wrapped_admin_menu():
            markup = old_menu()
            rows = [list(r) for r in (getattr(markup, "inline_keyboard", None) or [])]
            # Keep Customer View last.
            insert_at = max(0, len(rows) - 1)
            rows.insert(insert_at, [bot.InlineKeyboardButton("🖼 Banner Manager", callback_data="bm:home")])
            return bot.InlineKeyboardMarkup(rows)

        wrapped_admin_menu._banner_manager_patched = True
        v94.v94_admin_menu = wrapped_admin_menu
        bot.admin_menu = wrapped_admin_menu
        try:
            v94.v91.v91_admin_menu = wrapped_admin_menu
        except Exception:
            pass
        bot.logger.warning("BANNER_MANAGER_ADMIN_MENU button=on")
    except Exception:
        bot.logger.exception("BANNER_MANAGER_ADMIN_MENU_PATCH_FAILED")


def _install_callback():
    global _previous_callback
    _previous_callback = bot.callback_handler

    async def banner_callback(update, context):
        q = getattr(update, "callback_query", None)
        data = str(getattr(q, "data", "") or "") if q else ""
        if not q or not data.startswith("bm:"):
            return await _previous_callback(update, context)
        if not bot.is_admin(q.from_user.id):
            await q.answer("Admin only", show_alert=True)
            return

        parts = data.split(":")
        action = parts[1] if len(parts) > 1 else ""
        arg = parts[2] if len(parts) > 2 else ""

        if action == "home":
            await q.answer()
            await _send_menu(q.message, _manager_text(), _main_markup())
            return
        if action == "welcome":
            await q.answer()
            await _send_menu(q.message, "👋 <b>WELCOME BANNERS</b>\n\nUpload, preview or restore without touching code.", _welcome_markup())
            return
        if action == "channel":
            await q.answer()
            await _send_menu(q.message, "🏆 <b>QUIZ CHANNEL BANNERS</b>\n\nEach slot rotates its approved creatives round-robin by IST day.", _channel_markup())
            return
        if action == "library":
            await q.answer()
            await _send_menu(q.message, "📚 <b>BANNER LIBRARY</b>\n\nChoose a quiz slot to manage its approved creatives.", _channel_markup())
            return
        if action == "slot" and arg in CHANNEL_SLOTS:
            await q.answer()
            await _send_menu(
                q.message,
                f"🏆 <b>{_slot_label(arg)}</b>\n\nActive banners: <b>{_active_count(arg)}</b>\nRotation: <b>Round Robin</b>\nSame slot uses one selected banner per IST day.",
                _slot_markup(arg),
            )
            return
        if action == "upload" and (arg in ALL_SLOTS or arg in SPECIAL_UPLOAD_SLOTS):
            _set_upload_state(q.from_user.id, arg)
            await q.answer("Send the banner as a Photo")
            await context.bot.send_message(chat_id=q.from_user.id, text=_upload_prompt(arg), parse_mode=bot.ParseMode.HTML)
            return
        if action == "preview" and arg in ALL_SLOTS:
            await q.answer("Sending preview")
            await _preview_slot(context, q.from_user.id, arg)
            return
        if action == "previewall":
            await q.answer("Sending next rotation previews")
            for slot in CHANNEL_SLOTS:
                await _preview_slot(context, q.from_user.id, slot)
            return
        if action == "list" and arg in CHANNEL_SLOTS:
            await q.answer("Opening library")
            await _show_library(context, q.from_user.id, arg)
            return
        if action == "restore" and arg in WELCOME_SLOTS:
            ok = _restore_previous(arg)
            await q.answer("Previous banner restored ✅" if ok else "No previous approved banner available", show_alert=not ok)
            if ok:
                await _preview_slot(context, q.from_user.id, arg)
            return
        if action == "approve" and arg.isdigit():
            ok, detail = _approve_banner(int(arg), q.from_user.id)
            await q.answer("Approved & applied ✅" if ok else detail, show_alert=not ok)
            if ok:
                try:
                    await q.edit_message_caption(
                        caption="✅ <b>APPROVED & APPLIED</b>\n\nStored as a Telegram file_id. No deploy required.",
                        parse_mode=bot.ParseMode.HTML,
                        reply_markup=None,
                    )
                except Exception:
                    pass
                bot.logger.warning("BANNER_MANAGER_APPROVED banner_id=%s admin=%s", arg, q.from_user.id)
            return
        if action == "cancel" and arg.isdigit():
            ok = _cancel_pending(int(arg))
            await q.answer("Upload cancelled" if ok else "Already processed", show_alert=not ok)
            try:
                await q.edit_message_caption(caption="❌ Banner upload cancelled.", reply_markup=None)
            except Exception:
                pass
            return
        if action == "fallback" and arg.isdigit():
            ok = _set_fallback(int(arg))
            await q.answer("Fallback updated ⭐" if ok else "Unable to set fallback", show_alert=not ok)
            return
        if action == "disable" and arg.isdigit():
            ok, detail = _disable_banner(int(arg))
            await q.answer("Banner disabled" if ok else detail, show_alert=not ok)
            return
        if action in {"up", "down"} and arg.isdigit():
            ok = _move_banner(int(arg), action)
            await q.answer("Order updated" if ok else "Already at the end", show_alert=False)
            return

        await q.answer("Unsupported Banner Manager action", show_alert=True)

    bot.callback_handler = banner_callback
    return banner_callback


def _install_upload_handlers():
    global _previous_post_init
    _previous_post_init = bot.post_init

    async def banner_post_init(application):
        if _previous_post_init:
            await _previous_post_init(application)

        async def banner_manager_command(update, context):
            user = getattr(update, "effective_user", None)
            message = getattr(update, "effective_message", None)
            if not user or not message or not bot.is_admin(user.id):
                return
            await message.reply_text(_manager_text(), parse_mode=bot.ParseMode.HTML, reply_markup=_main_markup())

        async def quiz_banners_command(update, context):
            user = getattr(update, "effective_user", None)
            message = getattr(update, "effective_message", None)
            if not user or not message or not bot.is_admin(user.id):
                return
            await message.reply_text("🏆 <b>QUIZ CHANNEL BANNERS</b>", parse_mode=bot.ParseMode.HTML, reply_markup=_channel_markup())

        async def welcome_banners_command(update, context):
            user = getattr(update, "effective_user", None)
            message = getattr(update, "effective_message", None)
            if not user or not message or not bot.is_admin(user.id):
                return
            await message.reply_text("👋 <b>WELCOME BANNERS</b>", parse_mode=bot.ParseMode.HTML, reply_markup=_welcome_markup())

        async def photo_upload(update, context):
            user = getattr(update, "effective_user", None)
            message = getattr(update, "effective_message", None)
            if not user or not message or not bot.is_admin(user.id):
                return
            slot = _get_upload_state(user.id)
            if not slot:
                return
            photos = list(getattr(message, "photo", None) or [])
            if not photos:
                return
            photo = photos[-1]
            banner_id = _insert_pending(slot, str(photo.file_id), str(photo.file_unique_id), user.id)
            _clear_upload_state(user.id)
            try:
                await context.bot.send_photo(
                    chat_id=user.id,
                    photo=str(photo.file_id),
                    caption=(
                        "👁 <b>BANNER PREVIEW</b>\n\n"
                        f"Target: <b>{_slot_label(slot)}</b>\n\n"
                        "This preview was re-sent using the captured Telegram file_id.\n"
                        "Nothing is live until you approve it."
                    ),
                    parse_mode=bot.ParseMode.HTML,
                    reply_markup=bot.InlineKeyboardMarkup([[
                        bot.InlineKeyboardButton("✅ Approve & Apply", callback_data=f"bm:approve:{banner_id}"),
                        bot.InlineKeyboardButton("❌ Cancel", callback_data=f"bm:cancel:{banner_id}"),
                    ]]),
                )
                bot.logger.warning("BANNER_MANAGER_UPLOAD_CAPTURED slot=%s banner_id=%s unique=%s", slot, banner_id, photo.file_unique_id)
            except Exception:
                _set_upload_state(user.id, slot)
                bot.logger.exception("BANNER_MANAGER_PREVIEW_FAILED slot=%s banner_id=%s", slot, banner_id)
                await message.reply_text("❌ Telegram could not preview that image. Please send it again as a normal Photo.")

        application.add_handler(bot.CommandHandler("banner_manager", banner_manager_command), group=-92)
        application.add_handler(bot.CommandHandler("quiz_banners", quiz_banners_command), group=-92)
        application.add_handler(bot.CommandHandler("welcome_banners", welcome_banners_command), group=-92)
        application.add_handler(bot.MessageHandler(bot.filters.PHOTO, photo_upload), group=-92)
        bot.logger.warning("BANNER_MANAGER_HANDLERS active=on commands=/banner_manager,/quiz_banners,/welcome_banners photo_upload=admin_only")

    bot.post_init = banner_post_init


def install():
    global _installed
    if _installed:
        return bot.callback_handler
    _ensure_schema()
    _seed_locked_channel_creatives()
    _patch_admin_menu()
    handler = _install_callback()
    _install_upload_handlers()
    _installed = True
    bot.logger.warning(
        "BANNER_MANAGER active=on welcome_slots=2 channel_slots=4 storage=telegram_file_id "
        "approval_required=on rollback=on channel_rotation=round_robin_daily_IST "
        "locked_fallbacks=imported runtime_renderer=off"
    )
    return handler
