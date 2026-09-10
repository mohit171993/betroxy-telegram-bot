import html
import threading
import time
from datetime import datetime, timedelta, timezone

import bot
import v84_autopilot_safety_fix as v84

from telegram.ext import ApplicationHandlerStop

# ============================================================
# V85 - SILENT SMART TELEGRAM BUSINESS INBOX
# ============================================================
# Customer messages are always stored, but the admin is no longer interrupted
# for every message. The admin gets:
# - one card for a truly new customer
# - a red attention card when human intervention is likely needed
# - one card when a resolved customer returns
# - one daily inbox summary
# Everything else is stored silently and visible inside Business Inbox.

v83 = v84.v83
v63 = v84.v63
biz51 = v83.biz51
v49 = biz51.v49

_previous_callback_handler = bot.callback_handler
_original_enquiry_detail_text = v49._enquiry_detail_text

HUMAN_TERMS = (
    "human", "agent", "real person", "speak to", "talk to", "customer care",
    "manager", "call me", "please call", "need a call", "baat kar", "baat karni",
    "kisi se baat", "support executive",
)
PROBLEM_TERMS = (
    "pending", "failed", "not received", "not credited", "not showing",
    "deducted", "debited", "stuck", "declined", "missing", "wrong amount",
    "money gone", "money deducted", "still waiting", "not come", "not came",
)
URGENT_TERMS = (
    "urgent", "complaint", "fraud", "scam", "cheated", "legal", "police",
    "cyber", "consumer court", "report you", "not resolved", "very bad",
)
SIMPLE_GREETING_TERMS = {
    "hi", "hello", "hey", "hii", "hiii", "hello sir", "hi sir", "good morning",
    "good afternoon", "good evening", "namaste", "namaskar",
}


def _ensure_v85_schema():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            for sql in (
                "ALTER TABLE telegram_business_enquiries ADD COLUMN IF NOT EXISTS admin_notified_at TIMESTAMPTZ",
                "ALTER TABLE telegram_business_enquiries ADD COLUMN IF NOT EXISTS last_admin_alert_at TIMESTAMPTZ",
                "ALTER TABLE telegram_business_enquiries ADD COLUMN IF NOT EXISTS admin_alert_count INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE telegram_business_enquiries ADD COLUMN IF NOT EXISTS silent_unread_count INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE telegram_business_enquiries ADD COLUMN IF NOT EXISTS admin_priority TEXT NOT NULL DEFAULT 'auto'",
                "ALTER TABLE telegram_business_enquiries ADD COLUMN IF NOT EXISTS attention_reason TEXT",
                "ALTER TABLE telegram_business_enquiries ADD COLUMN IF NOT EXISTS last_escalation_key TEXT",
                "ALTER TABLE telegram_business_enquiries ADD COLUMN IF NOT EXISTS last_customer_message_text TEXT",
                "ALTER TABLE telegram_business_enquiries ADD COLUMN IF NOT EXISTS last_customer_message_at TIMESTAMPTZ",
                "ALTER TABLE telegram_business_enquiries ADD COLUMN IF NOT EXISTS last_admin_viewed_at TIMESTAMPTZ",
            ):
                cur.execute(sql)

            # Historical rows have already been surfaced under the old noisy system.
            # Mark them notified so a redeploy does not create a flood of "new" cards.
            cur.execute(
                """
                UPDATE telegram_business_enquiries
                SET admin_notified_at=COALESCE(admin_notified_at, first_message_at),
                    last_admin_alert_at=COALESCE(last_admin_alert_at, first_message_at)
                WHERE admin_notified_at IS NULL
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS business_admin_digest_log (
                    digest_date DATE PRIMARY KEY,
                    sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    summary TEXT
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_business_attention
                ON telegram_business_enquiries(admin_priority, status, last_customer_message_at DESC)
                """
            )
        conn.commit()


def _enable_smart_reply_without_reset():
    # Keep smart replies ON, but unlike the old startup helper do NOT clear
    # auto_ack_sent_at/auto_reply_count for open conversations on every deploy.
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE telegram_business_settings
                SET auto_ack_enabled=TRUE, updated_at=NOW()
                WHERE id=1
                """
            )
        conn.commit()
    bot.logger.warning("V85_BUSINESS_SMART_REPLY enabled_without_open_chat_reset=on")


def _existing_enquiry(connection_id, chat_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM telegram_business_enquiries
                WHERE connection_id=%s AND customer_chat_id=%s
                LIMIT 1
                """,
                (str(connection_id), int(chat_id)),
            )
            return cur.fetchone()


def _clean_text(text):
    return " ".join(str(text or "").lower().strip().split())


def _attention_decision(text, intent, message_type, auto_reply_failed=False):
    t = _clean_text(text)

    if auto_reply_failed:
        return True, "Bot could not complete the automatic reply", "auto_reply_failed"

    if any(term in t for term in HUMAN_TERMS):
        return True, "Customer requested a human/agent", "human_request"

    if any(term in t for term in URGENT_TERMS):
        return True, "Complaint / urgent wording detected", "urgent_or_complaint"

    if intent in {"deposit", "withdrawal"} and any(term in t for term in PROBLEM_TERMS):
        label = "Deposit problem" if intent == "deposit" else "Withdrawal problem"
        return True, label, f"{intent}_problem"

    if message_type not in {"text", "message"}:
        return True, f"Customer sent {message_type} for review", f"attachment_{message_type}"

    # A meaningful message that the rule engine cannot classify should be visible
    # to a human, while ordinary greetings stay silent after the first alert.
    if intent == "general" and t and t not in SIMPLE_GREETING_TERMS and len(t) >= 18:
        return True, "Unrecognised customer request", "unrecognised_request"

    return False, None, None


def _update_silent_state(enquiry_id, inbound_preview, priority, reason=None, escalation_key=None):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE telegram_business_enquiries
                SET last_customer_message_text=%s,
                    last_customer_message_at=NOW(),
                    silent_unread_count=COALESCE(silent_unread_count,0)+1,
                    admin_priority=CASE
                        WHEN admin_priority='attention' THEN 'attention'
                        ELSE %s
                    END,
                    attention_reason=CASE
                        WHEN %s='attention' THEN %s
                        ELSE attention_reason
                    END,
                    last_escalation_key=CASE
                        WHEN %s='attention' THEN %s
                        ELSE last_escalation_key
                    END
                WHERE id=%s
                RETURNING *
                """,
                (
                    str(inbound_preview)[:1500], priority, priority, reason,
                    priority, escalation_key, int(enquiry_id),
                ),
            )
            row = cur.fetchone()
        conn.commit()
    return row


def _mark_admin_alert(enquiry_id, priority=None, reason=None, escalation_key=None):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE telegram_business_enquiries
                SET admin_notified_at=COALESCE(admin_notified_at,NOW()),
                    last_admin_alert_at=NOW(),
                    admin_alert_count=COALESCE(admin_alert_count,0)+1,
                    admin_priority=COALESCE(%s,admin_priority),
                    attention_reason=COALESCE(%s,attention_reason),
                    last_escalation_key=COALESCE(%s,last_escalation_key)
                WHERE id=%s
                """,
                (priority, reason, escalation_key, int(enquiry_id)),
            )
        conn.commit()


def _should_repeat_attention(previous, escalation_key):
    if not previous:
        return True
    if str(previous.get("last_escalation_key") or "") != str(escalation_key or ""):
        return True
    last = previous.get("last_admin_alert_at")
    if not last:
        return True
    try:
        return datetime.now(timezone.utc) - last >= timedelta(hours=1)
    except Exception:
        return True


def _admin_alert_keyboard(enquiry_id):
    return bot.InlineKeyboardMarkup([
        [bot.InlineKeyboardButton("👁 Open Enquiry", callback_data=f"biz_view:{int(enquiry_id)}")],
        [
            bot.InlineKeyboardButton("↩️ Reply", callback_data=f"biz_reply:{int(enquiry_id)}"),
            bot.InlineKeyboardButton("✅ Resolve", callback_data=f"biz_resolve:{int(enquiry_id)}"),
        ],
    ])


async def _send_new_lead_alert(context, enquiry, intent, inbound_preview, auto_replied):
    await context.bot.send_message(
        chat_id=bot.ADMIN_ID,
        text=(
            "🟢 <b>NEW BUSINESS LEAD</b>\n\n"
            f"From: <b>{html.escape(v49._customer_name(enquiry))}</b>\n"
            f"Intent: <b>{html.escape(str(intent).replace('_',' ').title())}</b>\n"
            f"Bot: <b>{'Handling automatically ✅' if auto_replied else 'Conversation stored'}</b>\n"
            f"Message: {html.escape(str(inbound_preview)[:900])}\n\n"
            "Further normal messages from this customer will stay silent. "
            "I will alert you again only if attention is needed."
        ),
        parse_mode=bot.ParseMode.HTML,
        reply_markup=_admin_alert_keyboard(enquiry["id"]),
        disable_web_page_preview=True,
    )


async def _send_attention_alert(context, enquiry, intent, inbound_preview, reason, reopened=False):
    unread = int(enquiry.get("silent_unread_count") or 0)
    title = "🔴 <b>BUSINESS CHAT NEEDS ATTENTION</b>"
    if reopened and not reason:
        title = "🟠 <b>RESOLVED CUSTOMER RETURNED</b>"
    await context.bot.send_message(
        chat_id=bot.ADMIN_ID,
        text=(
            f"{title}\n\n"
            f"From: <b>{html.escape(v49._customer_name(enquiry))}</b>\n"
            f"Reason: <b>{html.escape(reason or 'Customer reopened the conversation')}</b>\n"
            f"Intent: <b>{html.escape(str(intent).replace('_',' ').title())}</b>\n"
            f"Unread in inbox: <b>{unread}</b>\n"
            f"Last message: {html.escape(str(inbound_preview)[:900])}"
        ),
        parse_mode=bot.ParseMode.HTML,
        reply_markup=_admin_alert_keyboard(enquiry["id"]),
        disable_web_page_preview=True,
    )


async def v85_business_message_update(update, context):
    message = update.business_message
    if not message:
        return

    connection_id = getattr(message, "business_connection_id", None)
    if not connection_id:
        raise ApplicationHandlerStop

    conn = await v49._ensure_connection_from_message(context, connection_id)
    owner_user_id = (conn or {}).get("owner_user_id")
    sender = getattr(message, "from_user", None)

    if owner_user_id and sender and int(sender.id) == int(owner_user_id):
        raise ApplicationHandlerStop
    if getattr(message, "sender_business_bot", None):
        raise ApplicationHandlerStop

    previous = _existing_enquiry(connection_id, message.chat_id)
    was_resolved = bool(previous and previous.get("status") == "resolved")
    inbound_preview = v49._message_preview(message)
    message_type = v49._message_type(message)
    message_text = getattr(message, "text", None) or getattr(message, "caption", None) or ""

    try:
        enquiry = v49._upsert_incoming_enquiry(connection_id, message)
    except Exception:
        bot.logger.exception("V85_BUSINESS_ENQUIRY_SAVE_FAILED")
        raise ApplicationHandlerStop

    intent = biz51._detect_intent(message_text)
    enquiry = biz51._update_lead_state(enquiry["id"], intent=intent, stage=None, auto_replied=False) or enquiry

    auto_replied = False
    auto_reply_failed = False
    settings = v49._business_settings()
    if settings.get("auto_ack_enabled") and biz51._auto_reply_allowed(enquiry):
        should_reply = (not enquiry.get("auto_ack_sent_at")) or intent not in {"general", "greeting"}
        if should_reply:
            try:
                enquiry = await biz51._send_smart_reply(context, enquiry, intent)
                auto_replied = True
            except Exception:
                auto_reply_failed = True
                bot.logger.exception("V85_BUSINESS_SMART_AUTO_REPLY_FAILED")

    needs_attention, reason, escalation_key = _attention_decision(
        message_text, intent, message_type, auto_reply_failed=auto_reply_failed
    )
    if was_resolved and not needs_attention:
        reason = "Customer returned after enquiry was resolved"
        escalation_key = "reopened"

    priority = "attention" if needs_attention else "auto"
    enquiry = _update_silent_state(
        enquiry["id"], inbound_preview, priority,
        reason=reason if needs_attention else None,
        escalation_key=escalation_key if needs_attention else None,
    ) or enquiry

    notify_new = previous is None
    notify_reopened = was_resolved
    notify_attention = needs_attention and _should_repeat_attention(previous, escalation_key)

    try:
        if notify_attention:
            await _send_attention_alert(context, enquiry, intent, inbound_preview, reason, reopened=was_resolved)
            _mark_admin_alert(enquiry["id"], "attention", reason, escalation_key)
            bot.logger.warning("V85_BUSINESS_ADMIN_ALERT attention enquiry_id=%s reason=%s", enquiry["id"], escalation_key)
        elif notify_reopened:
            await _send_attention_alert(context, enquiry, intent, inbound_preview, reason, reopened=True)
            _mark_admin_alert(enquiry["id"], "attention", reason, "reopened")
            bot.logger.warning("V85_BUSINESS_ADMIN_ALERT reopened enquiry_id=%s", enquiry["id"])
        elif notify_new:
            await _send_new_lead_alert(context, enquiry, intent, inbound_preview, auto_replied)
            _mark_admin_alert(enquiry["id"], "auto", None, None)
            bot.logger.warning("V85_BUSINESS_ADMIN_ALERT new_lead enquiry_id=%s", enquiry["id"])
        else:
            bot.logger.info(
                "V85_BUSINESS_SILENT_STORED enquiry_id=%s unread=%s intent=%s",
                enquiry["id"], int(enquiry.get("silent_unread_count") or 0), intent,
            )
    except Exception:
        bot.logger.exception("V85_BUSINESS_ADMIN_ALERT_FAILED")

    raise ApplicationHandlerStop


# ============================================================
# ADMIN INBOX UI
# ============================================================

def _silent_counts():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE status='open') AS open_count,
                    COUNT(*) FILTER (WHERE status='resolved') AS resolved_count,
                    COUNT(*) FILTER (WHERE status='open' AND admin_priority='attention') AS attention_count,
                    COALESCE(SUM(silent_unread_count) FILTER (WHERE status='open'),0) AS unread_count,
                    COUNT(*) FILTER (
                        WHERE status='open' AND admin_priority<>'attention'
                          AND COALESCE(auto_reply_count,0)>0
                    ) AS auto_handled
                FROM telegram_business_enquiries
                """
            )
            return cur.fetchone() or {}


def silent_business_home_keyboard():
    c = _silent_counts()
    settings = v49._business_settings()
    auto = "ON ✅" if settings.get("auto_ack_enabled") else "OFF"
    return bot.InlineKeyboardMarkup([
        [bot.InlineKeyboardButton(f"🔴 Needs Attention ({int(c.get('attention_count') or 0)})", callback_data="biz_silent_attention:0")],
        [bot.InlineKeyboardButton(f"📥 Unread Messages ({int(c.get('unread_count') or 0)})", callback_data="biz_silent_unread:0")],
        [
            bot.InlineKeyboardButton(f"🟡 Open ({int(c.get('open_count') or 0)})", callback_data="biz_list:open:0"),
            bot.InlineKeyboardButton(f"✅ Resolved ({int(c.get('resolved_count') or 0)})", callback_data="biz_list:resolved:0"),
        ],
        [bot.InlineKeyboardButton(f"🎯 Smart Auto Reply: {auto}", callback_data="biz_toggle_auto_ack")],
        [
            bot.InlineKeyboardButton("🔌 Connection", callback_data="biz_connection_status"),
            bot.InlineKeyboardButton("🔄 Refresh", callback_data="business_home"),
        ],
        [bot.InlineKeyboardButton("⬅️ Admin Panel", callback_data="admin_home")],
    ])


def silent_business_home_text():
    c = _silent_counts()
    return (
        "💬 <b>BETROXY SILENT SMART INBOX</b>\n\n"
        "Normal follow-up messages are stored silently. You are interrupted only for a new lead, a reopened enquiry, or a chat that needs human attention.\n\n"
        f"🔴 Needs attention: <b>{int(c.get('attention_count') or 0)}</b>\n"
        f"📥 Unread customer messages: <b>{int(c.get('unread_count') or 0)}</b>\n"
        f"🟡 Open conversations: <b>{int(c.get('open_count') or 0)}</b>\n"
        f"🤖 Auto-handled open chats: <b>{int(c.get('auto_handled') or 0)}</b>\n"
        f"✅ Resolved: <b>{int(c.get('resolved_count') or 0)}</b>\n\n"
        "Priority: 🔴 human attention • 🟢 automation handling"
    )


def _filtered_rows(kind, page=0, per_page=8):
    page = max(0, int(page))
    condition = "status='open' AND admin_priority='attention'" if kind == "attention" else "status='open' AND silent_unread_count>0"
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS n FROM telegram_business_enquiries WHERE {condition}")
            total = int((cur.fetchone() or {}).get("n") or 0)
            cur.execute(
                f"""
                SELECT * FROM telegram_business_enquiries
                WHERE {condition}
                ORDER BY CASE WHEN admin_priority='attention' THEN 0 ELSE 1 END,
                         last_customer_message_at DESC NULLS LAST, id DESC
                LIMIT %s OFFSET %s
                """,
                (int(per_page), page * int(per_page)),
            )
            rows = cur.fetchall()
    return rows, total


def _filtered_keyboard(kind, page=0, per_page=8):
    rows, total = _filtered_rows(kind, page, per_page)
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(max(0, int(page)), pages - 1)
    buttons = []
    for r in rows:
        name = v49._customer_name(r)
        unread = int(r.get("silent_unread_count") or 0)
        icon = "🔴" if r.get("admin_priority") == "attention" else "🟢"
        label = f"{icon} {name} • {unread} new"
        buttons.append([bot.InlineKeyboardButton(label[:60], callback_data=f"biz_view:{int(r['id'])}")])
    nav = []
    if page > 0:
        nav.append(bot.InlineKeyboardButton("⬅️", callback_data=f"biz_silent_{kind}:{page-1}"))
    nav.append(bot.InlineKeyboardButton(f"{page+1}/{pages}", callback_data="campaign_noop"))
    if page < pages - 1:
        nav.append(bot.InlineKeyboardButton("➡️", callback_data=f"biz_silent_{kind}:{page+1}"))
    buttons.append(nav)
    buttons.append([bot.InlineKeyboardButton("⬅️ Business Inbox", callback_data="business_home")])
    return bot.InlineKeyboardMarkup(buttons), total


def silent_enquiry_list_keyboard(status="open", page=0, per_page=8):
    rows, total = v49._enquiries(status=status, page=page, per_page=per_page)
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(max(0, int(page)), pages - 1)
    buttons = []
    for r in rows:
        name = v49._customer_name(r)
        unread = int(r.get("silent_unread_count") or 0)
        icon = "🔴" if r.get("admin_priority") == "attention" else "🟢"
        suffix = f" • {unread} new" if unread else ""
        buttons.append([bot.InlineKeyboardButton(f"{icon} {name}{suffix}"[:60], callback_data=f"biz_view:{int(r['id'])}")])
    nav = []
    if page > 0:
        nav.append(bot.InlineKeyboardButton("⬅️", callback_data=f"biz_list:{status}:{page-1}"))
    nav.append(bot.InlineKeyboardButton(f"{page+1}/{pages}", callback_data="campaign_noop"))
    if page < pages - 1:
        nav.append(bot.InlineKeyboardButton("➡️", callback_data=f"biz_list:{status}:{page+1}"))
    buttons.append(nav)
    buttons.append([bot.InlineKeyboardButton("⬅️ Business Inbox", callback_data="business_home")])
    return bot.InlineKeyboardMarkup(buttons), rows, total, page, pages


def silent_enquiry_detail_text(row):
    base = _original_enquiry_detail_text(row)
    priority = "🔴 NEEDS ATTENTION" if row.get("admin_priority") == "attention" else "🟢 AUTOMATION HANDLING"
    reason = str(row.get("attention_reason") or "")
    unread = int(row.get("silent_unread_count") or 0)
    head = f"{priority}\nUnread before opening: <b>{unread}</b>"
    if reason:
        head += f"\nReason: <b>{html.escape(reason)}</b>"
    return head + "\n\n" + base


def _mark_read(enquiry_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE telegram_business_enquiries
                SET silent_unread_count=0, last_admin_viewed_at=NOW()
                WHERE id=%s
                """,
                (int(enquiry_id),),
            )
        conn.commit()


def _clear_attention(enquiry_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE telegram_business_enquiries
                SET silent_unread_count=0, admin_priority='auto', attention_reason=NULL,
                    last_escalation_key=NULL, last_admin_viewed_at=NOW()
                WHERE id=%s
                """,
                (int(enquiry_id),),
            )
        conn.commit()


async def v85_callback_handler(update, context):
    q = update.callback_query
    data = (q.data or "") if q else ""
    if not q:
        return await _previous_callback_handler(update, context)

    if data.startswith("biz_silent_attention:") or data.startswith("biz_silent_unread:"):
        if not bot.is_admin(q.from_user.id):
            await q.answer("Admin only", show_alert=True)
            return
        await q.answer()
        kind = "attention" if data.startswith("biz_silent_attention:") else "unread"
        try:
            page = int(data.rsplit(":", 1)[1])
        except Exception:
            page = 0
        kb, total = _filtered_keyboard(kind, page)
        title = "🔴 NEEDS HUMAN ATTENTION" if kind == "attention" else "📥 UNREAD BUSINESS MESSAGES"
        await q.message.reply_text(
            f"{title}\n\nTotal conversations: <b>{total}</b>\nTap a customer to open the full conversation.",
            parse_mode=bot.ParseMode.HTML,
            reply_markup=kb,
        )
        return

    if data.startswith("biz_view:") and bot.is_admin(q.from_user.id):
        try:
            _mark_read(int(data.split(":", 1)[1]))
        except Exception:
            bot.logger.exception("V85_MARK_READ_FAILED")
        return await _previous_callback_handler(update, context)

    if data.startswith("biz_resolve:") and bot.is_admin(q.from_user.id):
        try:
            _clear_attention(int(data.split(":", 1)[1]))
        except Exception:
            bot.logger.exception("V85_CLEAR_ATTENTION_FAILED")
        return await _previous_callback_handler(update, context)

    return await _previous_callback_handler(update, context)


# ============================================================
# DAILY ADMIN DIGEST
# ============================================================

def _local_day_bounds():
    local_now = v83._local_now()
    local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    offset = timedelta(hours=v83.TZ_OFFSET)
    return local_start - offset, local_start - offset + timedelta(days=1), local_now


def _maybe_send_business_digest():
    start_utc, end_utc, local_now = _local_day_bounds()
    if local_now.hour != 18:
        return
    digest_date = local_now.date()

    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM business_admin_digest_log WHERE digest_date=%s", (digest_date,))
            if cur.fetchone():
                return
            cur.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM telegram_business_enquiries WHERE first_message_at>=%s AND first_message_at<%s) AS leads,
                    (SELECT COUNT(*) FROM telegram_business_messages WHERE direction='inbound' AND created_at>=%s AND created_at<%s) AS inbound,
                    (SELECT COUNT(*) FROM telegram_business_enquiries WHERE status='open' AND admin_priority='attention') AS attention,
                    (SELECT COALESCE(SUM(silent_unread_count),0) FROM telegram_business_enquiries WHERE status='open') AS unread,
                    (SELECT COUNT(*) FROM telegram_business_enquiries WHERE status='open' AND admin_priority<>'attention' AND COALESCE(auto_reply_count,0)>0) AS auto_handled
                """,
                (start_utc, end_utc, start_utc, end_utc),
            )
            r = cur.fetchone() or {}

    inbound = int(r.get("inbound") or 0)
    if inbound <= 0:
        return

    text = (
        "📬 <b>BETROXY BUSINESS INBOX • DAILY SUMMARY</b>\n\n"
        f"New leads today: <b>{int(r.get('leads') or 0)}</b>\n"
        f"Customer messages today: <b>{inbound}</b>\n"
        f"🤖 Auto-handled open chats: <b>{int(r.get('auto_handled') or 0)}</b>\n"
        f"🔴 Needs attention now: <b>{int(r.get('attention') or 0)}</b>\n"
        f"📥 Unread messages: <b>{int(r.get('unread') or 0)}</b>"
    )
    ok, data = v83._tg_send(
        bot.ADMIN_ID,
        text,
        [[{"text": "💬 Open Business Inbox", "callback_data": "business_home"}]],
    )
    if ok:
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO business_admin_digest_log(digest_date,summary) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                    (digest_date, text[:2000]),
                )
            conn.commit()


_original_worker_cycle = v83._worker_cycle


def _v85_worker_cycle(force=False):
    _original_worker_cycle(force=force)
    try:
        _maybe_send_business_digest()
    except Exception:
        bot.logger.exception("V85_BUSINESS_DIGEST_FAILED")


# ============================================================
# PATCH LIVE BINDINGS BEFORE bot.main() BUILDS THE APPLICATION
# ============================================================
try:
    _ensure_v85_schema()
    bot.logger.warning("V85_SILENT_BUSINESS_INBOX_SCHEMA ready=on historical_rows_backfilled=on")
except Exception:
    bot.logger.exception("V85_SILENT_BUSINESS_INBOX_SCHEMA_FAILED")

v49._business_message_update = v85_business_message_update
v49.business_home_keyboard = silent_business_home_keyboard
v49.business_home_text = silent_business_home_text
v49._enquiry_list_keyboard = silent_enquiry_list_keyboard
v49._enquiry_detail_text = silent_enquiry_detail_text

# V51 home helpers are referenced by some callback paths.
biz51.v51_business_home_keyboard = silent_business_home_keyboard
biz51.v51_business_home_text = silent_business_home_text

bot.callback_handler = v85_callback_handler
v83._worker_cycle = _v85_worker_cycle

bot.logger.warning(
    "V85_SILENT_BUSINESS_INBOX active=on first_lead_alert=once normal_messages=silent "
    "priority_escalation=on unread_badges=on daily_digest=18h admin_popup_flood=off"
)


if __name__ == "__main__":
    v63.apply_signup_cta()
    _enable_smart_reply_without_reset()
    threading.Thread(target=v83._worker_loop, name="betroxy-engagement-worker", daemon=True).start()
    bot.logger.warning("V85 polling handover delay=12s")
    time.sleep(12)
    bot.main()
