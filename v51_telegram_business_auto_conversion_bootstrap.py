import html
from datetime import datetime, timezone

import bot
import v50_telegram_business_handler_fix_bootstrap as v50
import v49_telegram_business_inbox_bootstrap as v49

from telegram.ext import ApplicationHandlerStop


# ============================================================
# V51 - TELEGRAM BUSINESS SMART AUTO-CONVERSION
# ============================================================
# Incoming Telegram Business enquiries can now receive a useful automatic
# response that guides the customer to the official BETROXY product bot or
# website. This remains opt-in through the existing Auto Reply toggle.
#
# Safety / UX rules:
# - customer must message the Business account first
# - first reply always gives clear official destinations
# - subsequent keyword replies are throttled and capped
# - no repeated spam on every message
# - human admin is still notified and can take over at any time

_previous_callback_handler = bot.callback_handler

BETROXY_PRODUCT_BOT = "https://t.me/BetroxyBot"
BETROXY_WEBSITE = "https://betroxy.com/"
BETROXY_SPORTSBOOK = "https://t.me/BetroxyBot/sportsbook"
BETROXY_CASINO = "https://t.me/BetroxyBot/casino"


# ============================================================
# DATABASE EXTENSIONS
# ============================================================

def _ensure_v51_columns():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                ALTER TABLE telegram_business_enquiries
                ADD COLUMN IF NOT EXISTS detected_intent TEXT DEFAULT 'general'
                """
            )
            cur.execute(
                """
                ALTER TABLE telegram_business_enquiries
                ADD COLUMN IF NOT EXISTS lead_stage TEXT DEFAULT 'new'
                """
            )
            cur.execute(
                """
                ALTER TABLE telegram_business_enquiries
                ADD COLUMN IF NOT EXISTS auto_reply_count INTEGER DEFAULT 0
                """
            )
            cur.execute(
                """
                ALTER TABLE telegram_business_enquiries
                ADD COLUMN IF NOT EXISTS last_auto_reply_at TIMESTAMPTZ
                """
            )
        conn.commit()


def _update_lead_state(enquiry_id, intent=None, stage=None, auto_replied=False):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE telegram_business_enquiries
                SET detected_intent=COALESCE(%s, detected_intent),
                    lead_stage=COALESCE(%s, lead_stage),
                    auto_reply_count=auto_reply_count + CASE WHEN %s THEN 1 ELSE 0 END,
                    last_auto_reply_at=CASE WHEN %s THEN NOW() ELSE last_auto_reply_at END
                WHERE id=%s
                RETURNING *
                """,
                (intent, stage, bool(auto_replied), bool(auto_replied), int(enquiry_id)),
            )
            row = cur.fetchone()
        conn.commit()
    return row


def _auto_reply_allowed(enquiry):
    # First reply is always allowed when the feature is ON.
    if not enquiry.get('auto_ack_sent_at'):
        return True

    count = int(enquiry.get('auto_reply_count') or 0)
    if count >= 5:
        return False

    last = enquiry.get('last_auto_reply_at')
    if last:
        try:
            now = datetime.now(timezone.utc)
            if (now - last).total_seconds() < 30:
                return False
        except Exception:
            pass
    return True


# ============================================================
# INTENT + REPLY ENGINE
# ============================================================

def _normalise(text):
    return ' '.join(str(text or '').lower().strip().split())


def _detect_intent(text):
    t = _normalise(text)
    if not t:
        return 'general'

    if any(x in t for x in ('withdraw', 'withdrawal', 'cash out', 'payout', 'money not received')):
        return 'withdrawal'
    if any(x in t for x in ('deposit', 'add money', 'payment', 'upi', 'fund', 'recharge')):
        return 'deposit'
    if any(x in t for x in ('bonus', 'offer', 'promo', 'promotion', 'cashback')):
        return 'bonus'
    if any(x in t for x in ('register', 'registration', 'sign up', 'signup', 'create account', 'new account', 'join')):
        return 'register'
    if any(x in t for x in ('login', 'log in', 'password', 'otp', 'account issue', 'account problem')):
        return 'account'
    if any(x in t for x in ('sportsbook', 'sports book', 'sports betting', 'cricket', 'football')):
        return 'sportsbook'
    if any(x in t for x in ('casino', 'slot', 'slots', 'live casino', 'game', 'games')):
        return 'casino'
    if any(x in t for x in ('help', 'support', 'problem', 'issue', 'complaint')):
        return 'support'
    if any(x == t or t.startswith(x + ' ') for x in ('hi', 'hello', 'hey', 'hii', 'hiii', 'good morning', 'good evening')):
        return 'greeting'
    return 'general'


def _reply_payload(intent, first_reply=False):
    common_buttons = bot.InlineKeyboardMarkup([
        [
            bot.InlineKeyboardButton('🤖 Open BetroxyBot', url=BETROXY_PRODUCT_BOT),
            bot.InlineKeyboardButton('🌐 Betroxy.com', url=BETROXY_WEBSITE),
        ]
    ])

    if first_reply or intent in {'greeting', 'general'}:
        return (
            "👋 Welcome to <b>BETROXY</b>.\n\n"
            "I can help you get started or connect you to support. The fastest way to access your BETROXY account is through our official bot or website below.\n\n"
            "If you need help with registration, deposit, withdrawal, bonus, login, sportsbook or casino, just type your question here.",
            common_buttons,
            'engaged',
        )

    if intent == 'register':
        return (
            "✅ <b>Getting started with BETROXY</b>\n\n"
            "Use the official BetroxyBot or Betroxy.com to create/access your account. If you face any issue during registration, send the issue here and our team can assist.",
            common_buttons,
            'registration_intent',
        )

    if intent == 'deposit':
        return (
            "💳 <b>Deposit help</b>\n\n"
            "Please open your BETROXY account through the official bot or website and use the deposit section available there. If a payment is pending or you need support, send the details here and the team will review it.",
            common_buttons,
            'account_support',
        )

    if intent == 'withdrawal':
        return (
            "💸 <b>Withdrawal help</b>\n\n"
            "Please access your BETROXY account through the official bot or website and check the withdrawal section/status. If you already requested a withdrawal and need support, send the relevant details here for the team to review.",
            common_buttons,
            'account_support',
        )

    if intent == 'bonus':
        return (
            "🎁 <b>Offers & promotions</b>\n\n"
            "Current official BETROXY offers are available through BetroxyBot and Betroxy.com. Open either option below to see what is currently available for your account.",
            common_buttons,
            'engaged',
        )

    if intent == 'account':
        return (
            "🔐 <b>Account / login help</b>\n\n"
            "Open your account using the official BETROXY bot or website. If you still cannot log in, send the error or issue here and support can assist.",
            common_buttons,
            'account_support',
        )

    if intent == 'sportsbook':
        kb = bot.InlineKeyboardMarkup([
            [bot.InlineKeyboardButton('🏏 Open Sportsbook', url=BETROXY_SPORTSBOOK)],
            [bot.InlineKeyboardButton('🌐 Betroxy.com', url=BETROXY_WEBSITE)],
        ])
        return (
            "🏏 <b>BETROXY Sportsbook</b>\n\n"
            "You can open the sportsbook directly below, or use Betroxy.com. If you need account assistance, send your question here.",
            kb,
            'product_interest',
        )

    if intent == 'casino':
        kb = bot.InlineKeyboardMarkup([
            [bot.InlineKeyboardButton('🎰 Open Casino', url=BETROXY_CASINO)],
            [bot.InlineKeyboardButton('🌐 Betroxy.com', url=BETROXY_WEBSITE)],
        ])
        return (
            "🎰 <b>BETROXY Casino</b>\n\n"
            "You can open the casino directly below, or use Betroxy.com. If you need account assistance, send your question here.",
            kb,
            'product_interest',
        )

    # support
    return (
        "🧑‍💼 <b>Support request received</b>\n\n"
        "You can access your BETROXY account through the official bot or website below. Your message is also visible to our support team, so you can continue describing the issue here.",
        common_buttons,
        'support_needed',
    )


async def _send_smart_reply(context, enquiry, intent):
    first_reply = not bool(enquiry.get('auto_ack_sent_at'))
    text, keyboard, stage = _reply_payload(intent, first_reply=first_reply)

    try:
        sent = await context.bot.send_message(
            chat_id=int(enquiry['customer_chat_id']),
            text=text,
            parse_mode=bot.ParseMode.HTML,
            business_connection_id=str(enquiry['connection_id']),
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
    except Exception:
        # Some Business chat/client combinations may reject inline markup.
        # Fall back to plain official links rather than failing the auto reply.
        fallback = (
            html.unescape(text.replace('<b>', '').replace('</b>', ''))
            + f"\n\nBetroxyBot: {BETROXY_PRODUCT_BOT}\nWebsite: {BETROXY_WEBSITE}"
        )
        sent = await context.bot.send_message(
            chat_id=int(enquiry['customer_chat_id']),
            text=fallback,
            business_connection_id=str(enquiry['connection_id']),
            disable_web_page_preview=True,
        )
        text = fallback

    if first_reply:
        v49._mark_auto_ack(enquiry['id'])
    v49._record_outbound(enquiry['id'], getattr(sent, 'message_id', None), text)
    return _update_lead_state(enquiry['id'], intent=intent, stage=stage, auto_replied=True)


# ============================================================
# BUSINESS MESSAGE HANDLER
# ============================================================

async def v51_business_message_update(update, context):
    message = update.business_message
    if not message:
        return

    connection_id = getattr(message, 'business_connection_id', None)
    if not connection_id:
        raise ApplicationHandlerStop

    conn = await v49._ensure_connection_from_message(context, connection_id)
    owner_user_id = (conn or {}).get('owner_user_id')
    sender = getattr(message, 'from_user', None)

    # Ignore messages written by the Business owner or by this Business bot.
    if owner_user_id and sender and int(sender.id) == int(owner_user_id):
        raise ApplicationHandlerStop
    if getattr(message, 'sender_business_bot', None):
        raise ApplicationHandlerStop

    try:
        enquiry = v49._upsert_incoming_enquiry(connection_id, message)
    except Exception:
        bot.logger.exception('BUSINESS_ENQUIRY_SAVE_FAILED')
        raise ApplicationHandlerStop

    message_text = getattr(message, 'text', None) or getattr(message, 'caption', None) or ''
    intent = _detect_intent(message_text)
    enquiry = _update_lead_state(enquiry['id'], intent=intent, stage=None, auto_replied=False) or enquiry

    auto_replied = False
    settings = v49._business_settings()
    if settings.get('auto_ack_enabled') and _auto_reply_allowed(enquiry):
        # First message always receives a useful welcome/CTA. Later messages only
        # receive automatic responses when we have a clear actionable intent.
        should_reply = (not enquiry.get('auto_ack_sent_at')) or intent not in {'general', 'greeting'}
        if should_reply:
            try:
                enquiry = await _send_smart_reply(context, enquiry, intent)
                auto_replied = True
            except Exception:
                bot.logger.exception('BUSINESS_SMART_AUTO_REPLY_FAILED')

    # Admin still receives every inbound enquiry so a human can take over.
    preview = html.escape(str(enquiry.get('last_message_text') or ''))
    intent_label = html.escape(str(intent).replace('_', ' ').title())
    try:
        await context.bot.send_message(
            chat_id=bot.ADMIN_ID,
            text=(
                "🔔 <b>TELEGRAM BUSINESS ENQUIRY</b>\n\n"
                f"From: <b>{html.escape(v49._customer_name(enquiry))}</b>\n"
                f"Intent: <b>{intent_label}</b>\n"
                f"Auto reply: <b>{'SENT' if auto_replied else 'NOT SENT'}</b>\n"
                f"Message: {preview}\n\n"
                "You can open the enquiry and take over manually at any time."
            ),
            parse_mode=bot.ParseMode.HTML,
            reply_markup=bot.InlineKeyboardMarkup([
                [bot.InlineKeyboardButton('↩️ Reply Manually', callback_data=f"biz_reply:{enquiry['id']}")],
                [
                    bot.InlineKeyboardButton('👁 Open Enquiry', callback_data=f"biz_view:{enquiry['id']}"),
                    bot.InlineKeyboardButton('✅ Resolve', callback_data=f"biz_resolve:{enquiry['id']}"),
                ],
            ]),
        )
    except Exception:
        bot.logger.exception('BUSINESS_ADMIN_NOTIFY_FAILED')

    raise ApplicationHandlerStop


# ============================================================
# CLEARER ADMIN UI
# ============================================================

def v51_business_home_keyboard():
    counts = v49._business_counts()
    settings = v49._business_settings()
    auto = 'ON ✅' if settings.get('auto_ack_enabled') else 'OFF'
    return bot.InlineKeyboardMarkup([
        [bot.InlineKeyboardButton(f"🟡 Open Enquiries ({counts['open']})", callback_data='biz_list:open:0')],
        [
            bot.InlineKeyboardButton(f"✅ Resolved ({counts['resolved']})", callback_data='biz_list:resolved:0'),
            bot.InlineKeyboardButton('🔌 Connection', callback_data='biz_connection_status'),
        ],
        [bot.InlineKeyboardButton(f"🎯 Smart Auto Reply: {auto}", callback_data='biz_toggle_auto_ack')],
        [bot.InlineKeyboardButton('🔄 Refresh Inbox', callback_data='business_home')],
        [bot.InlineKeyboardButton('⬅️ Admin Panel', callback_data='admin_home')],
    ])


def v51_business_home_text():
    counts = v49._business_counts()
    settings = v49._business_settings()
    auto = 'ON' if settings.get('auto_ack_enabled') else 'OFF'
    return (
        "💬 <b>TELEGRAM BUSINESS SALES & SUPPORT</b>\n\n"
        "Incoming enquiries are stored here. When Smart Auto Reply is ON, BETROXY automatically guides new customers to the official BetroxyBot / Betroxy.com and answers common intent categories.\n\n"
        f"Connected business accounts: <b>{counts['connections']}</b>\n"
        f"Open enquiries: <b>{counts['open']}</b>\n"
        f"Resolved: <b>{counts['resolved']}</b>\n"
        f"Active today: <b>{counts['today']}</b>\n"
        f"Smart Auto Reply: <b>{auto}</b>\n\n"
        "A human admin can take over any enquiry at any time."
    )


async def v51_callback_handler(update, context):
    q = update.callback_query
    data = (q.data or '') if q else ''

    if data == 'biz_toggle_auto_ack':
        await q.answer()
        if not bot.is_admin(q.from_user.id):
            return
        enabled = v49._toggle_auto_ack()
        await q.message.reply_text(
            (
                "🎯 Smart Auto Reply is now ON. New Telegram Business enquiries will be guided automatically to BetroxyBot / Betroxy.com, with intent-based help for common questions."
                if enabled else
                "🎯 Smart Auto Reply is now OFF. Enquiries will still be collected, but replies will wait for you."
            ),
            reply_markup=v51_business_home_keyboard(),
        )
        return

    return await _previous_callback_handler(update, context)


_ensure_v51_columns()

# V50 installs v49._business_message_update when bot.main() builds the PTB app,
# so replacing this module-level reference before main() gives us the smart
# handler without adding a second Business-message handler.
v49._business_message_update = v51_business_message_update
v49.business_home_keyboard = v51_business_home_keyboard
v49.business_home_text = v51_business_home_text
bot.callback_handler = v51_callback_handler

bot.logger.warning(
    'V51_TELEGRAM_BUSINESS_AUTO_CONVERSION_ACTIVE smart_auto_reply=on_if_enabled '
    'official_bot_cta=on website_cta=on intent_routing=on throttle=30s max_auto_replies=5'
)


if __name__ == '__main__':
    bot.main()
