"""Additive V2 layer for the BETROXY Sunday Mega Quiz.

This module intentionally does NOT modify the locked Daily Quiz implementation.
It only adds new weekly-mega behavior on top of weekly_mega_quiz:
- persistent Sunday Mega Quiz entry point in OfficialBot and Business menus
- Amazon Pay Gift Voucher payout route for the weekly pool
- channel timing aligned with the established Daily Quiz cadence
- a completely separate five-slot Telegram-file-id banner manager for Mega Quiz
- image-backed Saturday preview, Sunday open/afternoon/last-call and results posts

Daily Quiz remains 10:00 / 16:00 / 19:00 / 21:05 IST with its existing banners,
prizes, delivery rules and code paths unchanged.
"""
from __future__ import annotations

import html
import json
from datetime import datetime, time as dtime, timedelta, timezone

import requests
from telegram.ext import ApplicationHandlerStop

import bot

IST = timezone(timedelta(hours=5, minutes=30))
AMAZON_OPERATOR = "GPAPGV"

MEGA_MEDIA = {
    "mega_preview": "Saturday 7:30 PM IST — Tomorrow Preview",
    "mega_open": "Sunday 10:00 AM IST — Mega Quiz Open",
    "mega_afternoon": "Sunday 4:00 PM IST — Still Open / Afternoon",
    "mega_last_chance": "Sunday 7:00 PM IST — Last Chance / 2 Hours Left",
    "mega_result": "Sunday 9:10 PM IST — Final Results / Winners",
}
MEGA_MEDIA_ORDER = tuple(MEGA_MEDIA)
DELIVERY_TO_ASSET = {
    "preview": "mega_preview",
    "open": "mega_open",
    "reminder": "mega_afternoon",
    "last_call": "mega_last_chance",
}

_weekly = None
_compact = None
_prepared = False
_installed = False
_previous_callback = None
_previous_post_init = None


def _ist_now():
    return datetime.now(timezone.utc).astimezone(IST)


def _amazon_denominations():
    try:
        row = _weekly.v97._catalogue_row(AMAZON_OPERATOR) or {}
        values = []
        for raw in _weekly.v97._parse_denominations(row.get("denominations")):
            value = float(raw)
            if value > 0 and value.is_integer():
                values.append(int(value))
        return str(row.get("brand_name") or "Amazon Pay Gift Voucher B2B"), sorted(set(values))
    except Exception:
        return "Amazon Pay Gift Voucher B2B", []


def _home_text(campaign):
    campaign = _weekly._refresh_status(campaign)
    day_label = campaign["campaign_date"].strftime("%d %b %Y")
    if campaign["status"] == "open":
        status = "🟢 <b>OPEN NOW</b> — closes 9:00 PM IST"
    elif campaign["status"] == "closed":
        status = "🔒 <b>Closed</b> — final results at 9:10 PM IST"
    else:
        status = f"⏳ Opens <b>Sunday {day_label} at 10:00 AM IST</b>"
    return (
        "🔥 <b>BETROXY SUNDAY MEGA QUIZ</b>\n\n"
        f"{status}\n\n"
        "🎁 <b>₹5,000 Amazon Pay Gift Voucher Prize Pool</b>\n"
        "🥇 1st — ₹2,500\n"
        "🥈 2nd — ₹1,500\n"
        "🥉 3rd — ₹1,000\n\n"
        "10 questions • 30 seconds each • one attempt\n"
        "Accuracy first; hard-question accuracy and speed break ties.\n"
        "💯 Free entry — no deposit or wager required."
    )


def _promo_text(kind, campaign):
    day = campaign["campaign_date"].strftime("%d %b")
    common = (
        "\n\n🎁 <b>₹5,000 Amazon Pay Gift Voucher Prize Pool</b>\n"
        "🥇 ₹2,500 • 🥈 ₹1,500 • 🥉 ₹1,000\n"
        "10 questions • 30 seconds each • free entry"
    )
    if kind == "preview":
        return (
            f"🔥 <b>TOMORROW: BETROXY SUNDAY MEGA QUIZ</b>\n\n"
            f"Starts Sunday {day} at <b>10:00 AM IST</b>.{common}"
        )
    if kind == "open":
        return (
            "🔥 <b>SUNDAY MEGA QUIZ IS OPEN!</b>\n\n"
            "Play anytime before <b>9:00 PM IST</b>." + common
        )
    if kind == "reminder":
        return (
            "🏆 <b>SUNDAY MEGA QUIZ IS STILL OPEN</b>\n\n"
            "The leaderboard is moving. Complete your 10 questions before <b>9:00 PM IST</b>." + common
        )
    return (
        "⏰ <b>ONLY 2 HOURS LEFT — SUNDAY MEGA QUIZ</b>\n\n"
        "Final public reminder. Entries close at <b>9:00 PM IST</b>." + common
    )


def _result_text(campaign, rows):
    lines = ["🔥 <b>BETROXY SUNDAY MEGA QUIZ — FINAL RESULTS</b>", ""]
    if not rows:
        lines.append("No eligible finishers qualified for the prize positions this week.")
    else:
        medals = ["🥇", "🥈", "🥉"]
        for i, row in enumerate(rows[:3]):
            name = str(row.get("telegram_username") or f"Player {str(row.get('telegram_user_id') or '')[-4:]}")
            lines.append(
                f"{medals[i]} <b>{html.escape(name)}</b> — "
                f"{int(row.get('correct_count') or 0)}/{_weekly.QUESTION_COUNT} — <b>₹{_weekly.PRIZES[i]:,}</b>"
            )
        lines += ["", "Final ranking: accuracy → hard-question accuracy → total answer time."]
    lines += ["", "🎁 Total prize pool: <b>₹5,000 in Amazon Pay Gift Vouchers</b>"]
    return "\n".join(lines)


def _admin_reward_text(campaign, rows, reminder_no=0):
    lead = (
        "🚨 <b>ACTION REQUIRED — MEGA QUIZ PAYOUT</b>"
        if reminder_no == 0
        else "⏰ <b>REMINDER — MEGA QUIZ PAYOUT STILL PENDING</b>"
    )
    lines = [
        lead,
        "",
        f"Date: <b>{campaign['campaign_date']}</b>",
        f"Eligible payout: <b>₹{sum(_weekly.PRIZES[:len(rows)]):,}</b>",
        "",
    ]
    medals = ["🥇", "🥈", "🥉"]
    for i, row in enumerate(rows[:3]):
        name = str(row.get("telegram_username") or f"Player {str(row.get('telegram_user_id') or '')[-4:]}")
        lines.append(f"{medals[i]} {html.escape(name)} — ₹{_weekly.PRIZES[i]:,}")
    lines += [
        "",
        "GiftPort route: <b>Amazon Pay Gift Voucher B2B (GPAPGV)</b>",
        "₹2,500 is delivered as 5×₹500; ₹1,500 as 3×₹500; ₹1,000 as 2×₹500 when fixed denominations apply.",
        "Payout remains manual until you approve below.",
    ]
    return "\n".join(lines)


def prepare(weekly_module):
    """Set only weekly-mega constants/functions before weekly_module.install()."""
    global _weekly, _prepared
    _weekly = weekly_module
    if _prepared:
        return

    # Weekly only. Daily Quiz constants are not touched.
    _weekly.REWARD_OPERATOR = AMAZON_OPERATOR
    _weekly.PROMO_PREVIEW_AT = dtime(19, 30)   # Saturday
    _weekly.PROMO_REMINDER_AT = dtime(16, 0)   # Sunday, aligned with Daily Quiz afternoon slot
    _weekly.PROMO_LAST_CALL_AT = dtime(19, 0)  # Sunday, aligned with Daily Quiz last-call slot
    _weekly._home_text = _home_text
    _weekly._promo_text = _promo_text
    _weekly._result_text = _result_text
    _weekly._admin_reward_text = _admin_reward_text
    _prepared = True


def _ensure_media_schema():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS mega_quiz_media_assets (
                    asset_key TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    approved_file_id TEXT,
                    approved_file_unique_id TEXT,
                    pending_file_id TEXT,
                    pending_file_unique_id TEXT,
                    status TEXT NOT NULL DEFAULT 'needs_upload',
                    approved_by BIGINT,
                    approved_at TIMESTAMPTZ,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS mega_quiz_media_upload_state (
                    admin_id BIGINT PRIMARY KEY,
                    asset_key TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            for key, label in MEGA_MEDIA.items():
                cur.execute("""
                    INSERT INTO mega_quiz_media_assets(asset_key,label,status)
                    VALUES (%s,%s,'needs_upload')
                    ON CONFLICT(asset_key) DO UPDATE SET label=EXCLUDED.label,updated_at=NOW()
                """, (key, label))
        conn.commit()


def _media_row(key):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM mega_quiz_media_assets WHERE asset_key=%s", (str(key),))
            return cur.fetchone() or {}


def _approved_file_id(key):
    row = _media_row(key)
    if str(row.get("status") or "") != "approved":
        return None
    value = str(row.get("approved_file_id") or "").strip()
    return value or None


def _set_upload_state(admin_id, key):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO mega_quiz_media_upload_state(admin_id,asset_key,updated_at)
                VALUES (%s,%s,NOW())
                ON CONFLICT(admin_id) DO UPDATE SET asset_key=EXCLUDED.asset_key,updated_at=NOW()
            """, (int(admin_id), str(key)))
        conn.commit()


def _get_upload_state(admin_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT asset_key FROM mega_quiz_media_upload_state WHERE admin_id=%s", (int(admin_id),))
            row = cur.fetchone() or {}
    key = str(row.get("asset_key") or "")
    return key if key in MEGA_MEDIA else None


def _clear_upload_state(admin_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM mega_quiz_media_upload_state WHERE admin_id=%s", (int(admin_id),))
        conn.commit()


def _save_pending(key, file_id, unique_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE mega_quiz_media_assets
                SET pending_file_id=%s,pending_file_unique_id=%s,status='pending',updated_at=NOW()
                WHERE asset_key=%s
            """, (str(file_id), str(unique_id or "") or None, str(key)))
        conn.commit()


def _approve_media(key, admin_id):
    row = _media_row(key)
    pending = str(row.get("pending_file_id") or "").strip()
    if not pending:
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


def _first_missing():
    for key in MEGA_MEDIA_ORDER:
        if not _approved_file_id(key):
            return key
    return None


def _next_after(key):
    try:
        idx = MEGA_MEDIA_ORDER.index(key)
    except ValueError:
        return _first_missing()
    for candidate in MEGA_MEDIA_ORDER[idx + 1:]:
        if not _approved_file_id(candidate):
            return candidate
    for candidate in MEGA_MEDIA_ORDER[:idx + 1]:
        if not _approved_file_id(candidate):
            return candidate
    return None


def _status_text():
    lines = [
        "🖼 <b>SUNDAY MEGA QUIZ BANNERS</b>",
        "",
        "These 5 images are completely separate from the locked Daily Quiz banners.",
        "Upload as normal Telegram <b>Photos</b>, not documents.",
        "",
    ]
    for key in MEGA_MEDIA_ORDER:
        row = _media_row(key)
        status = "✅ Ready" if _approved_file_id(key) else ("🟡 Pending approval" if str(row.get("status") or "") == "pending" else "⬜ Upload needed")
        lines.append(f"{status} — <b>{MEGA_MEDIA[key]}</b>")
    lines += [
        "",
        "Sunday schedule: <b>10:00 AM / 4:00 PM / 7:00 PM / 9:10 PM IST</b>",
        "Saturday preview: <b>7:30 PM IST</b>",
        "Daily Quiz remains unchanged at <b>10:00 AM / 4:00 PM / 7:00 PM / 9:05 PM IST</b>.",
    ]
    return "\n".join(lines)


def _media_menu():
    rows = [[bot.InlineKeyboardButton("📦 Start Guided 5-Banner Upload", callback_data="mega_media:start")]]
    for key in MEGA_MEDIA_ORDER:
        ready = "✅" if _approved_file_id(key) else "📤"
        rows.append([bot.InlineKeyboardButton(f"{ready} {MEGA_MEDIA[key]}", callback_data=f"mega_media:slot:{key}")])
    rows += [
        [bot.InlineKeyboardButton("👁 Preview All Uploaded", callback_data="mega_media:previewall")],
        [bot.InlineKeyboardButton("🔄 Refresh Status", callback_data="mega_media:home")],
    ]
    return bot.InlineKeyboardMarkup(rows)


def _approval_markup(key):
    return bot.InlineKeyboardMarkup([
        [
            bot.InlineKeyboardButton("✅ Approve & Continue", callback_data=f"mega_media:approve:{key}"),
            bot.InlineKeyboardButton("🔁 Upload Again", callback_data=f"mega_media:retry:{key}"),
        ],
        [bot.InlineKeyboardButton("🏠 Mega Banner Manager", callback_data="mega_media:home")],
    ])


def _upload_prompt(key):
    pos = MEGA_MEDIA_ORDER.index(key) + 1
    return (
        f"📤 <b>MEGA QUIZ BANNER {pos}/5</b>\n\n"
        f"Upload now for:\n<b>{MEGA_MEDIA[key]}</b>\n\n"
        "Send the image as a normal Telegram <b>Photo</b>.\n"
        "Recommended: the same portrait format/theme used by your Daily Quiz channel banners.\n\n"
        "The existing approved image stays live until you approve this replacement."
    )


def _api_send_photo(chat_id, file_id, caption, rows=None):
    data = {
        "chat_id": str(chat_id),
        "photo": str(file_id),
        "caption": str(caption),
        "parse_mode": "HTML",
    }
    if rows:
        data["reply_markup"] = json.dumps({"inline_keyboard": rows}, separators=(",", ":"))
    try:
        response = requests.post(f"{_weekly.v110.TG_API}/sendPhoto", data=data, timeout=25)
        payload = response.json() if response.content else {}
        return bool(response.ok and payload.get("ok")), payload
    except Exception as exc:
        return False, {"description": str(exc)}


def _media_channel_post(campaign, delivery_type, text):
    if _weekly._delivery_exists(campaign["id"], _weekly.CHANNEL, delivery_type):
        return True
    rows = [[{"text": "🔥 Open Mega Quiz", "url": _weekly.BOT_DEEPLINK}]]
    asset_key = DELIVERY_TO_ASSET.get(str(delivery_type))
    file_id = _approved_file_id(asset_key) if asset_key else None
    if file_id:
        ok, payload = _api_send_photo(_weekly.CHANNEL, file_id, text, rows)
        mode = "approved_telegram_file_id"
    else:
        ok, payload = _weekly.quiz._tg_send_text(_weekly.CHANNEL, text, rows)
        mode = "text_fallback"
    if ok:
        _weekly._mark_delivery(campaign["id"], _weekly.CHANNEL, delivery_type)
    bot.logger.warning(
        "MEGA_MEDIA_CHANNEL_POST type=%s asset=%s mode=%s sent=%s campaign=%s",
        delivery_type, asset_key, mode, ok, campaign["id"],
    )
    return bool(ok)


def _media_announce_due_results():
    now = datetime.now(timezone.utc)
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM mega_quiz_campaigns WHERE result_at<=%s ORDER BY campaign_date DESC LIMIT 4", (now,))
            campaigns = cur.fetchall() or []
    for campaign in campaigns:
        if _weekly._delivery_exists(campaign["id"], _weekly.CHANNEL, "final_result"):
            continue
        rows = _weekly._final_rows(campaign["id"])
        _weekly._stage_awards(campaign, rows)
        keyboard = [[{"text": "🔥 Next Mega Quiz", "url": _weekly.BOT_DEEPLINK}]]
        file_id = _approved_file_id("mega_result")
        if file_id:
            ok, _ = _api_send_photo(_weekly.CHANNEL, file_id, _weekly._result_text(campaign, rows), keyboard)
            mode = "approved_telegram_file_id"
        else:
            ok, _ = _weekly.quiz._tg_send_text(_weekly.CHANNEL, _weekly._result_text(campaign, rows), keyboard)
            mode = "text_fallback"
        if ok:
            _weekly._mark_delivery(campaign["id"], _weekly.CHANNEL, "final_result")
            _weekly._send_admin_reward_alert(campaign, rows, 0)
        bot.logger.warning(
            "MEGA_MEDIA_FINAL_RESULT mode=%s sent=%s campaign=%s winners=%s",
            mode, ok, campaign["id"], len(rows),
        )


def _add_mega_button(markup, business=False):
    rows = [list(r) for r in (getattr(markup, "inline_keyboard", None) or [])]
    for row in rows:
        if any("Sunday Mega Quiz" in str(getattr(button, "text", "")) for button in row):
            return bot.InlineKeyboardMarkup(rows)
    style = None if business else "primary"
    api_kwargs = {"style": style} if style else None
    button = bot.InlineKeyboardButton(
        "🔥 Sunday Mega Quiz ₹5,000",
        url=_weekly.BOT_DEEPLINK,
        api_kwargs=api_kwargs,
    )
    insert_at = len(rows)
    for idx, row in enumerate(rows):
        if any("Daily Quiz" in str(getattr(item, "text", "")) for item in row):
            insert_at = idx + 1
            break
    rows.insert(insert_at, [button])
    return bot.InlineKeyboardMarkup(rows)


def _patch_customer_menus():
    old_public = _compact.compact_public_menu
    if not getattr(old_public, "_mega_v2_patched", False):
        def public_with_mega(user_id=None):
            return _add_mega_button(old_public(user_id), business=False)
        public_with_mega._mega_v2_patched = True
        _compact.compact_public_menu = public_with_mega
        bot.public_menu = public_with_mega
        try:
            _compact.v96.v96_public_menu = public_with_mega
            _compact.v53.v53_public_menu = public_with_mega
        except Exception:
            pass

    # Business first/general replies are patched after unified_customer_menu.install(),
    # without changing any of the original six destinations.
    try:
        v75 = _weekly.v110.v83.v75
        biz51 = v75.biz51
        old_payload = v75._business_reply_payload
        if not getattr(old_payload, "_mega_v2_patched", False):
            def payload_with_mega(intent, first_reply=False):
                text, keyboard, stage = old_payload(intent, first_reply=first_reply)
                if first_reply or intent in {"greeting", "general"}:
                    keyboard = _add_mega_button(keyboard, business=True)
                return text, keyboard, stage
            payload_with_mega._mega_v2_patched = True
            v75._business_reply_payload = payload_with_mega
            biz51._reply_payload = payload_with_mega

        old_business_menu = v75._business_menu
        if not getattr(old_business_menu, "_mega_v2_patched", False):
            def business_menu_with_mega(styled=True):
                return _add_mega_button(old_business_menu(styled), business=True)
            business_menu_with_mega._mega_v2_patched = True
            v75._business_menu = business_menu_with_mega
    except Exception:
        bot.logger.exception("MEGA_V2_BUSINESS_MENU_PATCH_FAILED")


def _install_media_callback():
    global _previous_callback
    _previous_callback = bot.callback_handler

    async def media_callback(update, context):
        q = getattr(update, "callback_query", None)
        data = str(getattr(q, "data", "") or "") if q else ""
        if not q or not data.startswith("mega_media:"):
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
                await q.edit_message_text(_status_text(), parse_mode=bot.ParseMode.HTML, reply_markup=_media_menu())
            except Exception:
                await q.message.reply_text(_status_text(), parse_mode=bot.ParseMode.HTML, reply_markup=_media_menu())
            return

        if action == "start":
            target = _first_missing() or MEGA_MEDIA_ORDER[0]
            _set_upload_state(q.from_user.id, target)
            await q.answer("Send banner 1 as a Photo")
            await context.bot.send_message(chat_id=q.from_user.id, text=_upload_prompt(target), parse_mode=bot.ParseMode.HTML)
            return

        if action == "slot" and key in MEGA_MEDIA:
            _set_upload_state(q.from_user.id, key)
            await q.answer("Send the banner as a Photo")
            await context.bot.send_message(chat_id=q.from_user.id, text=_upload_prompt(key), parse_mode=bot.ParseMode.HTML)
            return

        if action == "retry" and key in MEGA_MEDIA:
            _reset_pending(key)
            _set_upload_state(q.from_user.id, key)
            await q.answer("Send the replacement Photo")
            await context.bot.send_message(chat_id=q.from_user.id, text=_upload_prompt(key), parse_mode=bot.ParseMode.HTML)
            return

        if action == "approve" and key in MEGA_MEDIA:
            ok = _approve_media(key, q.from_user.id)
            await q.answer("Approved ✅" if ok else "No pending upload", show_alert=not ok)
            if not ok:
                return
            next_key = _next_after(key)
            if next_key:
                _set_upload_state(q.from_user.id, next_key)
                await context.bot.send_message(
                    chat_id=q.from_user.id,
                    text=f"✅ <b>{MEGA_MEDIA[key]}</b> approved.\n\nNext:\n" + _upload_prompt(next_key),
                    parse_mode=bot.ParseMode.HTML,
                )
            else:
                _clear_upload_state(q.from_user.id)
                await context.bot.send_message(
                    chat_id=q.from_user.id,
                    text="✅ <b>ALL 5 MEGA QUIZ BANNERS ARE READY</b>\n\nThey will be reused by Telegram file_id at the aligned schedule. Daily Quiz banners were not changed.",
                    parse_mode=bot.ParseMode.HTML,
                    reply_markup=_media_menu(),
                )
            return

        if action == "previewall":
            await q.answer("Sending previews")
            for slot in MEGA_MEDIA_ORDER:
                file_id = _approved_file_id(slot)
                if file_id:
                    await context.bot.send_photo(
                        chat_id=q.from_user.id,
                        photo=file_id,
                        caption=f"👁 <b>{MEGA_MEDIA[slot]}</b>",
                        parse_mode=bot.ParseMode.HTML,
                    )
            return

        await q.answer("Unsupported Mega banner action", show_alert=True)

    bot.callback_handler = media_callback


def _install_media_handlers():
    global _previous_post_init
    _previous_post_init = bot.post_init

    async def mega_media_post_init(application):
        if _previous_post_init:
            await _previous_post_init(application)

        async def mega_banners_command(update, context):
            user = getattr(update, "effective_user", None)
            msg = getattr(update, "effective_message", None)
            if not user or not msg or not bot.is_admin(user.id):
                return
            await msg.reply_text(_status_text(), parse_mode=bot.ParseMode.HTML, reply_markup=_media_menu())

        async def photo_upload(update, context):
            user = getattr(update, "effective_user", None)
            msg = getattr(update, "effective_message", None)
            if not user or not msg or not bot.is_admin(user.id):
                return
            key = _get_upload_state(user.id)
            if not key:
                return
            photos = list(getattr(msg, "photo", None) or [])
            if not photos:
                return
            photo = photos[-1]
            _save_pending(key, photo.file_id, photo.file_unique_id)
            _clear_upload_state(user.id)
            await context.bot.send_photo(
                chat_id=user.id,
                photo=photo.file_id,
                caption=(
                    "👁 <b>MEGA QUIZ BANNER PREVIEW</b>\n\n"
                    f"Target: <b>{MEGA_MEDIA[key]}</b>\n\n"
                    "Nothing changes until you approve this image."
                ),
                parse_mode=bot.ParseMode.HTML,
                reply_markup=_approval_markup(key),
            )
            bot.logger.warning("MEGA_MEDIA_UPLOAD_CAPTURED asset=%s admin=%s unique=%s", key, user.id, photo.file_unique_id)
            raise ApplicationHandlerStop

        application.add_handler(bot.CommandHandler("mega_banners", mega_banners_command), group=-89)
        application.add_handler(bot.MessageHandler(bot.filters.PHOTO, photo_upload), group=-89)
        bot.logger.warning("MEGA_MEDIA_HANDLERS active=on command=/mega_banners slots=5 photo_upload=admin_only")

    bot.post_init = mega_media_post_init


def install(weekly_module, compact_menu):
    global _weekly, _compact, _installed
    _weekly = weekly_module
    _compact = compact_menu
    if _installed:
        return
    if not _prepared:
        prepare(weekly_module)

    _ensure_media_schema()
    _patch_customer_menus()
    _install_media_callback()
    _install_media_handlers()

    # Worker resolves these names at runtime, so replacing them here is isolated
    # to the weekly-mega module and leaves Daily Quiz posting untouched.
    _weekly._channel_post = _media_channel_post
    _weekly._announce_due_results = _media_announce_due_results

    brand, denoms = _amazon_denominations()
    split_ok = 500 in denoms
    _installed = True
    bot.logger.warning(
        "MEGA_V2_OVERLAY active=on locked_daily_unchanged=on menu_entry=on media_slots=5 "
        "preview=Sat19:30 open=Sun10:00 afternoon=Sun16:00 last=Sun19:00 result=Sun21:10_IST "
        "reward_operator=%s reward_brand=%s amazon_500=%s payout_plan=2500:5x500,1500:3x500,1000:2x500 "
        "private_campaign_dm=off upload_command=/mega_banners",
        AMAZON_OPERATOR, brand, split_ok,
    )
