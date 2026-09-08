import html
from datetime import datetime, timezone

import bot
import v48_simple_report_hub_bootstrap as v48

from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    BusinessConnectionHandler,
    BusinessMessagesDeletedHandler,
    MessageHandler,
    filters,
)


# ============================================================
# V49 - TELEGRAM BUSINESS ENQUIRY INBOX
# ============================================================
# A connected Telegram Business account can send its customer chats to this
# bot. We keep this feature separate from Campaign Tracker so business support
# enquiries never get mixed with Instagram/campaign analytics.
#
# Default behaviour is HUMAN-FIRST:
# - receive + store incoming enquiries
# - notify admin immediately
# - admin can reply on behalf of the connected Business account
# - admin can mark enquiries resolved/reopen them
# - optional acknowledgement exists, but is OFF by default

_previous_callback_handler = bot.callback_handler
_previous_chat_handler = bot.chat_handler
_original_admin_menu = bot.admin_menu
_original_application_add_handler = Application.add_handler


# ============================================================
# DATABASE
# ============================================================

def _ensure_business_tables():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS telegram_business_connections (
                    connection_id TEXT PRIMARY KEY,
                    owner_user_id BIGINT,
                    owner_username TEXT,
                    owner_first_name TEXT,
                    can_reply BOOLEAN DEFAULT FALSE,
                    is_enabled BOOLEAN DEFAULT TRUE,
                    connected_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS telegram_business_enquiries (
                    id BIGSERIAL PRIMARY KEY,
                    connection_id TEXT NOT NULL,
                    customer_chat_id BIGINT NOT NULL,
                    customer_user_id BIGINT,
                    customer_username TEXT,
                    customer_first_name TEXT,
                    customer_last_name TEXT,
                    status TEXT NOT NULL DEFAULT 'open',
                    first_message_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_message_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_message_text TEXT,
                    last_message_type TEXT DEFAULT 'text',
                    message_count INTEGER NOT NULL DEFAULT 0,
                    auto_ack_sent_at TIMESTAMPTZ,
                    resolved_at TIMESTAMPTZ,
                    UNIQUE(connection_id, customer_chat_id)
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tg_business_enquiries_status_time
                ON telegram_business_enquiries(status, last_message_at DESC)
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS telegram_business_messages (
                    id BIGSERIAL PRIMARY KEY,
                    enquiry_id BIGINT NOT NULL REFERENCES telegram_business_enquiries(id) ON DELETE CASCADE,
                    direction TEXT NOT NULL,
                    telegram_message_id BIGINT,
                    message_type TEXT,
                    message_text TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tg_business_messages_enquiry_time
                ON telegram_business_messages(enquiry_id, created_at DESC)
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS telegram_business_settings (
                    id INTEGER PRIMARY KEY,
                    auto_ack_enabled BOOLEAN NOT NULL DEFAULT FALSE,
                    auto_ack_text TEXT NOT NULL DEFAULT 'Thanks for contacting BETROXY. We have received your message and our team will reply shortly.',
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                INSERT INTO telegram_business_settings (id)
                VALUES (1)
                ON CONFLICT (id) DO NOTHING
                """
            )
        conn.commit()


def _save_connection(connection):
    user = getattr(connection, 'user', None)
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO telegram_business_connections (
                    connection_id, owner_user_id, owner_username,
                    owner_first_name, can_reply, is_enabled, connected_at, updated_at
                ) VALUES (%s,%s,%s,%s,%s,%s,NOW(),NOW())
                ON CONFLICT (connection_id) DO UPDATE SET
                    owner_user_id=EXCLUDED.owner_user_id,
                    owner_username=EXCLUDED.owner_username,
                    owner_first_name=EXCLUDED.owner_first_name,
                    can_reply=EXCLUDED.can_reply,
                    is_enabled=EXCLUDED.is_enabled,
                    updated_at=NOW()
                """,
                (
                    str(connection.id),
                    getattr(user, 'id', None),
                    getattr(user, 'username', None),
                    getattr(user, 'first_name', None),
                    bool(getattr(connection, 'can_reply', False)),
                    bool(getattr(connection, 'is_enabled', True)),
                ),
            )
        conn.commit()


def _connection_row(connection_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM telegram_business_connections WHERE connection_id=%s",
                (str(connection_id),),
            )
            return cur.fetchone()


def _business_settings():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM telegram_business_settings WHERE id=1")
            return cur.fetchone() or {'auto_ack_enabled': False, 'auto_ack_text': ''}


def _toggle_auto_ack():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE telegram_business_settings
                SET auto_ack_enabled=NOT auto_ack_enabled,
                    updated_at=NOW()
                WHERE id=1
                RETURNING auto_ack_enabled
                """
            )
            row = cur.fetchone() or {}
        conn.commit()
    return bool(row.get('auto_ack_enabled'))


def _message_type(message):
    if getattr(message, 'text', None):
        return 'text'
    if getattr(message, 'photo', None):
        return 'photo'
    if getattr(message, 'video', None):
        return 'video'
    if getattr(message, 'document', None):
        return 'document'
    if getattr(message, 'voice', None):
        return 'voice'
    if getattr(message, 'audio', None):
        return 'audio'
    if getattr(message, 'sticker', None):
        return 'sticker'
    if getattr(message, 'location', None):
        return 'location'
    if getattr(message, 'contact', None):
        return 'contact'
    return 'message'


def _message_preview(message):
    text = (getattr(message, 'text', None) or getattr(message, 'caption', None) or '').strip()
    if text:
        return text[:1500]
    return f"[{_message_type(message).title()}]"


def _upsert_incoming_enquiry(connection_id, message):
    user = getattr(message, 'from_user', None)
    preview = _message_preview(message)
    mtype = _message_type(message)
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO telegram_business_enquiries (
                    connection_id, customer_chat_id, customer_user_id,
                    customer_username, customer_first_name, customer_last_name,
                    status, first_message_at, last_message_at,
                    last_message_text, last_message_type, message_count
                ) VALUES (%s,%s,%s,%s,%s,%s,'open',NOW(),NOW(),%s,%s,1)
                ON CONFLICT (connection_id, customer_chat_id) DO UPDATE SET
                    customer_user_id=COALESCE(EXCLUDED.customer_user_id, telegram_business_enquiries.customer_user_id),
                    customer_username=COALESCE(EXCLUDED.customer_username, telegram_business_enquiries.customer_username),
                    customer_first_name=COALESCE(EXCLUDED.customer_first_name, telegram_business_enquiries.customer_first_name),
                    customer_last_name=COALESCE(EXCLUDED.customer_last_name, telegram_business_enquiries.customer_last_name),
                    status='open',
                    resolved_at=NULL,
                    last_message_at=NOW(),
                    last_message_text=EXCLUDED.last_message_text,
                    last_message_type=EXCLUDED.last_message_type,
                    message_count=telegram_business_enquiries.message_count+1
                RETURNING *
                """,
                (
                    str(connection_id),
                    int(message.chat_id),
                    getattr(user, 'id', None),
                    getattr(user, 'username', None),
                    getattr(user, 'first_name', None),
                    getattr(user, 'last_name', None),
                    preview,
                    mtype,
                ),
            )
            enquiry = cur.fetchone()
            cur.execute(
                """
                INSERT INTO telegram_business_messages (
                    enquiry_id, direction, telegram_message_id,
                    message_type, message_text, created_at
                ) VALUES (%s,'inbound',%s,%s,%s,NOW())
                """,
                (enquiry['id'], getattr(message, 'message_id', None), mtype, preview),
            )
        conn.commit()
    return enquiry


def _record_outbound(enquiry_id, message_id, text):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO telegram_business_messages (
                    enquiry_id, direction, telegram_message_id,
                    message_type, message_text, created_at
                ) VALUES (%s,'outbound',%s,'text',%s,NOW())
                """,
                (int(enquiry_id), message_id, str(text)[:4000]),
            )
            cur.execute(
                """
                UPDATE telegram_business_enquiries
                SET last_message_at=NOW(),
                    last_message_text=%s,
                    last_message_type='text',
                    message_count=message_count+1
                WHERE id=%s
                """,
                (str(text)[:1500], int(enquiry_id)),
            )
        conn.commit()


def _enquiry_by_id(enquiry_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT e.*, c.owner_username, c.owner_first_name,
                       c.owner_user_id, c.can_reply, c.is_enabled
                FROM telegram_business_enquiries e
                LEFT JOIN telegram_business_connections c
                  ON c.connection_id=e.connection_id
                WHERE e.id=%s
                """,
                (int(enquiry_id),),
            )
            return cur.fetchone()


def _enquiries(status='open', page=0, per_page=8):
    page = max(0, int(page))
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS n FROM telegram_business_enquiries WHERE status=%s",
                (status,),
            )
            total = int((cur.fetchone() or {}).get('n') or 0)
            cur.execute(
                """
                SELECT * FROM telegram_business_enquiries
                WHERE status=%s
                ORDER BY last_message_at DESC, id DESC
                LIMIT %s OFFSET %s
                """,
                (status, per_page, page * per_page),
            )
            rows = cur.fetchall()
    return rows, total


def _business_counts():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE status='open') AS open_count,
                    COUNT(*) FILTER (WHERE status='resolved') AS resolved_count,
                    COUNT(*) FILTER (WHERE DATE(last_message_at AT TIME ZONE 'UTC')=CURRENT_DATE) AS active_today
                FROM telegram_business_enquiries
                """
            )
            counts = cur.fetchone() or {}
            cur.execute(
                """
                SELECT COUNT(*) AS n
                FROM telegram_business_connections
                WHERE is_enabled=TRUE
                """
            )
            connected = int((cur.fetchone() or {}).get('n') or 0)
    return {
        'open': int(counts.get('open_count') or 0),
        'resolved': int(counts.get('resolved_count') or 0),
        'today': int(counts.get('active_today') or 0),
        'connections': connected,
    }


def _set_enquiry_status(enquiry_id, status):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE telegram_business_enquiries
                SET status=%s,
                    resolved_at=CASE WHEN %s='resolved' THEN NOW() ELSE NULL END
                WHERE id=%s
                RETURNING *
                """,
                (status, status, int(enquiry_id)),
            )
            row = cur.fetchone()
        conn.commit()
    return row


def _mark_auto_ack(enquiry_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE telegram_business_enquiries SET auto_ack_sent_at=NOW() WHERE id=%s",
                (int(enquiry_id),),
            )
        conn.commit()


def _recent_messages(enquiry_id, limit=8):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM telegram_business_messages
                WHERE enquiry_id=%s
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                (int(enquiry_id), int(limit)),
            )
            rows = cur.fetchall()
    return list(reversed(rows))


# ============================================================
# DISPLAY HELPERS
# ============================================================

def _customer_name(row):
    first = str(row.get('customer_first_name') or '').strip()
    last = str(row.get('customer_last_name') or '').strip()
    username = str(row.get('customer_username') or '').strip().lstrip('@')
    name = ' '.join(x for x in (first, last) if x).strip()
    if username:
        return f"{name or username} (@{username})"
    return name or f"Chat {row.get('customer_chat_id')}"


def business_home_keyboard():
    counts = _business_counts()
    settings = _business_settings()
    ack = 'ON ✅' if settings.get('auto_ack_enabled') else 'OFF'
    return bot.InlineKeyboardMarkup([
        [bot.InlineKeyboardButton(f"🟡 Open Enquiries ({counts['open']})", callback_data='biz_list:open:0')],
        [
            bot.InlineKeyboardButton(f"✅ Resolved ({counts['resolved']})", callback_data='biz_list:resolved:0'),
            bot.InlineKeyboardButton('🔌 Connection', callback_data='biz_connection_status'),
        ],
        [bot.InlineKeyboardButton(f"🤖 Auto Acknowledge: {ack}", callback_data='biz_toggle_auto_ack')],
        [bot.InlineKeyboardButton('🔄 Refresh Inbox', callback_data='business_home')],
        [bot.InlineKeyboardButton('⬅️ Admin Panel', callback_data='admin_home')],
    ])


def business_home_text():
    counts = _business_counts()
    settings = _business_settings()
    ack = 'ON' if settings.get('auto_ack_enabled') else 'OFF'
    return (
        "💬 <b>TELEGRAM BUSINESS INBOX</b>\n\n"
        "Customer enquiries received by the connected Telegram Business account appear here.\n\n"
        f"Connected business accounts: <b>{counts['connections']}</b>\n"
        f"Open enquiries: <b>{counts['open']}</b>\n"
        f"Resolved: <b>{counts['resolved']}</b>\n"
        f"Active today: <b>{counts['today']}</b>\n"
        f"Automatic acknowledgement: <b>{ack}</b>\n\n"
        "Replies sent from this inbox are sent on behalf of the connected Telegram Business account."
    )


def _enquiry_list_keyboard(status, page=0, per_page=8):
    rows, total = _enquiries(status=status, page=page, per_page=per_page)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(int(page), total_pages - 1))
    buttons = []
    for r in rows:
        name = _customer_name(r)
        if len(name) > 34:
            name = name[:33] + '…'
        buttons.append([
            bot.InlineKeyboardButton(
                f"💬 {name}",
                callback_data=f"biz_view:{r['id']}",
            )
        ])
    nav = []
    if page > 0:
        nav.append(bot.InlineKeyboardButton('⬅️', callback_data=f'biz_list:{status}:{page-1}'))
    nav.append(bot.InlineKeyboardButton(f'{page+1}/{total_pages}', callback_data='campaign_noop'))
    if page < total_pages - 1:
        nav.append(bot.InlineKeyboardButton('➡️', callback_data=f'biz_list:{status}:{page+1}'))
    buttons.append(nav)
    buttons.append([bot.InlineKeyboardButton('⬅️ Business Inbox', callback_data='business_home')])
    return bot.InlineKeyboardMarkup(buttons), rows, total, page, total_pages


def _enquiry_detail_text(row):
    msgs = _recent_messages(row['id'], 8)
    lines = [
        f"💬 <b>{html.escape(_customer_name(row))}</b>",
        "",
        f"Status: <b>{html.escape(str(row.get('status') or 'open').upper())}</b>",
        f"Messages: <b>{int(row.get('message_count') or 0)}</b>",
    ]
    if row.get('last_message_at'):
        lines.append(f"Last activity: <b>{row['last_message_at'].strftime('%d %b %Y %H:%M UTC')}</b>")
    lines.extend(['', '<b>Recent conversation</b>'])
    for m in msgs:
        direction = '👤 Customer' if m.get('direction') == 'inbound' else '🧑‍💼 You'
        text = html.escape(str(m.get('message_text') or f"[{m.get('message_type') or 'message'}]")[:1000])
        lines.append(f"\n{direction}: {text}")
    return '\n'.join(lines)


def _enquiry_detail_keyboard(row):
    buttons = []
    if row.get('status') == 'open':
        buttons.append([bot.InlineKeyboardButton('↩️ Reply to Customer', callback_data=f"biz_reply:{row['id']}")])
        buttons.append([bot.InlineKeyboardButton('✅ Mark Resolved', callback_data=f"biz_resolve:{row['id']}")])
    else:
        buttons.append([bot.InlineKeyboardButton('↩️ Reply to Customer', callback_data=f"biz_reply:{row['id']}")])
        buttons.append([bot.InlineKeyboardButton('🟡 Reopen Enquiry', callback_data=f"biz_reopen:{row['id']}")])
    buttons.append([bot.InlineKeyboardButton('⬅️ Business Inbox', callback_data='business_home')])
    return bot.InlineKeyboardMarkup(buttons)


def v49_admin_menu():
    markup = _original_admin_menu()
    rows = [list(r) for r in markup.inline_keyboard]
    business_row = [bot.InlineKeyboardButton('💬 Telegram Business Inbox', callback_data='business_home')]
    # Keep campaign tracker prominent, then business enquiries directly under it.
    insert_at = 1 if rows else 0
    rows.insert(insert_at, business_row)
    return bot.InlineKeyboardMarkup(rows)


# ============================================================
# TELEGRAM BUSINESS UPDATE HANDLERS
# ============================================================

async def _business_connection_update(update, context):
    connection = update.business_connection
    if not connection:
        return
    try:
        _save_connection(connection)
        status = 'connected' if connection.is_enabled else 'disconnected'
        can_reply = 'yes' if connection.can_reply else 'no'
        await context.bot.send_message(
            chat_id=bot.ADMIN_ID,
            text=(
                "🔌 <b>Telegram Business connection updated</b>\n\n"
                f"Status: <b>{status}</b>\n"
                f"Can reply: <b>{can_reply}</b>\n\n"
                "Open Admin Panel → Telegram Business Inbox to manage enquiries."
            ),
            parse_mode=bot.ParseMode.HTML,
            reply_markup=business_home_keyboard(),
        )
    except Exception:
        bot.logger.exception('BUSINESS_CONNECTION_UPDATE_FAILED')


async def _ensure_connection_from_message(context, connection_id):
    row = _connection_row(connection_id)
    if row:
        return row
    try:
        connection = await context.bot.get_business_connection(str(connection_id))
        _save_connection(connection)
        return _connection_row(connection_id)
    except Exception:
        bot.logger.exception('BUSINESS_CONNECTION_LOOKUP_FAILED')
        return None


async def _business_message_update(update, context):
    message = update.business_message
    if not message:
        return
    connection_id = getattr(message, 'business_connection_id', None)
    if not connection_id:
        raise ApplicationHandlerStop

    conn = await _ensure_connection_from_message(context, connection_id)
    owner_user_id = (conn or {}).get('owner_user_id')
    sender = getattr(message, 'from_user', None)

    # Messages written by the Business account itself (or by this business bot)
    # are outbound activity, not new customer enquiries.
    if owner_user_id and sender and int(sender.id) == int(owner_user_id):
        raise ApplicationHandlerStop
    if getattr(message, 'sender_business_bot', None):
        raise ApplicationHandlerStop

    try:
        enquiry = _upsert_incoming_enquiry(connection_id, message)
    except Exception:
        bot.logger.exception('BUSINESS_ENQUIRY_SAVE_FAILED')
        raise ApplicationHandlerStop

    # Optional acknowledgement is deliberately OFF by default. When enabled,
    # send only once per enquiry so customers are not spammed on every message.
    settings = _business_settings()
    if settings.get('auto_ack_enabled') and not enquiry.get('auto_ack_sent_at'):
        try:
            sent = await context.bot.send_message(
                chat_id=int(enquiry['customer_chat_id']),
                text=str(settings.get('auto_ack_text') or '').strip(),
                business_connection_id=str(enquiry['connection_id']),
            )
            _mark_auto_ack(enquiry['id'])
            _record_outbound(enquiry['id'], getattr(sent, 'message_id', None), settings.get('auto_ack_text') or '')
        except Exception:
            bot.logger.exception('BUSINESS_AUTO_ACK_FAILED')

    preview = html.escape(str(enquiry.get('last_message_text') or ''))
    try:
        await context.bot.send_message(
            chat_id=bot.ADMIN_ID,
            text=(
                "🔔 <b>NEW TELEGRAM BUSINESS ENQUIRY</b>\n\n"
                f"From: <b>{html.escape(_customer_name(enquiry))}</b>\n"
                f"Message: {preview}\n\n"
                "Reply from the button below; the customer will receive it from your Telegram Business account."
            ),
            parse_mode=bot.ParseMode.HTML,
            reply_markup=bot.InlineKeyboardMarkup([
                [bot.InlineKeyboardButton('↩️ Reply', callback_data=f"biz_reply:{enquiry['id']}")],
                [
                    bot.InlineKeyboardButton('👁 Open Enquiry', callback_data=f"biz_view:{enquiry['id']}"),
                    bot.InlineKeyboardButton('✅ Resolve', callback_data=f"biz_resolve:{enquiry['id']}"),
                ],
            ]),
        )
    except Exception:
        bot.logger.exception('BUSINESS_ADMIN_NOTIFY_FAILED')

    # Do not allow this Business message to fall through to the normal public
    # chat handler. The two systems are intentionally isolated.
    raise ApplicationHandlerStop


async def _business_deleted_update(update, context):
    # We intentionally keep the enquiry audit trail. A customer deleting a
    # Telegram message should not erase the support record or break the inbox.
    raise ApplicationHandlerStop


# ============================================================
# ADMIN CALLBACKS + MANUAL REPLIES
# ============================================================

async def v49_callback_handler(update, context):
    q = update.callback_query
    data = (q.data or '') if q else ''

    if data == 'business_home':
        await q.answer()
        if not bot.is_admin(q.from_user.id):
            return
        await q.message.reply_text(
            business_home_text(),
            parse_mode=bot.ParseMode.HTML,
            reply_markup=business_home_keyboard(),
        )
        return

    if data.startswith('biz_list:'):
        await q.answer()
        if not bot.is_admin(q.from_user.id):
            return
        parts = data.split(':')
        status = parts[1] if len(parts) > 1 and parts[1] in {'open', 'resolved'} else 'open'
        try:
            page = int(parts[2]) if len(parts) > 2 else 0
        except Exception:
            page = 0
        kb, rows, total, page, total_pages = _enquiry_list_keyboard(status, page)
        title = 'OPEN ENQUIRIES' if status == 'open' else 'RESOLVED ENQUIRIES'
        await q.message.reply_text(
            f"💬 <b>{title}</b>\n\nTotal: <b>{total}</b>\nTap a customer to view the conversation and reply.",
            parse_mode=bot.ParseMode.HTML,
            reply_markup=kb,
        )
        return

    if data.startswith('biz_view:'):
        await q.answer()
        if not bot.is_admin(q.from_user.id):
            return
        try:
            enquiry_id = int(data.split(':', 1)[1])
        except Exception:
            return
        row = _enquiry_by_id(enquiry_id)
        if not row:
            await q.message.reply_text('Enquiry not found.', reply_markup=business_home_keyboard())
            return
        await q.message.reply_text(
            _enquiry_detail_text(row),
            parse_mode=bot.ParseMode.HTML,
            reply_markup=_enquiry_detail_keyboard(row),
            disable_web_page_preview=True,
        )
        return

    if data.startswith('biz_reply:'):
        await q.answer()
        if not bot.is_admin(q.from_user.id):
            return
        try:
            enquiry_id = int(data.split(':', 1)[1])
        except Exception:
            return
        row = _enquiry_by_id(enquiry_id)
        if not row:
            await q.message.reply_text('Enquiry not found.')
            return
        if not row.get('is_enabled', True):
            await q.message.reply_text('❌ This Telegram Business connection is currently disabled.')
            return
        if row.get('can_reply') is False:
            await q.message.reply_text('❌ The connected Business bot does not currently have permission to reply.')
            return
        context.user_data['business_reply_enquiry_id'] = enquiry_id
        await q.message.reply_text(
            f"↩️ <b>Replying to {html.escape(_customer_name(row))}</b>\n\n"
            "Send the reply as your next text message. It will be delivered from the connected Telegram Business account.",
            parse_mode=bot.ParseMode.HTML,
            reply_markup=bot.InlineKeyboardMarkup([
                [bot.InlineKeyboardButton('❌ Cancel Reply', callback_data='biz_reply_cancel')],
            ]),
        )
        return

    if data == 'biz_reply_cancel':
        await q.answer('Reply cancelled')
        context.user_data.pop('business_reply_enquiry_id', None)
        await q.message.reply_text('Reply cancelled.', reply_markup=business_home_keyboard())
        return

    if data.startswith('biz_resolve:') or data.startswith('biz_reopen:'):
        await q.answer()
        if not bot.is_admin(q.from_user.id):
            return
        try:
            enquiry_id = int(data.split(':', 1)[1])
        except Exception:
            return
        new_status = 'resolved' if data.startswith('biz_resolve:') else 'open'
        row = _set_enquiry_status(enquiry_id, new_status)
        if row:
            await q.message.reply_text(
                '✅ Enquiry marked resolved.' if new_status == 'resolved' else '🟡 Enquiry reopened.',
                reply_markup=business_home_keyboard(),
            )
        return

    if data == 'biz_toggle_auto_ack':
        await q.answer()
        if not bot.is_admin(q.from_user.id):
            return
        enabled = _toggle_auto_ack()
        await q.message.reply_text(
            (
                '🤖 Automatic acknowledgement is now ON. New customers will receive one acknowledgement message.'
                if enabled else
                '🤖 Automatic acknowledgement is now OFF. Enquiries will wait for your manual reply.'
            ),
            reply_markup=business_home_keyboard(),
        )
        return

    if data == 'biz_connection_status':
        await q.answer()
        if not bot.is_admin(q.from_user.id):
            return
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM telegram_business_connections
                    ORDER BY updated_at DESC
                    LIMIT 10
                    """
                )
                rows = cur.fetchall()
        if not rows:
            text = (
                "🔌 <b>TELEGRAM BUSINESS CONNECTION</b>\n\n"
                "No Telegram Business account has connected to this bot yet.\n\n"
                "Enable Telegram Business/Secretary mode for the bot in @BotFather, then connect this bot from the Telegram Business account settings."
            )
        else:
            lines = ['🔌 <b>TELEGRAM BUSINESS CONNECTION</b>', '']
            for r in rows:
                owner = r.get('owner_username') or r.get('owner_first_name') or str(r.get('owner_user_id') or 'Business account')
                enabled = '✅ Connected' if r.get('is_enabled') else '❌ Disconnected'
                reply = '✅ Reply allowed' if r.get('can_reply') else '❌ Reply not allowed'
                lines.append(f"<b>{html.escape(str(owner))}</b> — {enabled} — {reply}")
            text = '\n'.join(lines)
        await q.message.reply_text(
            text,
            parse_mode=bot.ParseMode.HTML,
            reply_markup=business_home_keyboard(),
        )
        return

    return await _previous_callback_handler(update, context)


async def v49_chat_handler(update, context):
    # Admin's next ordinary text message is used as the pending Business reply.
    pending = context.user_data.get('business_reply_enquiry_id')
    if pending and update.effective_user and bot.is_admin(update.effective_user.id):
        message = update.message
        text = (message.text if message else '') or ''
        text = text.strip()
        if not text:
            await update.effective_message.reply_text('Please send a text reply, or tap Cancel Reply.')
            return
        row = _enquiry_by_id(pending)
        if not row:
            context.user_data.pop('business_reply_enquiry_id', None)
            await update.effective_message.reply_text('Enquiry no longer exists.', reply_markup=business_home_keyboard())
            return
        try:
            sent = await context.bot.send_message(
                chat_id=int(row['customer_chat_id']),
                text=text,
                business_connection_id=str(row['connection_id']),
            )
            _record_outbound(row['id'], getattr(sent, 'message_id', None), text)
            context.user_data.pop('business_reply_enquiry_id', None)
            await update.effective_message.reply_text(
                f"✅ Reply sent to <b>{html.escape(_customer_name(row))}</b> from the Telegram Business account.",
                parse_mode=bot.ParseMode.HTML,
                reply_markup=_enquiry_detail_keyboard(_enquiry_by_id(row['id'])),
            )
            return
        except Exception as exc:
            bot.logger.exception('BUSINESS_MANUAL_REPLY_FAILED')
            await update.effective_message.reply_text(
                "❌ Telegram could not send this Business reply. Check that the Business connection is enabled, reply permission is granted, and the customer chat is still eligible for bot replies.\n\n"
                f"Technical detail: {html.escape(str(exc))[:500]}",
                parse_mode=bot.ParseMode.HTML,
                reply_markup=business_home_keyboard(),
            )
            return

    return await _previous_chat_handler(update, context)


# ============================================================
# INJECT BUSINESS-SPECIFIC UPDATE HANDLERS
# ============================================================

def _v49_add_handler(self, handler, group=0):
    if not getattr(self, '_betroxy_business_handlers_installed', False):
        self._betroxy_business_handlers_installed = True
        # Group -2 runs before the normal bot handlers. Business messages are
        # stopped after processing so they never fall through into public chat.
        _original_application_add_handler(self, BusinessConnectionHandler(_business_connection_update), group=-2)
        _original_application_add_handler(
            self,
            MessageHandler(filters.UpdateType.BUSINESS_MESSAGE, _business_message_update),
            group=-2,
        )
        _original_application_add_handler(self, BusinessMessagesDeletedHandler(_business_deleted_update), group=-2)
    return _original_application_add_handler(self, handler, group=group)


_ensure_business_tables()
Application.add_handler = _v49_add_handler
bot.admin_menu = v49_admin_menu
bot.callback_handler = v49_callback_handler
bot.chat_handler = v49_chat_handler

bot.logger.warning(
    'V49_TELEGRAM_BUSINESS_INBOX_ACTIVE human_first=on auto_ack_default=off admin_reply=on business_updates=isolated'
)


if __name__ == '__main__':
    bot.main()
