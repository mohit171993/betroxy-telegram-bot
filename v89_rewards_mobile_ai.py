import io
import os
import re
import threading
import time
from datetime import datetime, timezone

import bot
import v88_unified_free_quiz_bridge as v88
import v62_ai_admin_assistant_bootstrap as v62

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, LongTable, TableStyle
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, filters

# ============================================================
# V89 - REWARDS FOUNDATION + CONSENTED MOBILE REPORT + AI COMMAND FIX
# ============================================================
# - Prepares an INR weekly reward engine with a Giftport adapter boundary.
# - DOES NOT issue real vouchers until Giftport production credentials/API mapping
#   are explicitly configured and auto-issue is enabled by the admin.
# - Collects mobile numbers only when the Telegram user explicitly shares their
#   own contact. Telegram does not expose phone numbers automatically.
# - Adds an admin-only PDF report of all known bot/business users and mobile
#   numbers that were explicitly shared.
# - Makes /ai <instruction> work and keeps the existing natural-language admin
#   assistant available.
# ============================================================

v87 = v88.v87
v86 = v88.v86
v85 = v88.v85
v83 = v88.v83
v49 = v85.v49

MOBILE_DEEPLINK = f"https://t.me/{v83.OFFICIAL_BOT}?start=mobile"
GIFTPORT_API_BASE = os.getenv("GIFTPORT_API_BASE", "").strip().rstrip("/")
GIFTPORT_API_KEY = os.getenv("GIFTPORT_API_KEY", "").strip()
GIFTPORT_API_SECRET = os.getenv("GIFTPORT_API_SECRET", "").strip()
GIFTPORT_ORDER_PATH = os.getenv("GIFTPORT_ORDER_PATH", "").strip()
GIFTPORT_WEBHOOK_SECRET = os.getenv("GIFTPORT_WEBHOOK_SECRET", "").strip()

_old_start = bot.start
_old_callback_handler = bot.callback_handler
_old_chat_handler = bot.chat_handler
_old_public_menu = bot.public_menu
_old_admin_menu = bot.admin_menu
_old_business_menu = v88.v88_business_menu
_old_business_message_update = v49._business_message_update
_old_worker_cycle = v83._worker_cycle
_old_application_add_handler = Application.add_handler

_installed_application_ids = set()


def _ensure_v89_schema():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS user_contact_profiles (
                    telegram_user_id BIGINT PRIMARY KEY,
                    mobile_number TEXT,
                    mobile_source TEXT,
                    mobile_consent_at TIMESTAMPTZ,
                    mobile_prompted_at TIMESTAMPTZ,
                    mobile_removed_at TIMESTAMPTZ,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS reward_settings (
                    id INTEGER PRIMARY KEY,
                    program_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                    auto_issue_enabled BOOLEAN NOT NULL DEFAULT FALSE,
                    provider TEXT NOT NULL DEFAULT 'giftport',
                    currency TEXT NOT NULL DEFAULT 'INR',
                    weekly_first_amount INTEGER NOT NULL DEFAULT 500,
                    weekly_second_amount INTEGER NOT NULL DEFAULT 250,
                    weekly_third_amount INTEGER NOT NULL DEFAULT 100,
                    daily_budget INTEGER NOT NULL DEFAULT 5000,
                    monthly_budget INTEGER NOT NULL DEFAULT 50000,
                    min_provider_balance INTEGER NOT NULL DEFAULT 2000,
                    default_brand_code TEXT NOT NULL DEFAULT 'AMAZON_IN',
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute("INSERT INTO reward_settings(id) VALUES (1) ON CONFLICT(id) DO NOTHING")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS reward_awards (
                    id BIGSERIAL PRIMARY KEY,
                    telegram_user_id BIGINT NOT NULL,
                    reward_type TEXT NOT NULL DEFAULT 'weekly_leaderboard',
                    period_key TEXT NOT NULL,
                    rank INTEGER,
                    points INTEGER NOT NULL DEFAULT 0,
                    amount INTEGER NOT NULL,
                    currency TEXT NOT NULL DEFAULT 'INR',
                    brand_code TEXT,
                    provider TEXT NOT NULL DEFAULT 'giftport',
                    status TEXT NOT NULL DEFAULT 'queued',
                    provider_order_id TEXT,
                    voucher_code TEXT,
                    voucher_pin TEXT,
                    voucher_url TEXT,
                    error_detail TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    issued_at TIMESTAMPTZ,
                    delivered_at TIMESTAMPTZ,
                    UNIQUE(reward_type, period_key, rank)
                )
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_reward_awards_user_time ON reward_awards(telegram_user_id, created_at DESC)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_reward_awards_status ON reward_awards(status, created_at)")
        conn.commit()


def _reward_settings():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM reward_settings WHERE id=1")
            return cur.fetchone() or {}


def _giftport_ready():
    # Exact auth/payload mapping will be locked only after the private API docs/
    # credentials are supplied. Until then this deliberately fails closed.
    return bool(GIFTPORT_API_BASE and GIFTPORT_API_KEY and GIFTPORT_ORDER_PATH)


def _mobile_row(uid):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM user_contact_profiles WHERE telegram_user_id=%s", (int(uid),))
            return cur.fetchone()


def _normalize_mobile(value):
    raw = str(value or "").strip()
    digits = re.sub(r"\D+", "", raw)
    if not (7 <= len(digits) <= 15):
        return None
    # Most users are Indian. Normalize a bare 10-digit Indian mobile to +91.
    if len(digits) == 10:
        return "+91" + digits
    return "+" + digits


def _save_mobile(uid, raw, source="officialbot_contact"):
    mobile = _normalize_mobile(raw)
    if not mobile:
        return None
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO user_contact_profiles(
                    telegram_user_id,mobile_number,mobile_source,mobile_consent_at,mobile_removed_at,updated_at
                ) VALUES (%s,%s,%s,NOW(),NULL,NOW())
                ON CONFLICT(telegram_user_id) DO UPDATE SET
                    mobile_number=EXCLUDED.mobile_number,
                    mobile_source=EXCLUDED.mobile_source,
                    mobile_consent_at=NOW(),
                    mobile_removed_at=NULL,
                    updated_at=NOW()
                RETURNING *
                """,
                (int(uid), mobile, source),
            )
            row = cur.fetchone()
        conn.commit()
    return row


def _mark_mobile_prompted(uid):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO user_contact_profiles(telegram_user_id,mobile_prompted_at)
                VALUES (%s,NOW())
                ON CONFLICT(telegram_user_id) DO UPDATE SET
                    mobile_prompted_at=COALESCE(user_contact_profiles.mobile_prompted_at,NOW()),
                    updated_at=NOW()
                """,
                (int(uid),),
            )
        conn.commit()


def _remove_mobile(uid):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO user_contact_profiles(telegram_user_id,mobile_removed_at,updated_at)
                VALUES (%s,NOW(),NOW())
                ON CONFLICT(telegram_user_id) DO UPDATE SET
                    mobile_number=NULL,
                    mobile_source=NULL,
                    mobile_consent_at=NULL,
                    mobile_removed_at=NOW(),
                    updated_at=NOW()
                """,
                (int(uid),),
            )
        conn.commit()


def _mobile_request_markup():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📱 Share My Mobile", request_contact=True)], [KeyboardButton("Not now")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


async def _send_mobile_prompt(msg, uid):
    _mark_mobile_prompted(uid)
    await msg.reply_text(
        "📱 <b>Add your mobile for rewards & support</b>\n\n"
        "Telegram does not give us your phone number automatically. If you want BETROXY to store your number for reward delivery, account matching and support, tap <b>Share My Mobile</b>.\n\n"
        "Sharing is optional and you can remove it later.",
        parse_mode=bot.ParseMode.HTML,
        reply_markup=_mobile_request_markup(),
    )


async def _official_contact_handler(update, context):
    user = update.effective_user
    msg = update.effective_message
    contact = getattr(msg, "contact", None) if msg else None
    if not user or not msg or not contact:
        return
    contact_uid = getattr(contact, "user_id", None)
    if not contact_uid or int(contact_uid) != int(user.id):
        await msg.reply_text(
            "For privacy, please use the <b>Share My Mobile</b> button to share your own Telegram-linked contact.",
            parse_mode=bot.ParseMode.HTML,
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    row = _save_mobile(user.id, getattr(contact, "phone_number", ""), "officialbot_contact")
    if not row:
        await msg.reply_text("I couldn't validate that mobile number. Please try again.", reply_markup=ReplyKeyboardRemove())
        return
    await msg.reply_text(
        f"✅ Mobile saved securely for rewards/support: <code>{row['mobile_number']}</code>\n\n"
        "You can remove it anytime from <b>📱 Mobile for Rewards</b>.",
        parse_mode=bot.ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove(),
    )


def _insert_button(markup, text, *, callback_data=None, url=None, position=None):
    rows = [list(r) for r in markup.inline_keyboard]
    if any(any(text.lower() in str(getattr(b, "text", "")).lower() for b in row) for row in rows):
        return markup
    btn = InlineKeyboardButton(text, callback_data=callback_data, url=url)
    idx = len(rows) if position is None else max(0, min(int(position), len(rows)))
    rows.insert(idx, [btn])
    return InlineKeyboardMarkup(rows)


def v89_public_menu(user_id=None):
    markup = _old_public_menu(user_id)
    markup = _insert_button(markup, "🎁 My Rewards", callback_data="v89_my_rewards", position=2)
    markup = _insert_button(markup, "📱 Mobile for Rewards", callback_data="v89_mobile", position=3)
    return markup


def v89_admin_menu():
    markup = _old_admin_menu()
    markup = _insert_button(markup, "🎁 Reward Center", callback_data="v89_reward_center", position=1)
    markup = _insert_button(markup, "📱 User Mobile Report (PDF)", callback_data="v89_mobile_report", position=2)
    return markup


def v89_business_menu(styled=True):
    markup = _old_business_menu(styled=styled)
    return _insert_button(markup, "📱 Verify Mobile for Rewards", url=MOBILE_DEEPLINK, position=2)


def _my_rewards_text(uid):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM reward_awards
                WHERE telegram_user_id=%s
                ORDER BY created_at DESC LIMIT 8
                """,
                (int(uid),),
            )
            rows = cur.fetchall()
    if not rows:
        return (
            "🎁 <b>My Rewards</b>\n\n"
            "No voucher rewards have been issued to you yet. Keep playing the free Sports Quiz & live predictions to build your weekly score."
        )
    lines = ["🎁 <b>My Rewards</b>", ""]
    for r in rows:
        status = str(r.get("status") or "queued").replace("_", " ").title()
        lines.append(f"• ₹{int(r.get('amount') or 0):,} - {status} - {r.get('period_key')}")
        if r.get("voucher_url"):
            lines.append(f"  Claim: {r['voucher_url']}")
        elif r.get("voucher_code"):
            lines.append(f"  Code: <code>{r['voucher_code']}</code>")
    return "\n".join(lines)


def _reward_center_text():
    s = _reward_settings()
    provider = "READY ✅" if _giftport_ready() else "WAITING FOR API CONFIG"
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) FILTER(WHERE status='queued') AS queued,
                       COUNT(*) FILTER(WHERE status='delivered') AS delivered,
                       COALESCE(SUM(amount) FILTER(WHERE status='delivered'),0) AS delivered_value
                FROM reward_awards
                """
            )
            c = cur.fetchone() or {}
    return (
        "🎁 <b>BETROXY REWARD CENTER</b>\n\n"
        f"Program: <b>{'ON ✅' if s.get('program_enabled') else 'OFF'}</b>\n"
        f"Auto voucher issue: <b>{'ON ✅' if s.get('auto_issue_enabled') else 'OFF'}</b>\n"
        f"Provider: <b>Giftport</b> - {provider}\n"
        f"Currency: <b>INR</b>\n\n"
        f"🥇 Weekly #1: <b>₹{int(s.get('weekly_first_amount') or 0):,}</b>\n"
        f"🥈 Weekly #2: <b>₹{int(s.get('weekly_second_amount') or 0):,}</b>\n"
        f"🥉 Weekly #3: <b>₹{int(s.get('weekly_third_amount') or 0):,}</b>\n"
        f"Daily budget guard: <b>₹{int(s.get('daily_budget') or 0):,}</b>\n"
        f"Monthly budget guard: <b>₹{int(s.get('monthly_budget') or 0):,}</b>\n\n"
        f"Queued awards: <b>{int(c.get('queued') or 0)}</b>\n"
        f"Delivered awards: <b>{int(c.get('delivered') or 0)}</b>\n"
        f"Delivered value: <b>₹{int(c.get('delivered_value') or 0):,}</b>\n\n"
        "Real Giftport issuance stays fail-closed until the exact private API authentication/order mapping is configured."
    )


def _reward_center_keyboard():
    s = _reward_settings()
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🏆 Reward Program: {'ON ✅' if s.get('program_enabled') else 'OFF'}", callback_data="v89_reward_toggle_program")],
        [InlineKeyboardButton(f"⚡ Auto Issue: {'ON ✅' if s.get('auto_issue_enabled') else 'OFF'}", callback_data="v89_reward_toggle_issue")],
        [InlineKeyboardButton("🧮 Prepare This Week's Winners", callback_data="v89_reward_settle")],
        [InlineKeyboardButton("📱 User Mobile Report PDF", callback_data="v89_mobile_report")],
        [InlineKeyboardButton("⬅️ Admin Panel", callback_data="admin_home")],
    ])


def _prepare_weekly_awards(force=False):
    local = v83._local_now()
    if not force and (local.weekday() != 6 or local.hour < 20):
        return 0
    settings = _reward_settings()
    if not settings.get("program_enabled"):
        return 0
    period_key = f"weekly:{local.strftime('%G-W%V')}"
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH pts AS (
                    SELECT telegram_user_id, points, answered_at AS t
                    FROM engagement_quiz_answers
                    WHERE answered_at >= DATE_TRUNC('week',NOW())
                    UNION ALL
                    SELECT telegram_user_id, points, predicted_at AS t
                    FROM live_sports_predictions
                    WHERE predicted_at >= DATE_TRUNC('week',NOW())
                ), ranked AS (
                    SELECT telegram_user_id, SUM(points)::INTEGER AS score, MIN(t) AS first_action
                    FROM pts
                    GROUP BY telegram_user_id
                    HAVING SUM(points) > 0
                    ORDER BY SUM(points) DESC, MIN(t) ASC
                    LIMIT 3
                )
                SELECT * FROM ranked
                """
            )
            winners = cur.fetchall()
            amounts = [
                int(settings.get("weekly_first_amount") or 500),
                int(settings.get("weekly_second_amount") or 250),
                int(settings.get("weekly_third_amount") or 100),
            ]
            inserted = 0
            for rank, w in enumerate(winners, 1):
                cur.execute(
                    """
                    INSERT INTO reward_awards(
                        telegram_user_id,reward_type,period_key,rank,points,amount,currency,brand_code,provider,status
                    ) VALUES (%s,'weekly_leaderboard',%s,%s,%s,%s,'INR',%s,'giftport','queued')
                    ON CONFLICT(reward_type,period_key,rank) DO NOTHING
                    RETURNING id
                    """,
                    (
                        int(w["telegram_user_id"]), period_key, rank, int(w["score"] or 0),
                        amounts[rank-1], settings.get("default_brand_code") or "AMAZON_IN",
                    ),
                )
                if cur.fetchone():
                    inserted += 1
        conn.commit()
    if inserted:
        bot.logger.warning("V89_REWARD_WINNERS_PREPARED period=%s inserted=%s", period_key, inserted)
    return inserted


def _known_user_rows():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH all_users AS (
                    SELECT telegram_user_id AS uid, telegram_username AS username,
                           first_name, last_name, first_seen_at AS first_at, last_seen_at AS last_at,
                           'Lead'::TEXT AS source
                    FROM intelligence_leads
                    WHERE telegram_user_id IS NOT NULL
                    UNION ALL
                    SELECT telegram_user_id, telegram_username, first_name, last_name,
                           joined_at, joined_at, 'OfficialBot'
                    FROM referrals
                    WHERE telegram_user_id IS NOT NULL
                    UNION ALL
                    SELECT customer_user_id, customer_username, customer_first_name, customer_last_name,
                           first_message_at, last_message_at, 'Business Chat'
                    FROM telegram_business_enquiries
                    WHERE customer_user_id IS NOT NULL
                ), agg AS (
                    SELECT uid,
                           MAX(NULLIF(username,'')) AS username,
                           MAX(NULLIF(first_name,'')) AS first_name,
                           MAX(NULLIF(last_name,'')) AS last_name,
                           MIN(first_at) AS first_at,
                           MAX(last_at) AS last_at,
                           STRING_AGG(DISTINCT source, ', ' ORDER BY source) AS sources
                    FROM all_users
                    GROUP BY uid
                )
                SELECT a.*, c.mobile_number, c.mobile_source, c.mobile_consent_at
                FROM agg a
                LEFT JOIN user_contact_profiles c ON c.telegram_user_id=a.uid
                ORDER BY a.last_at DESC NULLS LAST, a.uid
                """
            )
            return cur.fetchall()


def _mobile_report_pdf():
    rows = _known_user_rows()
    mobile_count = sum(1 for r in rows if r.get("mobile_number"))
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        rightMargin=24, leftMargin=24, topMargin=28, bottomMargin=28,
        title="BETROXY User Mobile Report",
    )
    styles = getSampleStyleSheet()
    title = ParagraphStyle("ReportTitle", parent=styles["Title"], alignment=TA_CENTER, fontSize=16, leading=20)
    small = ParagraphStyle("Small", parent=styles["BodyText"], fontSize=7, leading=9)
    story = [
        Paragraph("BETROXY - User Mobile Report", title),
        Spacer(1, 6),
        Paragraph(
            f"Generated: {datetime.now(timezone.utc).strftime('%d %b %Y %H:%M UTC')} | "
            f"Known users: {len(rows)} | Mobile explicitly shared: {mobile_count} | Not shared: {len(rows)-mobile_count}",
            styles["BodyText"],
        ),
        Spacer(1, 4),
        Paragraph(
            "Important: Telegram does not reveal a user's phone number to a bot automatically. "
            "The Mobile column contains only numbers the user explicitly shared with BETROXY; all others are shown as Not shared.",
            small,
        ),
        Spacer(1, 10),
    ]
    data = [["#", "Name", "Telegram", "Mobile", "Source", "First Seen", "Last Seen"]]
    for i, r in enumerate(rows, 1):
        name = " ".join(x for x in [str(r.get("first_name") or "").strip(), str(r.get("last_name") or "").strip()] if x).strip() or "-"
        username = ("@" + str(r.get("username")).lstrip("@")) if r.get("username") else "-"
        mobile = str(r.get("mobile_number") or "Not shared")
        first_at = r.get("first_at")
        last_at = r.get("last_at")
        data.append([
            str(i),
            Paragraph(name[:34], small),
            Paragraph(username[:34], small),
            Paragraph(mobile, small),
            Paragraph(str(r.get("sources") or "-")[:42], small),
            first_at.strftime("%d-%m-%Y %H:%M") if first_at else "-",
            last_at.strftime("%d-%m-%Y %H:%M") if last_at else "-",
        ])
    table = LongTable(data, repeatRows=1, colWidths=[24, 120, 110, 90, 120, 105, 105])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1F2937")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,-1), 7),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("GRID", (0,0), (-1,-1), 0.25, colors.HexColor("#D1D5DB")),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#F7F7F7")]),
        ("LEFTPADDING", (0,0), (-1,-1), 4),
        ("RIGHTPADDING", (0,0), (-1,-1), 4),
        ("TOPPADDING", (0,0), (-1,-1), 3),
        ("BOTTOMPADDING", (0,0), (-1,-1), 3),
    ]))
    story.append(table)
    doc.build(story)
    buf.seek(0)
    buf.name = f"BETROXY_User_Mobile_Report_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.pdf"
    return buf, len(rows), mobile_count


async def _send_mobile_report(target_message):
    pdf, total, mobiles = _mobile_report_pdf()
    await target_message.reply_document(
        document=pdf,
        filename=pdf.name,
        caption=(
            f"📱 BETROXY User Mobile Report\n"
            f"Known users: {total}\n"
            f"Mobile explicitly shared: {mobiles}\n"
            f"Not shared: {total-mobiles}"
        ),
    )


class _MessageProxy:
    def __init__(self, original, text):
        self._original = original
        self.text = text
    def __getattr__(self, name):
        return getattr(self._original, name)


class _UpdateProxy:
    def __init__(self, original, text):
        self._original = original
        self.effective_user = original.effective_user
        self.effective_message = _MessageProxy(original.effective_message, text)
        self.message = self.effective_message
    def __getattr__(self, name):
        return getattr(self._original, name)


async def _ai_command(update, context):
    user = update.effective_user
    msg = update.effective_message
    if not user or not msg:
        return
    if not bot.is_admin(user.id):
        await msg.reply_text(
            "🔒 The BETROXY admin assistant is available only to the configured admin account.\n"
            f"Your Telegram ID is <code>{user.id}</code>.",
            parse_mode=bot.ParseMode.HTML,
        )
        return
    raw = " ".join(getattr(context, "args", []) or []).strip()
    if not raw:
        raw = "ai help"
    bot.logger.warning("V89_AI_COMMAND uid=%s text=%s", user.id, raw[:180])
    low = raw.lower()
    if any(x in low for x in ("mobile report", "phone report", "user mobile", "contact report")):
        await _send_mobile_report(msg)
        return
    if any(x in low for x in ("reward center", "rewards center", "giftport status", "reward status")):
        await msg.reply_text(_reward_center_text(), parse_mode=bot.ParseMode.HTML, reply_markup=_reward_center_keyboard())
        return
    proxy = _UpdateProxy(update, raw)
    await v62.ai_admin_chat_handler(proxy, context)


async def v89_chat_handler(update, context):
    user = update.effective_user
    msg = update.effective_message
    text = str(getattr(msg, "text", "") or "").strip() if msg else ""
    low = text.lower()
    if msg and low == "not now":
        await msg.reply_text("No problem. You can share it later from 📱 Mobile for Rewards.", reply_markup=ReplyKeyboardRemove())
        return
    if user and msg and bot.is_admin(user.id):
        if any(x in low for x in ("mobile report", "phone report", "user mobile report", "contact report")):
            bot.logger.warning("V89_AI_TEXT_MATCH uid=%s action=mobile_report", user.id)
            await _send_mobile_report(msg)
            return
        if low in {"reward center", "rewards center", "giftport status", "reward status"}:
            bot.logger.warning("V89_AI_TEXT_MATCH uid=%s action=reward_center", user.id)
            await msg.reply_text(_reward_center_text(), parse_mode=bot.ParseMode.HTML, reply_markup=_reward_center_keyboard())
            return
        bot.logger.info("V89_AI_TEXT_PASS uid=%s text=%s", user.id, text[:180])
    return await _old_chat_handler(update, context)


async def v89_start(update, context):
    result = await _old_start(update, context)
    user = update.effective_user
    msg = update.effective_message
    uid = getattr(user, "id", None)
    args = list(getattr(context, "args", []) or [])
    payload = str(args[0]).lower() if args else ""
    if uid and msg and payload == "mobile":
        await _send_mobile_prompt(msg, uid)
    elif uid and msg and payload == "freequiz":
        row = _mobile_row(uid) or {}
        if not row.get("mobile_number") and not row.get("mobile_prompted_at"):
            await _send_mobile_prompt(msg, uid)
    return result


async def v89_business_message_update(update, context):
    msg = update.business_message
    if msg and getattr(msg, "contact", None) and getattr(msg, "from_user", None):
        c = msg.contact
        if getattr(c, "user_id", None) and int(c.user_id) == int(msg.from_user.id):
            row = _save_mobile(msg.from_user.id, getattr(c, "phone_number", ""), "business_contact")
            if row:
                bot.logger.warning("V89_BUSINESS_MOBILE_CAPTURED uid=%s", msg.from_user.id)
    return await _old_business_message_update(update, context)


async def v89_callback_handler(update, context):
    q = update.callback_query
    if not q:
        return await _old_callback_handler(update, context)
    data = str(q.data or "")
    uid = q.from_user.id

    if data == "v89_mobile":
        await q.answer()
        row = _mobile_row(uid) or {}
        if row.get("mobile_number"):
            await q.message.reply_text(
                f"📱 <b>Your saved mobile</b>\n\n<code>{row['mobile_number']}</code>\n\n"
                "This is used only for rewards/support/account matching.",
                parse_mode=bot.ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🗑 Remove My Mobile", callback_data="v89_mobile_remove")]]),
            )
        else:
            await _send_mobile_prompt(q.message, uid)
        return

    if data == "v89_mobile_remove":
        _remove_mobile(uid)
        await q.answer("Mobile removed", show_alert=True)
        await q.message.reply_text("✅ Your saved mobile number has been removed.")
        return

    if data == "v89_my_rewards":
        await q.answer()
        await q.message.reply_text(_my_rewards_text(uid), parse_mode=bot.ParseMode.HTML, disable_web_page_preview=True)
        return

    if data == "v89_reward_center":
        if not bot.is_admin(uid):
            await q.answer("Admin only", show_alert=True)
            return
        await q.answer()
        await q.message.reply_text(_reward_center_text(), parse_mode=bot.ParseMode.HTML, reply_markup=_reward_center_keyboard())
        return

    if data == "v89_mobile_report":
        if not bot.is_admin(uid):
            await q.answer("Admin only", show_alert=True)
            return
        await q.answer("Preparing PDF...")
        await _send_mobile_report(q.message)
        return

    if data == "v89_reward_toggle_program":
        if not bot.is_admin(uid):
            await q.answer("Admin only", show_alert=True)
            return
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE reward_settings SET program_enabled=NOT program_enabled,updated_at=NOW() WHERE id=1")
            conn.commit()
        await q.answer("Updated")
        await q.message.reply_text(_reward_center_text(), parse_mode=bot.ParseMode.HTML, reply_markup=_reward_center_keyboard())
        return

    if data == "v89_reward_toggle_issue":
        if not bot.is_admin(uid):
            await q.answer("Admin only", show_alert=True)
            return
        if not _giftport_ready():
            await q.answer("Giftport API is not configured yet.", show_alert=True)
            return
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE reward_settings SET auto_issue_enabled=NOT auto_issue_enabled,updated_at=NOW() WHERE id=1")
            conn.commit()
        await q.answer("Updated")
        await q.message.reply_text(_reward_center_text(), parse_mode=bot.ParseMode.HTML, reply_markup=_reward_center_keyboard())
        return

    if data == "v89_reward_settle":
        if not bot.is_admin(uid):
            await q.answer("Admin only", show_alert=True)
            return
        n = _prepare_weekly_awards(force=True)
        await q.answer(f"Prepared {n} new award(s)", show_alert=True)
        await q.message.reply_text(_reward_center_text(), parse_mode=bot.ParseMode.HTML, reply_markup=_reward_center_keyboard())
        return

    return await _old_callback_handler(update, context)


def _v89_worker_cycle(force=False):
    _old_worker_cycle(force=force)
    try:
        _prepare_weekly_awards(force=False)
    except Exception:
        bot.logger.exception("V89_REWARD_PREPARE_FAILED")


def _v89_add_handler(self, handler, group=0):
    app_key = id(self)
    if app_key not in _installed_application_ids:
        _installed_application_ids.add(app_key)
        _old_application_add_handler(self, CommandHandler("ai", _ai_command), group=-1)
        _old_application_add_handler(self, CommandHandler("ai_status", _ai_command), group=-1)
        _old_application_add_handler(self, MessageHandler(filters.CONTACT & ~filters.UpdateType.BUSINESS_MESSAGE, _official_contact_handler), group=-1)
    return _old_application_add_handler(self, handler, group=group)


try:
    _ensure_v89_schema()
    bot.logger.warning("V89_SCHEMA ready=on rewards=on mobile_consent=on")
except Exception:
    bot.logger.exception("V89_SCHEMA_FAILED")

# Runtime patches
bot.start = v89_start
bot.callback_handler = v89_callback_handler
bot.chat_handler = v89_chat_handler
bot.public_menu = v89_public_menu
bot.admin_menu = v89_admin_menu

# Patch module globals that later wrappers call dynamically.
v83.v53.v53_public_menu = v89_public_menu
v88.v88_public_menu = v89_public_menu
v88.v88_business_menu = v89_business_menu
v83.v75._business_menu = v89_business_menu
v83.v78.business_main_menu = v89_business_menu
v49._business_message_update = v89_business_message_update
v83._worker_cycle = _v89_worker_cycle
Application.add_handler = _v89_add_handler

bot.logger.warning(
    "V89_REWARDS_MOBILE_AI active=on currency=INR giftport_adapter=prepared live_issue=fail_closed "
    "mobile_report_pdf=admin_only explicit_contact_only=on ai_slash_command=on"
)

if __name__ == "__main__":
    v88.v63.apply_signup_cta()
    v85._enable_smart_reply_without_reset()
    threading.Thread(target=v83._worker_loop, name="betroxy-engagement-worker", daemon=True).start()
    bot.logger.warning("V89 polling handover delay=12s")
    time.sleep(12)
    bot.main()
