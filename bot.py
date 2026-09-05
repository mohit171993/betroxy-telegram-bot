import os
import csv
import io
import logging
import secrets
import re
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing")
if not ADMIN_ID:
    raise RuntimeError("ADMIN_ID is missing")

BOT_USERNAME = "BetroxyOfficialBot"

# Public Betroxy links
PLAY_BOT_URL = "https://t.me/BetroxyBot"
CASINO_URL = "https://t.me/BetroxyBot/casino"
SPORTSBOOK_URL = "https://t.me/BetroxyBot/sportsbook"
EXCHANGE_URL = "https://t.me/BetroxyBot/exchange"
POPULAR_GAMES_URL = "https://t.me/BetroxyBot/populargames"
CRASH_GAMES_URL = "https://t.me/BetroxyBot/crashgames"

PROMOTIONS_URL = "https://betroxy.com/promotions"
VIP_URL = "https://betroxy.com/vip-club"
RESPONSIBLE_URL = "https://betroxy.com/responsible-gambling"
UPDATES_URL = "https://t.me/betroxycasino"
TELEGRAM_SUPPORT_URL = "https://t.me/betroxysports"
WHATSAPP_SUPPORT_URL = (
    "https://api.whatsapp.com/send/"
    "?phone=447777352382"
    "&text=Hi%2C+I+need+support"
    "&type=phone_number"
    "&app_absent=0"
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Conversation states
ADD_NAME, ADD_CODE, ADD_RATE = range(3)
SEARCH_CODE = 10
EDIT_NAME = 20
EDIT_RATE = 21
EDIT_CODE = 22
EDIT_URL = 23


# ============================================================
# DATABASE
# ============================================================

def get_db():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def init_db():
    """
    Creates tables if missing and safely upgrades the existing Phase 1 database.
    Existing affiliates/referrals are preserved.
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS agents (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    code TEXT UNIQUE NOT NULL,
                    commission_rate NUMERIC(8,4) DEFAULT 0,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS referrals (
                    id SERIAL PRIMARY KEY,
                    telegram_user_id BIGINT UNIQUE NOT NULL,
                    telegram_username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    agent_id INTEGER REFERENCES agents(id),
                    start_payload TEXT,
                    joined_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            # Safe migration for affiliate self-service dashboard
            cur.execute("""
                ALTER TABLE agents
                ADD COLUMN IF NOT EXISTS telegram_user_id BIGINT
            """)
            cur.execute("""
                ALTER TABLE agents
                ADD COLUMN IF NOT EXISTS claim_token TEXT
            """)
            cur.execute("""
                ALTER TABLE agents
                ADD COLUMN IF NOT EXISTS custom_url TEXT
            """)
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_agents_telegram_user_id
                ON agents(telegram_user_id)
                WHERE telegram_user_id IS NOT NULL
            """)
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_agents_claim_token
                ON agents(claim_token)
                WHERE claim_token IS NOT NULL
            """)

        conn.commit()


# ============================================================
# DATABASE HELPERS
# ============================================================

def find_agent_by_code(code):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM agents WHERE LOWER(code)=LOWER(%s) LIMIT 1",
                (code,),
            )
            return cur.fetchone()


def find_agent_by_telegram_user_id(user_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM agents WHERE telegram_user_id=%s LIMIT 1",
                (user_id,),
            )
            return cur.fetchone()


def find_agent_by_claim_token(token):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM agents WHERE claim_token=%s LIMIT 1",
                (token,),
            )
            return cur.fetchone()


def create_agent(name, code, commission_rate):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agents (name, code, commission_rate, is_active)
                VALUES (%s, %s, %s, TRUE)
                RETURNING *
                """,
                (name, code.lower(), commission_rate),
            )
            agent = cur.fetchone()
            conn.commit()
            return agent


def update_agent_name(code, new_name):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE agents
                SET name=%s
                WHERE LOWER(code)=LOWER(%s)
                RETURNING *
                """,
                (new_name, code),
            )
            agent = cur.fetchone()
            conn.commit()
            return agent



def update_agent_code(old_code, new_code):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE agents
                SET code=%s
                WHERE LOWER(code)=LOWER(%s)
                RETURNING *
                """,
                (new_code.lower(), old_code),
            )
            agent = cur.fetchone()
            conn.commit()
            return agent


def update_agent_url(code, custom_url):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE agents
                SET custom_url=%s
                WHERE LOWER(code)=LOWER(%s)
                RETURNING *
                """,
                (custom_url or None, code),
            )
            agent = cur.fetchone()
            conn.commit()
            return agent


def delete_agent_permanently(code):
    """
    Permanently deletes the affiliate and all Telegram referral rows attributed
    to that affiliate. This cannot be undone.
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM agents WHERE LOWER(code)=LOWER(%s) FOR UPDATE",
                (code,),
            )
            agent = cur.fetchone()
            if not agent:
                return None, 0

            cur.execute(
                "SELECT COUNT(*) AS n FROM referrals WHERE agent_id=%s",
                (agent["id"],),
            )
            referral_count = cur.fetchone()["n"]

            cur.execute(
                "DELETE FROM referrals WHERE agent_id=%s",
                (agent["id"],),
            )
            cur.execute(
                "DELETE FROM agents WHERE id=%s",
                (agent["id"],),
            )
            conn.commit()
            return agent, referral_count


def update_agent_rate(code, rate):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE agents
                SET commission_rate=%s
                WHERE LOWER(code)=LOWER(%s)
                RETURNING *
                """,
                (rate, code),
            )
            agent = cur.fetchone()
            conn.commit()
            return agent


def set_agent_status(code, status):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE agents
                SET is_active=%s
                WHERE LOWER(code)=LOWER(%s)
                RETURNING *
                """,
                (status, code),
            )
            agent = cur.fetchone()
            conn.commit()
            return agent


def generate_claim_token(code):
    token = secrets.token_urlsafe(18)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE agents
                SET claim_token=%s
                WHERE LOWER(code)=LOWER(%s)
                RETURNING *
                """,
                (token, code),
            )
            agent = cur.fetchone()
            conn.commit()
            return agent, token


def bind_agent_account(token, telegram_user_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE agents
                SET telegram_user_id=%s,
                    claim_token=NULL
                WHERE claim_token=%s
                RETURNING *
                """,
                (telegram_user_id, token),
            )
            agent = cur.fetchone()
            conn.commit()
            return agent


def unlink_agent_account(code):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE agents
                SET telegram_user_id=NULL,
                    claim_token=NULL
                WHERE LOWER(code)=LOWER(%s)
                RETURNING *
                """,
                (code,),
            )
            agent = cur.fetchone()
            conn.commit()
            return agent


def save_referral(user, agent, payload):
    """
    First-touch attribution:
    - each Telegram user is stored once
    - opening another affiliate link later does not overwrite attribution
    - an affiliate opening their own referral link does not count themselves
    """
    if agent and agent.get("telegram_user_id") == user.id:
        return None, False

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM referrals WHERE telegram_user_id=%s",
                (user.id,),
            )
            existing = cur.fetchone()
            if existing:
                return existing, False

            cur.execute(
                """
                INSERT INTO referrals (
                    telegram_user_id,
                    telegram_username,
                    first_name,
                    last_name,
                    agent_id,
                    start_payload,
                    joined_at
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                RETURNING *
                """,
                (
                    user.id,
                    user.username,
                    user.first_name,
                    user.last_name,
                    agent["id"] if agent else None,
                    payload,
                    datetime.now(timezone.utc),
                ),
            )
            referral = cur.fetchone()
            conn.commit()
            return referral, True


def get_agent_stats(code):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM agents WHERE LOWER(code)=LOWER(%s)",
                (code,),
            )
            agent = cur.fetchone()
            if not agent:
                return None

            cur.execute(
                "SELECT COUNT(*) AS total FROM referrals WHERE agent_id=%s",
                (agent["id"],),
            )
            total = cur.fetchone()["total"]

            cur.execute(
                """
                SELECT COUNT(*) AS today
                FROM referrals
                WHERE agent_id=%s
                  AND joined_at >= DATE_TRUNC('day', NOW())
                """,
                (agent["id"],),
            )
            today = cur.fetchone()["today"]

            cur.execute(
                """
                SELECT COUNT(*) AS week
                FROM referrals
                WHERE agent_id=%s
                  AND joined_at >= NOW() - INTERVAL '7 days'
                """,
                (agent["id"],),
            )
            week = cur.fetchone()["week"]

            cur.execute(
                """
                SELECT COUNT(*) AS month
                FROM referrals
                WHERE agent_id=%s
                  AND joined_at >= NOW() - INTERVAL '30 days'
                """,
                (agent["id"],),
            )
            month = cur.fetchone()["month"]

            return {
                "agent": agent,
                "total": total,
                "today": today,
                "week": week,
                "month": month,
            }


def get_agent_users(code, limit=20):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM agents WHERE LOWER(code)=LOWER(%s)",
                (code,),
            )
            agent = cur.fetchone()
            if not agent:
                return None

            cur.execute(
                """
                SELECT *
                FROM referrals
                WHERE agent_id=%s
                ORDER BY joined_at DESC
                LIMIT %s
                """,
                (agent["id"], limit),
            )
            return cur.fetchall()


def list_agents(limit=50):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    a.*,
                    COUNT(r.id) AS referral_count
                FROM agents a
                LEFT JOIN referrals r ON r.agent_id=a.id
                GROUP BY a.id
                ORDER BY referral_count DESC, a.created_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            return cur.fetchall()


def overall_report():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM agents")
            total_agents = cur.fetchone()["n"]

            cur.execute("SELECT COUNT(*) AS n FROM agents WHERE is_active=TRUE")
            active_agents = cur.fetchone()["n"]

            cur.execute("SELECT COUNT(*) AS n FROM referrals WHERE agent_id IS NOT NULL")
            total_referrals = cur.fetchone()["n"]

            cur.execute("""
                SELECT COUNT(*) AS n
                FROM referrals
                WHERE agent_id IS NOT NULL
                  AND joined_at >= DATE_TRUNC('day', NOW())
            """)
            today = cur.fetchone()["n"]

            cur.execute("""
                SELECT COUNT(*) AS n
                FROM referrals
                WHERE agent_id IS NOT NULL
                  AND joined_at >= NOW() - INTERVAL '7 days'
            """)
            week = cur.fetchone()["n"]

            cur.execute("""
                SELECT a.name, a.code, COUNT(r.id) AS c
                FROM agents a
                LEFT JOIN referrals r ON r.agent_id=a.id
                GROUP BY a.id
                ORDER BY c DESC
                LIMIT 1
            """)
            top = cur.fetchone()

            return {
                "total_agents": total_agents,
                "active_agents": active_agents,
                "total_referrals": total_referrals,
                "today": today,
                "week": week,
                "top": top,
            }


def export_rows():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    a.name AS agent_name,
                    a.code AS agent_code,
                    a.commission_rate,
                    a.is_active,
                    r.telegram_user_id,
                    r.telegram_username,
                    r.first_name,
                    r.last_name,
                    r.joined_at
                FROM referrals r
                LEFT JOIN agents a ON a.id=r.agent_id
                WHERE r.agent_id IS NOT NULL
                ORDER BY r.joined_at DESC
            """)
            return cur.fetchall()


# ============================================================
# MENUS
# ============================================================

def public_menu(user_id=None):
    rows = [
        [InlineKeyboardButton("🎮 Play on Betroxy", url=PLAY_BOT_URL)],
        [
            InlineKeyboardButton("🎰 Casino", url=CASINO_URL),
            InlineKeyboardButton("⚽ Sportsbook", url=SPORTSBOOK_URL),
        ],
        [
            InlineKeyboardButton("🔄 Exchange", url=EXCHANGE_URL),
            InlineKeyboardButton("🔥 Popular Games", url=POPULAR_GAMES_URL),
        ],
        [
            InlineKeyboardButton("🚀 Crash Games", url=CRASH_GAMES_URL),
            InlineKeyboardButton("🎁 Promotions", url=PROMOTIONS_URL),
        ],
        [
            InlineKeyboardButton("👑 VIP Club", url=VIP_URL),
            InlineKeyboardButton("📢 Updates", url=UPDATES_URL),
        ],
        [
            InlineKeyboardButton("💬 Support", callback_data="support"),
            InlineKeyboardButton("🛡 Responsible Play", url=RESPONSIBLE_URL),
        ],
    ]

    if user_id and find_agent_by_telegram_user_id(user_id):
        rows.insert(0, [
            InlineKeyboardButton("📈 My Affiliate Performance", callback_data="affiliate_home")
        ])

    if user_id == ADMIN_ID:
        rows.insert(0, [
            InlineKeyboardButton("🛠 Admin Panel", callback_data="admin_home")
        ])

    return InlineKeyboardMarkup(rows)


def support_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Telegram Support", url=TELEGRAM_SUPPORT_URL)],
        [InlineKeyboardButton("🟢 WhatsApp Support", url=WHATSAPP_SUPPORT_URL)],
        [InlineKeyboardButton("⬅️ Main Menu", callback_data="home")],
    ])


def admin_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👥 Affiliates", callback_data="admin_agents"),
            InlineKeyboardButton("➕ Add Affiliate", callback_data="admin_add"),
        ],
        [
            InlineKeyboardButton("📊 Overall Report", callback_data="admin_report"),
            InlineKeyboardButton("🔎 Search Affiliate", callback_data="admin_search"),
        ],
        [
            InlineKeyboardButton("📥 Export CSV", callback_data="admin_export"),
        ],
        [
            InlineKeyboardButton("🏠 Public Menu", callback_data="home"),
        ],
    ])


def agent_action_menu(code, active=True):
    status_button = (
        InlineKeyboardButton("⛔ Disable", callback_data=f"agent_disable:{code}")
        if active
        else InlineKeyboardButton("✅ Enable", callback_data=f"agent_enable:{code}")
    )

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✏️ Name", callback_data=f"agent_edit_name:{code}"),
            InlineKeyboardButton("🆔 Code", callback_data=f"agent_edit_code:{code}"),
        ],
        [
            InlineKeyboardButton("💰 Commission %", callback_data=f"agent_edit_rate:{code}"),
            InlineKeyboardButton("🔗 URL", callback_data=f"agent_edit_url:{code}"),
        ],
        [
            InlineKeyboardButton("👥 View Users", callback_data=f"agent_users:{code}"),
            status_button,
        ],
        [
            InlineKeyboardButton("🔐 Affiliate Access", callback_data=f"agent_access:{code}"),
            InlineKeyboardButton("🔓 Unlink Account", callback_data=f"agent_unlink:{code}"),
        ],
        [
            InlineKeyboardButton("🗑 Permanent Delete", callback_data=f"agent_delete_confirm:{code}"),
        ],
        [
            InlineKeyboardButton("⬅️ Affiliates", callback_data="admin_agents"),
            InlineKeyboardButton("🏠 Admin", callback_data="admin_home"),
        ],
    ])


def affiliate_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 Refresh", callback_data="affiliate_home"),
            InlineKeyboardButton("🔗 My Referral Link", callback_data="affiliate_link"),
        ],
        [
            InlineKeyboardButton("🏠 Betroxy Menu", callback_data="home"),
        ],
    ])


# ============================================================
# AUTH
# ============================================================

def is_admin(user_id):
    return user_id == ADMIN_ID


async def require_admin(update):
    if not update.effective_user or not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("❌ Admin access required.")
        return False
    return True


# ============================================================
# TEXT HELPERS
# ============================================================

def affiliate_report_text(stats):
    a = stats["agent"]
    status = "✅ Active" if a["is_active"] else "⛔ Inactive"
    return (
        "📈 <b>My Affiliate Performance</b>\n\n"
        f"Name: {a['name']}\n"
        f"Code: <code>{a['code']}</code>\n"
        f"Status: {status}\n"
        f"Commission Rate: {a['commission_rate']}%\n\n"
        f"👥 Total Referrals: {stats['total']}\n"
        f"🆕 Today: {stats['today']}\n"
        f"📅 Last 7 Days: {stats['week']}\n"
        f"🗓 Last 30 Days: {stats['month']}\n\n"
        "ℹ️ Registration, deposit and actual commission earnings "
        "will appear after Betroxy backend integration."
    )


# ============================================================
# PUBLIC START
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    payload = context.args[0].strip() if context.args else None

    # Affiliate account claim link
    if payload and payload.startswith("claim_"):
        token = payload[6:]
        agent = find_agent_by_claim_token(token)

        if not agent:
            await update.message.reply_text(
                "❌ This affiliate access link is invalid or has already been used."
            )
            return

        try:
            bound = bind_agent_account(token, user.id)
        except psycopg.errors.UniqueViolation:
            await update.message.reply_text(
                "❌ This Telegram account is already linked to another affiliate."
            )
            return

        await update.message.reply_text(
            "✅ <b>Affiliate Dashboard Activated</b>\n\n"
            f"Welcome {bound['name']}.\n"
            "You can now view your performance directly in this bot.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📈 View My Performance", callback_data="affiliate_home")]
            ]),
        )
        return

    # Public referral attribution
    agent = None
    if payload and payload.lower().startswith("agent_"):
        code = payload[6:]
        agent = find_agent_by_code(code)
        if agent and not agent["is_active"]:
            agent = None

    _, created = save_referral(user, agent, payload)

    if created and agent:
        logger.info("New referral user=%s agent=%s", user.id, agent["code"])

    await update.message.reply_text(
        "👑 <b>Welcome to Betroxy Official</b>\n\n"
        "Discover games, promotions, VIP rewards, latest updates and official support.\n\n"
        "Choose an option below 👇",
        parse_mode=ParseMode.HTML,
        reply_markup=public_menu(user.id),
        disable_web_page_preview=True,
    )


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update):
        return

    await update.message.reply_text(
        "🛠 <b>Betroxy Affiliate Admin</b>\n\nChoose an option:",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_menu(),
    )


async def affiliate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    agent = find_agent_by_telegram_user_id(update.effective_user.id)
    if not agent:
        await update.message.reply_text(
            "You do not have an affiliate dashboard linked to this Telegram account."
        )
        return

    stats = get_agent_stats(agent["code"])
    await update.message.reply_text(
        affiliate_report_text(stats),
        parse_mode=ParseMode.HTML,
        reply_markup=affiliate_menu(),
    )


# ============================================================
# ADMIN LEGACY COMMANDS - still supported
# ============================================================

async def agent_rate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /agent_rate code new_rate")
        return
    code = context.args[0]
    try:
        rate = float(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Rate must be a number.")
        return
    if not 0 <= rate <= 100:
        await update.message.reply_text("❌ Rate must be between 0 and 100.")
        return
    a = update_agent_rate(code, rate)
    if not a:
        await update.message.reply_text("❌ Agent not found.")
        return
    await update.message.reply_text(
        f"✅ {a['name']} commission changed to {a['commission_rate']}%."
    )


async def agent_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /agent code")
        return
    stats = get_agent_stats(context.args[0])
    if not stats:
        await update.message.reply_text("❌ Agent not found.")
        return
    a = stats["agent"]
    await update.message.reply_text(
        f"📊 <b>{a['name']}</b>\n\n"
        f"Code: <code>{a['code']}</code>\n"
        f"Commission: {a['commission_rate']}%\n"
        f"Status: {'Active' if a['is_active'] else 'Inactive'}\n"
        f"Total referrals: {stats['total']}\n"
        f"Today: {stats['today']}\n"
        f"Last 7 days: {stats['week']}\n"
        f"Last 30 days: {stats['month']}",
        parse_mode=ParseMode.HTML,
    )


async def agent_access_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /agent_access code")
        return

    agent, token = generate_claim_token(context.args[0])
    if not agent:
        await update.message.reply_text("❌ Agent not found.")
        return

    link = f"https://t.me/{BOT_USERNAME}?start=claim_{token}"

    await update.message.reply_text(
        "🔐 <b>Affiliate Dashboard Access Link</b>\n\n"
        f"Affiliate: {agent['name']}\n"
        f"Code: <code>{agent['code']}</code>\n\n"
        "Send this private one-time link only to the affiliate:\n"
        f"<code>{link}</code>\n\n"
        "After they open it, their Telegram account will be linked to this affiliate.",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )



# ============================================================
# BULK INSTAGRAM AFFILIATE CREATION
# ============================================================

INSTAGRAM_AFFILIATES = [
    ("fakt_cricket_memes", "faktcricket"),
    ("ritikwins", "ritikwins"),
    ("theankuedit", "theankuedit"),
    ("5wides", "5wides"),
    ("cricket.official10", "cricketofficial10"),
    ("bharath._editss", "bharatheditss"),
    ("ryuzakiii.exeeeeee", "ryuzakiiiexe"),
    ("cric__master18", "cricmaster18"),
    ("akash_mahi0007", "akashmahi0007"),
    ("cricysaakir2.0", "cricysaakir20"),
    ("ishankishan32_", "ishankishan32"),
    ("rsnreel", "rsnreel"),
    ("fahadcricketreviews", "fahadcricket"),
    ("cricsays", "cricsays"),
    ("saketeditt", "saketeditt"),
    ("rohit_sharma_status._45", "rohitstatus45"),
    ("official_bobby_4uhh_", "officialbobby4"),
    ("surat_tennis_cricket_", "surattennis"),
    ("cricket_exeee", "cricketexeee"),
    ("smriti_jemi_lovers", "smritijemi"),
    ("maxxo_editz_45", "maxxoeditz45"),
    ("rohit_sharma_.status_king", "rohitstatusking"),
    ("hitman_cha_diwana___45", "hitmandiwana45"),
    ("rishabh_dines17", "rishabhdines17"),
    ("csk_marathi_status_2.0", "cskmarathi20"),
    ("virat.kohli.marathi.status", "viratkohlitheme"),
    ("mahi.lifetime", "mahilifetime"),
]


async def create_instagram_affiliates(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not await require_admin(update):
        return

    created = []
    existing = []
    failed = []

    for name, code in INSTAGRAM_AFFILIATES:
        try:
            current = find_agent_by_code(code)
            if current:
                existing.append(code)
                continue

            create_agent(name, code, 0)
            created.append(code)

        except Exception as exc:
            logger.exception("Bulk affiliate create failed for %s", code)
            failed.append(code)

    lines = [
        "✅ <b>Instagram Affiliate Bulk Setup Complete</b>",
        "",
        f"Created: {len(created)}",
        f"Already Existing: {len(existing)}",
        f"Failed: {len(failed)}",
    ]

    if created:
        lines.append("")
        lines.append("<b>Created Codes:</b>")
        lines.extend([f"• <code>{c}</code>" for c in created])

    if existing:
        lines.append("")
        lines.append("<b>Already Existing:</b>")
        lines.extend([f"• <code>{c}</code>" for c in existing])

    if failed:
        lines.append("")
        lines.append("<b>Failed:</b>")
        lines.extend([f"• <code>{c}</code>" for c in failed])

    lines.append("")
    lines.append("All new affiliates were created with 0% commission.")

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML
    )


# ============================================================
# CALLBACK HANDLER
# ============================================================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "home":
        await q.message.reply_text(
            "👑 <b>Betroxy Official</b>\n\nChoose an option below 👇",
            parse_mode=ParseMode.HTML,
            reply_markup=public_menu(q.from_user.id),
        )
        return

    if data == "support":
        await q.message.reply_text(
            "💬 <b>Betroxy Support</b>\n\nChoose your preferred support channel:",
            parse_mode=ParseMode.HTML,
            reply_markup=support_menu(),
        )
        return

    if data == "affiliate_home":
        agent = find_agent_by_telegram_user_id(q.from_user.id)
        if not agent:
            await q.message.reply_text("❌ Affiliate dashboard not linked.")
            return
        stats = get_agent_stats(agent["code"])
        await q.message.reply_text(
            affiliate_report_text(stats),
            parse_mode=ParseMode.HTML,
            reply_markup=affiliate_menu(),
        )
        return

    if data == "affiliate_link":
        agent = find_agent_by_telegram_user_id(q.from_user.id)
        if not agent:
            await q.message.reply_text("❌ Affiliate dashboard not linked.")
            return
        link = f"https://t.me/{BOT_USERNAME}?start=agent_{agent['code']}"
        await q.message.reply_text(
            "🔗 <b>Your Referral Link</b>\n\n"
            f"<code>{link}</code>",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        return

    # Admin-only callbacks below
    if not is_admin(q.from_user.id):
        await q.message.reply_text("❌ Admin access required.")
        return

    if data == "admin_home":
        await q.message.reply_text(
            "🛠 <b>Betroxy Affiliate Admin</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_menu(),
        )
        return

    if data == "admin_agents":
        agents = list_agents()
        if not agents:
            await q.message.reply_text(
                "No affiliates created yet.",
                reply_markup=admin_menu(),
            )
            return

        rows = []
        for a in agents[:20]:
            status = "✅" if a["is_active"] else "⛔"
            linked = "🔐" if a.get("telegram_user_id") else ""
            label = f"{status}{linked} {a['name']} • {a['referral_count']} users"
            rows.append([
                InlineKeyboardButton(
                    label,
                    callback_data=f"agent_view:{a['code']}",
                )
            ])
        rows.append([InlineKeyboardButton("🏠 Admin", callback_data="admin_home")])

        await q.message.reply_text(
            "👥 <b>Affiliates</b>\n\nTap an affiliate:",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    if data == "admin_report":
        r = overall_report()
        top_text = "None yet"
        if r["top"]:
            top_text = f"{r['top']['name']} ({r['top']['code']}) • {r['top']['c']} users"

        await q.message.reply_text(
            "📊 <b>Overall Affiliate Report</b>\n\n"
            f"👥 Total Affiliates: {r['total_agents']}\n"
            f"✅ Active Affiliates: {r['active_agents']}\n"
            f"👤 Total Referred Users: {r['total_referrals']}\n"
            f"🆕 Referrals Today: {r['today']}\n"
            f"📅 Referrals Last 7 Days: {r['week']}\n"
            f"🏆 Top Affiliate: {top_text}",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_menu(),
        )
        return

    if data == "admin_export":
        rows = export_rows()
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow([
            "Agent Name", "Agent Code", "Commission %", "Active",
            "Telegram User ID", "Username", "First Name", "Last Name", "Joined At"
        ])
        for r in rows:
            writer.writerow([
                r["agent_name"], r["agent_code"], r["commission_rate"], r["is_active"],
                r["telegram_user_id"], r["telegram_username"], r["first_name"],
                r["last_name"], r["joined_at"]
            ])

        bio = io.BytesIO(out.getvalue().encode("utf-8-sig"))
        bio.name = f"betroxy_affiliate_report_{datetime.now().date()}.csv"

        await q.message.reply_document(
            document=bio,
            caption="📥 Betroxy affiliate referral report",
        )
        return

    if data.startswith("agent_view:"):
        code = data.split(":", 1)[1]
        stats = get_agent_stats(code)
        if not stats:
            await q.message.reply_text("❌ Agent not found.")
            return

        a = stats["agent"]
        link = f"https://t.me/{BOT_USERNAME}?start=agent_{a['code']}"
        linked = "Yes" if a.get("telegram_user_id") else "No"

        await q.message.reply_text(
            "👤 <b>Affiliate Details</b>\n\n"
            f"Name: {a['name']}\n"
            f"Code: <code>{a['code']}</code>\n"
            f"Commission: {a['commission_rate']}%\n"
            f"Status: {'Active' if a['is_active'] else 'Inactive'}\n"
            f"Dashboard Linked: {linked}\n"
            f"Custom URL: {a.get('custom_url') or 'Not set'}\n\n"
            f"👥 Total Users: {stats['total']}\n"
            f"🆕 Today: {stats['today']}\n"
            f"📅 Last 7 Days: {stats['week']}\n"
            f"🗓 Last 30 Days: {stats['month']}\n\n"
            f"🔗 Referral Link:\n<code>{link}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=agent_action_menu(a["code"], a["is_active"]),
            disable_web_page_preview=True,
        )
        return

    if data.startswith("agent_users:"):
        code = data.split(":", 1)[1]
        users = get_agent_users(code)
        stats = get_agent_stats(code)
        if users is None or not stats:
            await q.message.reply_text("❌ Agent not found.")
            return
        if not users:
            await q.message.reply_text(
                "No referred users yet.",
                reply_markup=agent_action_menu(code, stats["agent"]["is_active"]),
            )
            return

        lines = ["👥 <b>Recent Referred Users</b>", ""]
        for u in users:
            username = f"@{u['telegram_username']}" if u["telegram_username"] else "No username"
            lines.append(
                f"• {u['first_name'] or 'Unknown'} | {username} | "
                f"<code>{u['telegram_user_id']}</code>"
            )

        await q.message.reply_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=agent_action_menu(code, stats["agent"]["is_active"]),
        )
        return

    if data.startswith("agent_disable:"):
        code = data.split(":", 1)[1]
        a = set_agent_status(code, False)
        if not a:
            await q.message.reply_text("❌ Agent not found.")
            return
        await q.message.reply_text(
            f"⛔ {a['name']} disabled.",
            reply_markup=agent_action_menu(code, False),
        )
        return

    if data.startswith("agent_enable:"):
        code = data.split(":", 1)[1]
        a = set_agent_status(code, True)
        if not a:
            await q.message.reply_text("❌ Agent not found.")
            return
        await q.message.reply_text(
            f"✅ {a['name']} enabled.",
            reply_markup=agent_action_menu(code, True),
        )
        return

    if data.startswith("agent_access:"):
        code = data.split(":", 1)[1]
        agent, token = generate_claim_token(code)
        if not agent:
            await q.message.reply_text("❌ Agent not found.")
            return
        link = f"https://t.me/{BOT_USERNAME}?start=claim_{token}"
        await q.message.reply_text(
            "🔐 <b>Private Affiliate Access Link</b>\n\n"
            f"Send this one-time link to {agent['name']}:\n\n"
            f"<code>{link}</code>\n\n"
            "When they open it, their Telegram account will be linked "
            "and they will get a My Affiliate Performance dashboard.",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        return

    if data.startswith("agent_unlink:"):
        code = data.split(":", 1)[1]
        a = unlink_agent_account(code)
        if not a:
            await q.message.reply_text("❌ Agent not found.")
            return
        await q.message.reply_text(
            f"🔓 Affiliate dashboard account unlinked for {a['name']}."
        )
        return



    if data.startswith("agent_delete_confirm:"):
        code_to_delete = data.split(":", 1)[1]
        stats = get_agent_stats(code_to_delete)
        if not stats:
            await q.message.reply_text("❌ Agent not found.")
            return

        a = stats["agent"]
        await q.message.reply_text(
            "⚠️ <b>PERMANENT DELETE</b>\n\n"
            f"Affiliate: <b>{a['name']}</b>\n"
            f"Code: <code>{a['code']}</code>\n"
            f"Referred Users: {stats['total']}\n\n"
            "This will permanently delete the affiliate AND all referral records "
            "assigned to this affiliate. This cannot be undone.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🗑 YES, DELETE PERMANENTLY",
                        callback_data=f"agent_delete_yes:{a['code']}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "❌ Cancel",
                        callback_data=f"agent_view:{a['code']}"
                    )
                ],
            ]),
        )
        return

    if data.startswith("agent_delete_yes:"):
        code_to_delete = data.split(":", 1)[1]
        deleted, referral_count = delete_agent_permanently(code_to_delete)
        if not deleted:
            await q.message.reply_text("❌ Agent not found.")
            return

        await q.message.reply_text(
            "🗑 <b>Affiliate Permanently Deleted</b>\n\n"
            f"Name: {deleted['name']}\n"
            f"Code: <code>{deleted['code']}</code>\n"
            f"Referral records deleted: {referral_count}\n\n"
            "This action cannot be undone.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_menu(),
        )
        return



# ============================================================
# ADD AFFILIATE CONVERSATION
# ============================================================

async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return ConversationHandler.END

    await q.message.reply_text(
        "➕ <b>Add Affiliate</b>\n\nEnter affiliate name:",
        parse_mode=ParseMode.HTML,
    )
    return ADD_NAME


async def add_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_agent_name"] = update.message.text.strip()
    await update.message.reply_text(
        "Enter affiliate code.\nExample: rahul\n\nUse letters, numbers or underscore only."
    )
    return ADD_CODE


async def add_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip().lower()

    if not re.fullmatch(r"[a-z0-9_]{2,40}", code):
        await update.message.reply_text(
            "❌ Invalid code. Use only letters, numbers or underscore (2-40 characters)."
        )
        return ADD_CODE

    if find_agent_by_code(code):
        await update.message.reply_text(
            "❌ This code already exists. Enter another code:"
        )
        return ADD_CODE

    context.user_data["new_agent_code"] = code
    await update.message.reply_text(
        "Enter commission rate.\nExample: 0, 5, 7.5, 10"
    )
    return ADD_RATE


async def add_rate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        rate = float(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Enter a valid number.")
        return ADD_RATE

    if not 0 <= rate <= 100:
        await update.message.reply_text("❌ Rate must be between 0 and 100.")
        return ADD_RATE

    agent = create_agent(
        context.user_data["new_agent_name"],
        context.user_data["new_agent_code"],
        rate,
    )

    link = f"https://t.me/{BOT_USERNAME}?start=agent_{agent['code']}"

    await update.message.reply_text(
        "✅ <b>Affiliate Created</b>\n\n"
        f"Name: {agent['name']}\n"
        f"Code: <code>{agent['code']}</code>\n"
        f"Commission: {agent['commission_rate']}%\n\n"
        f"Referral Link:\n<code>{link}</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=agent_action_menu(agent["code"], True),
        disable_web_page_preview=True,
    )

    context.user_data.clear()
    return ConversationHandler.END


# ============================================================
# SEARCH CONVERSATION
# ============================================================

async def search_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return ConversationHandler.END
    await q.message.reply_text("🔎 Enter affiliate code:")
    return SEARCH_CODE


async def search_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip()
    stats = get_agent_stats(code)

    if not stats:
        await update.message.reply_text(
            "❌ Affiliate not found.",
            reply_markup=admin_menu(),
        )
        return ConversationHandler.END

    a = stats["agent"]
    link = f"https://t.me/{BOT_USERNAME}?start=agent_{a['code']}"

    await update.message.reply_text(
        "👤 <b>Affiliate Details</b>\n\n"
        f"Name: {a['name']}\n"
        f"Code: <code>{a['code']}</code>\n"
        f"Commission: {a['commission_rate']}%\n"
        f"Status: {'Active' if a['is_active'] else 'Inactive'}\n"
        f"Custom URL: {a.get('custom_url') or 'Not set'}\n\n"
        f"👥 Total Users: {stats['total']}\n"
        f"🆕 Today: {stats['today']}\n"
        f"📅 Last 7 Days: {stats['week']}\n"
        f"🗓 Last 30 Days: {stats['month']}\n\n"
        f"🔗 Referral Link:\n<code>{link}</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=agent_action_menu(a["code"], a["is_active"]),
        disable_web_page_preview=True,
    )

    return ConversationHandler.END


# ============================================================
# EDIT NAME CONVERSATION
# ============================================================

async def edit_name_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return ConversationHandler.END

    code = q.data.split(":", 1)[1]
    context.user_data["edit_code"] = code

    await q.message.reply_text(
        f"✏️ Enter the new name for affiliate <code>{code}</code>:",
        parse_mode=ParseMode.HTML,
    )
    return EDIT_NAME


async def edit_name_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = context.user_data["edit_code"]
    new_name = update.message.text.strip()

    if not new_name:
        await update.message.reply_text("❌ Name cannot be empty.")
        return EDIT_NAME

    a = update_agent_name(code, new_name)

    await update.message.reply_text(
        f"✅ Affiliate name changed to <b>{a['name']}</b>.",
        parse_mode=ParseMode.HTML,
        reply_markup=agent_action_menu(code, a["is_active"]),
    )

    context.user_data.clear()
    return ConversationHandler.END



# ============================================================
# EDIT CODE CONVERSATION
# ============================================================

async def edit_code_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return ConversationHandler.END

    old_code = q.data.split(":", 1)[1]
    context.user_data["edit_code_old"] = old_code

    await q.message.reply_text(
        f"🆔 Enter new affiliate code for <code>{old_code}</code>:\n\n"
        "Use only letters, numbers or underscore.\n"
        "Changing the code also changes the public referral link.",
        parse_mode=ParseMode.HTML,
    )
    return EDIT_CODE


async def edit_code_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    old_code = context.user_data["edit_code_old"]
    new_code = update.message.text.strip().lower()

    if not re.fullmatch(r"[a-z0-9_]{2,40}", new_code):
        await update.message.reply_text(
            "❌ Invalid code. Use only letters, numbers or underscore (2-40 characters)."
        )
        return EDIT_CODE

    existing = find_agent_by_code(new_code)
    if existing and existing["code"].lower() != old_code.lower():
        await update.message.reply_text(
            "❌ This affiliate code already exists. Enter another code:"
        )
        return EDIT_CODE

    try:
        a = update_agent_code(old_code, new_code)
    except psycopg.errors.UniqueViolation:
        await update.message.reply_text(
            "❌ This affiliate code already exists. Enter another code:"
        )
        return EDIT_CODE

    if not a:
        await update.message.reply_text("❌ Agent not found.")
        context.user_data.clear()
        return ConversationHandler.END

    new_link = f"https://t.me/{BOT_USERNAME}?start=agent_{a['code']}"

    await update.message.reply_text(
        "✅ <b>Affiliate Code Updated</b>\n\n"
        f"Name: {a['name']}\n"
        f"New Code: <code>{a['code']}</code>\n"
        f"New Referral Link:\n<code>{new_link}</code>\n\n"
        "⚠️ The old referral link will no longer attribute new users.",
        parse_mode=ParseMode.HTML,
        reply_markup=agent_action_menu(a["code"], a["is_active"]),
        disable_web_page_preview=True,
    )

    context.user_data.clear()
    return ConversationHandler.END


# ============================================================
# EDIT URL CONVERSATION
# ============================================================

async def edit_url_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return ConversationHandler.END

    code = q.data.split(":", 1)[1]
    context.user_data["edit_url_code"] = code

    await q.message.reply_text(
        f"🔗 Enter custom URL for affiliate <code>{code}</code>.\n\n"
        "Example:\nhttps://www.betraxy.com/5wides\n\n"
        "Send <code>clear</code> to remove the custom URL.",
        parse_mode=ParseMode.HTML,
    )
    return EDIT_URL


async def edit_url_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = context.user_data["edit_url_code"]
    value = update.message.text.strip()

    if value.lower() == "clear":
        value = ""
    elif not re.fullmatch(r"https://[^\s]+", value):
        await update.message.reply_text(
            "❌ Enter a valid HTTPS URL, for example:\n"
            "https://www.betraxy.com/5wides\n\n"
            "Or send: clear"
        )
        return EDIT_URL

    a = update_agent_url(code, value)

    if not a:
        await update.message.reply_text("❌ Agent not found.")
        context.user_data.clear()
        return ConversationHandler.END

    await update.message.reply_text(
        "✅ <b>Affiliate URL Updated</b>\n\n"
        f"Name: {a['name']}\n"
        f"Code: <code>{a['code']}</code>\n"
        f"Custom URL: {a.get('custom_url') or 'Not set'}",
        parse_mode=ParseMode.HTML,
        reply_markup=agent_action_menu(a["code"], a["is_active"]),
        disable_web_page_preview=True,
    )

    context.user_data.clear()
    return ConversationHandler.END



# ============================================================
# EDIT RATE CONVERSATION
# ============================================================

async def edit_rate_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return ConversationHandler.END

    code = q.data.split(":", 1)[1]
    context.user_data["edit_code"] = code

    await q.message.reply_text(
        f"💰 Enter new commission % for <code>{code}</code>:",
        parse_mode=ParseMode.HTML,
    )
    return EDIT_RATE


async def edit_rate_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = context.user_data["edit_code"]

    try:
        rate = float(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Enter a valid number.")
        return EDIT_RATE

    if not 0 <= rate <= 100:
        await update.message.reply_text("❌ Rate must be between 0 and 100.")
        return EDIT_RATE

    a = update_agent_rate(code, rate)

    await update.message.reply_text(
        f"✅ {a['name']} commission changed to {a['commission_rate']}%.",
        reply_markup=agent_action_menu(code, a["is_active"]),
    )

    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.effective_message.reply_text(
        "Cancelled.",
        reply_markup=admin_menu() if is_admin(update.effective_user.id) else None,
    )
    return ConversationHandler.END


# ============================================================
# FALLBACK CHAT
# ============================================================

async def chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if is_admin(user_id):
        await update.message.reply_text(
            "🛠 <b>Admin Panel</b>\n\nTap a button below:",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_menu(),
        )
        return

    agent = find_agent_by_telegram_user_id(user_id)
    if agent:
        stats = get_agent_stats(agent["code"])
        await update.message.reply_text(
            affiliate_report_text(stats),
            parse_mode=ParseMode.HTML,
            reply_markup=affiliate_menu(),
        )
        return

    await update.message.reply_text(
        "👑 <b>Betroxy Official</b>\n\nChoose an option below 👇",
        parse_mode=ParseMode.HTML,
        reply_markup=public_menu(user_id),
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Bot error", exc_info=context.error)


# ============================================================
# MAIN
# ============================================================

def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    # Conversation handlers FIRST so their callback patterns take priority
    add_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(add_start, pattern=r"^admin_add$")
        ],
        states={
            ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_name)],
            ADD_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_code)],
            ADD_RATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_rate)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    search_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(search_start, pattern=r"^admin_search$")
        ],
        states={
            SEARCH_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, search_code)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    edit_name_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(edit_name_start, pattern=r"^agent_edit_name:")
        ],
        states={
            EDIT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_name_save)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    edit_code_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(edit_code_start, pattern=r"^agent_edit_code:")
        ],
        states={
            EDIT_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_code_save)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    edit_url_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(edit_url_start, pattern=r"^agent_edit_url:")
        ],
        states={
            EDIT_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_url_save)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    edit_rate_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(edit_rate_start, pattern=r"^agent_edit_rate:")
        ],
        states={
            EDIT_RATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_rate_save)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("affiliate", affiliate_command))
    app.add_handler(CommandHandler("agent", agent_stats_command))
    app.add_handler(CommandHandler("agent_rate", agent_rate_command))
    app.add_handler(CommandHandler("agent_access", agent_access_command))
    app.add_handler(CommandHandler("create_instagram_affiliates", create_instagram_affiliates))

    app.add_handler(add_conv)
    app.add_handler(search_conv)
    app.add_handler(edit_name_conv)
    app.add_handler(edit_code_conv)
    app.add_handler(edit_url_conv)
    app.add_handler(edit_rate_conv)

    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_handler))
    app.add_error_handler(error_handler)

    logger.info("Betroxy advanced affiliate bot starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
