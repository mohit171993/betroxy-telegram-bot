import os
import csv
import io
import logging
import re
import secrets
import hashlib
import zipfile
import mimetypes
import html
from urllib.parse import urlencode
from datetime import datetime, timezone
from threading import Thread

from flask import Flask, jsonify, request, redirect, Response

import psycopg
from psycopg.rows import dict_row

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonWebApp,
    Update,
    WebAppInfo,
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
TRACKER_API_SECRET = os.getenv("TRACKER_API_SECRET", "")
PORT = int(os.getenv("PORT", "8080"))
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://www.batraxy.com").rstrip("/")
BETROXY_BOT_URL = os.getenv("BETROXY_BOT_URL", "https://t.me/BetroxyBot")
BETROXY_WEB_URL = os.getenv("BETROXY_WEB_URL", "https://betroxy.com/")
THEME_UPLOAD_MAX_MB = int(os.getenv("THEME_UPLOAD_MAX_MB", "20"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing")
if not ADMIN_ID:
    raise RuntimeError("ADMIN_ID is missing")

BOT_USERNAME = "BetroxyOfficialBot"

# Exact Web App URL shown in BotFather for the original Betroxy bot
APP_URL = "https://betroxy.com/"

TELEGRAM_SUPPORT_URL = "https://t.me/betroxysports"
UPDATES_URL = "https://t.me/betroxycasino"
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

ADD_NAME, ADD_CODE, ADD_RATE = range(3)
SEARCH_CODE = 10
EDIT_NAME = 20
EDIT_RATE = 21
EDIT_CODE = 22
EDIT_URL = 23
CAMPAIGN_ADD_SINGLE = 30
CAMPAIGN_ADD_BULK = 31
CAMPAIGN_EDIT_USERNAME = 32
CAMPAIGN_EDIT_SLUG = 33
CAMPAIGN_EDIT_CODE = 34
THEME_UPLOAD = 40


# ============================================================
# DATABASE
# ============================================================

def get_db():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def init_db():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS agents (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    code TEXT UNIQUE NOT NULL,
                    commission_rate NUMERIC(8,4) DEFAULT 0,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
                """
            )

            cur.execute(
                """
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
                """
            )

            cur.execute(
                """
                ALTER TABLE agents
                ADD COLUMN IF NOT EXISTS telegram_user_id BIGINT
                """
            )
            cur.execute(
                """
                ALTER TABLE agents
                ADD COLUMN IF NOT EXISTS claim_token TEXT
                """
            )
            cur.execute(
                """
                ALTER TABLE agents
                ADD COLUMN IF NOT EXISTS custom_url TEXT
                """
            )

            cur.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_agents_telegram_user_id
                ON agents(telegram_user_id)
                WHERE telegram_user_id IS NOT NULL
                """
            )
            cur.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_agents_claim_token
                ON agents(claim_token)
                WHERE claim_token IS NOT NULL
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS landing_clicks (
                    id BIGSERIAL PRIMARY KEY,
                    slug TEXT NOT NULL,
                    agent_code TEXT NOT NULL,
                    ip_hash TEXT,
                    user_agent TEXT,
                    referer TEXT,
                    clicked_at TIMESTAMPTZ DEFAULT NOW()
                )
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_landing_clicks_agent_time
                ON landing_clicks(agent_code, clicked_at)
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS conversion_events (
                    id BIGSERIAL PRIMARY KEY,
                    agent_code TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    external_user_id TEXT,
                    amount NUMERIC(18,2) DEFAULT 0,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_conversion_events_agent_time
                ON conversion_events(agent_code, event_type, created_at)
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS campaign_links (
                    id BIGSERIAL PRIMARY KEY,
                    instagram_username TEXT NOT NULL,
                    slug TEXT UNIQUE NOT NULL,
                    agent_code TEXT UNIQUE NOT NULL,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS landing_events (
                    id BIGSERIAL PRIMARY KEY,
                    slug TEXT NOT NULL,
                    agent_code TEXT NOT NULL,
                    visitor_hash TEXT,
                    user_agent TEXT,
                    referer TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_landing_events_agent_time
                ON landing_events(agent_code, created_at)
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS outbound_events (
                    id BIGSERIAL PRIMARY KEY,
                    slug TEXT NOT NULL,
                    agent_code TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    visitor_hash TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_outbound_events_agent_dest_time
                ON outbound_events(agent_code, destination, created_at)
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS landing_themes (
                    id BIGSERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    index_html TEXT NOT NULL,
                    created_by BIGINT,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    published_at TIMESTAMPTZ,
                    is_active BOOLEAN DEFAULT FALSE
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS landing_theme_assets (
                    id BIGSERIAL PRIMARY KEY,
                    theme_id BIGINT NOT NULL REFERENCES landing_themes(id) ON DELETE CASCADE,
                    path TEXT NOT NULL,
                    mime_type TEXT,
                    content BYTEA NOT NULL,
                    UNIQUE(theme_id, path)
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS theme_publish_history (
                    id BIGSERIAL PRIMARY KEY,
                    theme_id BIGINT NOT NULL REFERENCES landing_themes(id) ON DELETE CASCADE,
                    published_at TIMESTAMPTZ DEFAULT NOW()
                )
                """
            )

            cur.execute(
                """
                INSERT INTO campaign_links (instagram_username, slug, agent_code)
                VALUES
                ('fakt_cricket_memes','fakt-cricket-memes','faktcricket'),
                ('ritikwins','ritikwins','ritikwins'),
                ('theankuedit','theankuedit','theankuedit'),
                ('5wides','5wides','5wides'),
                ('cricket.official10','cricket-official10','cricketofficial10'),
                ('bharath._editss','bharath-editss','bharatheditss'),
                ('ryuzakiii.exeeeeee','ryuzakiii-exeeeeee','ryuzakiiiexe'),
                ('cric__master18','cric-master18','cricmaster18'),
                ('akash_mahi0007','akash-mahi0007','akashmahi0007'),
                ('cricysaakir2.0','cricysaakir2-0','cricysaakir20'),
                ('ishankishan32_','ishankishan32','ishankishan32'),
                ('rsnreel','rsnreel','rsnreel'),
                ('fahadcricketreviews','fahadcricketreviews','fahadcricket'),
                ('cricsays','cricsays','cricsays'),
                ('saketeditt','saketeditt','saketeditt'),
                ('rohit_sharma_status._45','rohit-sharma-status-45','rohitstatus45'),
                ('official_bobby_4uhh_','official-bobby-4uhh','officialbobby4'),
                ('surat_tennis_cricket_','surat-tennis-cricket','surattennis'),
                ('cricket_exeee','cricket-exeee','cricketexeee'),
                ('smriti_jemi_lovers','smriti-jemi-lovers','smritijemi'),
                ('maxxo_editz_45','maxxo-editz-45','maxxoeditz45'),
                ('rohit_sharma_.status_king','rohit-sharma-status-king','rohitstatusking'),
                ('hitman_cha_diwana___45','hitman-cha-diwana-45','hitmandiwana45'),
                ('rishabh_dines17','rishabh-dines17','rishabhdines17'),
                ('csk_marathi_status_2.0','csk-marathi-status-2-0','cskmarathi20'),
                ('virat.kohli.marathi.status','virat-kohli-marathi-status','viratkohlitheme'),
                ('mahi.lifetime','mahi-lifetime','mahilifetime')
                ON CONFLICT DO NOTHING
                """
            )

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


def delete_agent_permanently(code):
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

            cur.execute("DELETE FROM referrals WHERE agent_id=%s", (agent["id"],))
            cur.execute("DELETE FROM agents WHERE id=%s", (agent["id"],))
            conn.commit()
            return agent, referral_count


def save_referral(user, agent, payload):
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
            row = cur.fetchone()
            conn.commit()
            return row, True


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
    agent = find_agent_by_code(code)
    if not agent:
        return None

    with get_db() as conn:
        with conn.cursor() as cur:
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

            cur.execute(
                """
                SELECT COUNT(*) AS n
                FROM referrals
                WHERE agent_id IS NOT NULL
                  AND joined_at >= DATE_TRUNC('day', NOW())
                """
            )
            today = cur.fetchone()["n"]

            cur.execute(
                """
                SELECT COUNT(*) AS n
                FROM referrals
                WHERE agent_id IS NOT NULL
                  AND joined_at >= NOW() - INTERVAL '7 days'
                """
            )
            week = cur.fetchone()["n"]

            cur.execute(
                """
                SELECT a.name, a.code, COUNT(r.id) AS c
                FROM agents a
                LEFT JOIN referrals r ON r.agent_id=a.id
                GROUP BY a.id
                ORDER BY c DESC
                LIMIT 1
                """
            )
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
            cur.execute(
                """
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
                """
            )
            return cur.fetchall()


# ============================================================
# MENUS
# ============================================================

def public_menu(user_id=None):
    rows = [
        [
            InlineKeyboardButton("🎰 Casino", web_app=WebAppInfo(url=APP_URL)),
            InlineKeyboardButton("⚽ Sportsbook", web_app=WebAppInfo(url=APP_URL)),
        ],
        [
            InlineKeyboardButton("🔄 Exchange", web_app=WebAppInfo(url=APP_URL)),
            InlineKeyboardButton("💳 Deposit", web_app=WebAppInfo(url=APP_URL)),
        ],
        [
            InlineKeyboardButton("💸 Withdrawal", web_app=WebAppInfo(url=APP_URL)),
            InlineKeyboardButton("🎧 Support", callback_data="support"),
        ],
        [
            InlineKeyboardButton("🎁 Promotions", web_app=WebAppInfo(url=APP_URL)),
            InlineKeyboardButton("👥 Refer a Friend", callback_data="refer_friend"),
        ],
        [
            InlineKeyboardButton("👑 VIP Club", web_app=WebAppInfo(url=APP_URL)),
        ],
        [
            InlineKeyboardButton("🎟️ My Bets", web_app=WebAppInfo(url=APP_URL)),
            InlineKeyboardButton("📜 Transactions", web_app=WebAppInfo(url=APP_URL)),
        ],
        [
            InlineKeyboardButton("💰 My Balance", web_app=WebAppInfo(url=APP_URL)),
        ],
    ]

    if user_id and find_agent_by_telegram_user_id(user_id):
        rows.insert(
            0,
            [
                InlineKeyboardButton(
                    "📈 My Affiliate Performance",
                    callback_data="affiliate_home",
                )
            ],
        )

    if user_id == ADMIN_ID:
        rows.insert(
            0,
            [
                InlineKeyboardButton(
                    "🛠 Admin Panel",
                    callback_data="admin_home",
                )
            ],
        )

    return InlineKeyboardMarkup(rows)


def support_menu():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("💬 Telegram Support", url=TELEGRAM_SUPPORT_URL)],
            [InlineKeyboardButton("🟢 WhatsApp Support", url=WHATSAPP_SUPPORT_URL)],
            [InlineKeyboardButton("📢 Betroxy Updates", url=UPDATES_URL)],
            [InlineKeyboardButton("⬅️ Main Menu", callback_data="home")],
        ]
    )


def admin_menu():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📈 Instagram Campaign Tracker", callback_data="campaign_home")],
            [
                InlineKeyboardButton("👥 Affiliates", callback_data="admin_agents"),
                InlineKeyboardButton("➕ Add Affiliate", callback_data="admin_add"),
            ],
            [
                InlineKeyboardButton("📊 Affiliate Report", callback_data="admin_report"),
                InlineKeyboardButton("🔎 Search Affiliate", callback_data="admin_search"),
            ],
            [InlineKeyboardButton("📥 Export Affiliate CSV", callback_data="admin_export")],
            [InlineKeyboardButton("🏠 Public Menu", callback_data="home")],
        ]
    )


def campaign_menu():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📊 Full Report", callback_data="campaign_report"),
                InlineKeyboardButton("📅 Today", callback_data="campaign_today"),
            ],
            [
                InlineKeyboardButton("🏆 Top Pages", callback_data="campaign_top"),
                InlineKeyboardButton("🔄 Refresh", callback_data="campaign_home"),
            ],
            [
                InlineKeyboardButton("🔗 Creator Links", callback_data="campaign_links"),
                InlineKeyboardButton("➕ Add 1 Page", callback_data="campaign_add_single"),
            ],
            [
                InlineKeyboardButton("📚 Bulk Create Links", callback_data="campaign_add_bulk"),
                InlineKeyboardButton("📥 Export CSV", callback_data="campaign_export"),
            ],
            [
                InlineKeyboardButton("🎨 Landing Design Manager", callback_data="theme_home"),
            ],
            [InlineKeyboardButton("⬅️ Admin Panel", callback_data="admin_home")],
        ]
    )


def campaign_creator_menu(code):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✏️ Edit", callback_data=f"campaign_edit:{code}"),
                InlineKeyboardButton("🗑 Delete", callback_data=f"campaign_delete_confirm:{code}"),
            ],
            [InlineKeyboardButton("🔄 Refresh", callback_data=f"campaign_creator:{code}")],
            [InlineKeyboardButton("⬅️ Creator Links", callback_data="campaign_links")],
            [InlineKeyboardButton("🏠 Campaign Tracker", callback_data="campaign_home")],
        ]
    )


def campaign_edit_menu(code):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("👤 Instagram Name", callback_data=f"campaign_edit_username:{code}"),
                InlineKeyboardButton("🔗 Landing Slug", callback_data=f"campaign_edit_slug:{code}"),
            ],
            [
                InlineKeyboardButton("🆔 Affiliate Code", callback_data=f"campaign_edit_code:{code}"),
            ],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"campaign_creator:{code}")],
        ]
    )


def agent_action_menu(code, active=True):
    status_button = (
        InlineKeyboardButton("⛔ Disable", callback_data=f"agent_disable:{code}")
        if active
        else InlineKeyboardButton("✅ Enable", callback_data=f"agent_enable:{code}")
    )

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✏️ Name", callback_data=f"agent_edit_name:{code}"),
                InlineKeyboardButton("🆔 Code", callback_data=f"agent_edit_code:{code}"),
            ],
            [
                InlineKeyboardButton(
                    "💰 Commission %",
                    callback_data=f"agent_edit_rate:{code}",
                ),
                InlineKeyboardButton("🔗 URL", callback_data=f"agent_edit_url:{code}"),
            ],
            [
                InlineKeyboardButton("👥 View Users", callback_data=f"agent_users:{code}"),
                status_button,
            ],
            [
                InlineKeyboardButton(
                    "🔐 Affiliate Access",
                    callback_data=f"agent_access:{code}",
                ),
                InlineKeyboardButton(
                    "🔓 Unlink Account",
                    callback_data=f"agent_unlink:{code}",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🗑 Permanent Delete",
                    callback_data=f"agent_delete_confirm:{code}",
                )
            ],
            [
                InlineKeyboardButton("⬅️ Affiliates", callback_data="admin_agents"),
                InlineKeyboardButton("🏠 Admin", callback_data="admin_home"),
            ],
        ]
    )


def affiliate_menu():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔄 Refresh", callback_data="affiliate_home"),
                InlineKeyboardButton("🔗 My Referral Link", callback_data="affiliate_link"),
            ],
            [InlineKeyboardButton("🏠 Betroxy Menu", callback_data="home")],
        ]
    )


# ============================================================
# AUTH + TEXT
# ============================================================

def is_admin(user_id):
    return user_id == ADMIN_ID


async def require_admin(update):
    if not update.effective_user or not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("❌ Admin access required.")
        return False
    return True


def public_welcome_text():
    return (
        "👋 <b>Welcome to Betroxy!</b>\n\n"
        "Your ultimate destination for <b>Casino, Sportsbook, Exchange</b> and more.\n\n"
        "👇 Tap a button below to get started."
    )


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
        f"🗓 Last 30 Days: {stats['month']}"
    )


# ============================================================
# PUBLIC COMMANDS
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    payload = context.args[0].strip() if context.args else None

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
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "📈 View My Performance",
                            callback_data="affiliate_home",
                        )
                    ]
                ]
            ),
        )
        return

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
        public_welcome_text(),
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


async def agent_rate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /agent_rate code rate")
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
        "Send this private one-time link only to the affiliate:\n\n"
        f"<code>{link}</code>",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


# ============================================================
# INSTAGRAM AFFILIATES
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
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await require_admin(update):
        return

    created = []
    existing = []
    failed = []

    for name, code in INSTAGRAM_AFFILIATES:
        try:
            if find_agent_by_code(code):
                existing.append(code)
                continue
            create_agent(name, code, 0)
            created.append(code)
        except Exception:
            logger.exception("Bulk affiliate create failed for %s", code)
            failed.append(code)

    await update.message.reply_text(
        "✅ <b>Instagram Affiliate Bulk Setup Complete</b>\n\n"
        f"Created: {len(created)}\n"
        f"Already Existing: {len(existing)}\n"
        f"Failed: {len(failed)}",
        parse_mode=ParseMode.HTML,
    )



# ============================================================
# INSTAGRAM LINK TRACKER
# ============================================================


def update_campaign_username(code, new_username):
    new_username = new_username.strip().lstrip("@")
    if not new_username:
        raise ValueError("Instagram username cannot be empty")

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id FROM campaign_links
                WHERE LOWER(instagram_username)=LOWER(%s)
                  AND LOWER(agent_code)<>LOWER(%s)
                LIMIT 1
                """,
                (new_username, code),
            )
            if cur.fetchone():
                raise ValueError("That Instagram username already exists")

            cur.execute(
                """
                UPDATE campaign_links
                SET instagram_username=%s
                WHERE LOWER(agent_code)=LOWER(%s)
                RETURNING *
                """,
                (new_username, code),
            )
            row = cur.fetchone()

            # Keep affiliate display name aligned with creator username.
            if row:
                cur.execute(
                    """
                    UPDATE agents
                    SET name=%s
                    WHERE LOWER(code)=LOWER(%s)
                    """,
                    (new_username, code),
                )
            conn.commit()
            return row


def update_campaign_slug(code, new_slug):
    new_slug = re.sub(r"[^a-z0-9-]+", "-", new_slug.strip().lower())
    new_slug = re.sub(r"-+", "-", new_slug).strip("-")
    if len(new_slug) < 2:
        raise ValueError("Slug must contain at least 2 characters")

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id FROM campaign_links
                WHERE LOWER(slug)=LOWER(%s)
                  AND LOWER(agent_code)<>LOWER(%s)
                LIMIT 1
                """,
                (new_slug, code),
            )
            if cur.fetchone():
                raise ValueError("That landing-page slug already exists")

            cur.execute(
                """
                UPDATE campaign_links
                SET slug=%s
                WHERE LOWER(agent_code)=LOWER(%s)
                RETURNING *
                """,
                (new_slug, code),
            )
            row = cur.fetchone()
            conn.commit()
            return row


def update_campaign_agent_code(old_code, new_code):
    new_code = new_code.strip().lower()
    if not re.fullmatch(r"[a-z0-9_]{2,40}", new_code):
        raise ValueError("Code must use only letters, numbers or underscore")

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id FROM agents
                WHERE LOWER(code)=LOWER(%s)
                  AND LOWER(code)<>LOWER(%s)
                LIMIT 1
                """,
                (new_code, old_code),
            )
            if cur.fetchone():
                raise ValueError("That affiliate code already exists")

            cur.execute(
                """
                SELECT id FROM campaign_links
                WHERE LOWER(agent_code)=LOWER(%s)
                  AND LOWER(agent_code)<>LOWER(%s)
                LIMIT 1
                """,
                (new_code, old_code),
            )
            if cur.fetchone():
                raise ValueError("That creator code already exists")

            # Change the affiliate code and campaign code together.
            cur.execute(
                "UPDATE agents SET code=%s WHERE LOWER(code)=LOWER(%s)",
                (new_code, old_code),
            )
            cur.execute(
                """
                UPDATE campaign_links
                SET agent_code=%s
                WHERE LOWER(agent_code)=LOWER(%s)
                RETURNING *
                """,
                (new_code, old_code),
            )
            row = cur.fetchone()

            # Preserve historic tracker continuity by rewriting event codes.
            cur.execute(
                "UPDATE landing_events SET agent_code=%s WHERE LOWER(agent_code)=LOWER(%s)",
                (new_code, old_code),
            )
            cur.execute(
                "UPDATE outbound_events SET agent_code=%s WHERE LOWER(agent_code)=LOWER(%s)",
                (new_code, old_code),
            )
            cur.execute(
                "UPDATE conversion_events SET agent_code=%s WHERE LOWER(agent_code)=LOWER(%s)",
                (new_code, old_code),
            )
            conn.commit()
            return row


def delete_campaign_link(code):
    """
    Permanently removes only the creator tracking link.
    Affiliate account and historical event data are intentionally preserved.
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM campaign_links
                WHERE LOWER(agent_code)=LOWER(%s)
                RETURNING *
                """,
                (code,),
            )
            row = cur.fetchone()
            conn.commit()
            return row


def list_campaign_links(limit=500):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM campaign_links WHERE is_active=TRUE ORDER BY created_at DESC, id DESC LIMIT %s",
                (limit,),
            )
            return cur.fetchall()


def campaign_link_by_slug(slug):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM campaign_links WHERE LOWER(slug)=LOWER(%s) AND is_active=TRUE LIMIT 1",
                (slug,),
            )
            return cur.fetchone()


def campaign_link_by_code(code):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM campaign_links WHERE LOWER(agent_code)=LOWER(%s) LIMIT 1",
                (code,),
            )
            return cur.fetchone()


def slugify_instagram(username):
    username = username.strip().lstrip("@").lower()
    return re.sub(r"[^a-z0-9]+", "-", username).strip("-") or "creator"


def codeify_instagram(username):
    username = username.strip().lstrip("@").lower()
    return (re.sub(r"[^a-z0-9]+", "", username)[:28] or "creator")


def ensure_unique_slug_and_code(username):
    base_slug = slugify_instagram(username)
    base_code = codeify_instagram(username)
    slug, agent_code = base_slug, base_code
    n = 2
    with get_db() as conn:
        with conn.cursor() as cur:
            while True:
                cur.execute("SELECT 1 FROM campaign_links WHERE LOWER(slug)=LOWER(%s) LIMIT 1", (slug,))
                slug_exists = cur.fetchone() is not None
                cur.execute("SELECT 1 FROM agents WHERE LOWER(code)=LOWER(%s) LIMIT 1", (agent_code,))
                code_exists = cur.fetchone() is not None
                cur.execute("SELECT 1 FROM campaign_links WHERE LOWER(agent_code)=LOWER(%s) LIMIT 1", (agent_code,))
                campaign_code_exists = cur.fetchone() is not None
                if not slug_exists and not code_exists and not campaign_code_exists:
                    return slug, agent_code
                slug = f"{base_slug}-{n}"
                suffix = str(n)
                agent_code = base_code[:max(1, 28-len(suffix))] + suffix
                n += 1


def create_campaign_creator(username, commission_rate=0):
    username = username.strip().lstrip("@")
    if not username:
        raise ValueError("Instagram username is empty")
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM campaign_links WHERE LOWER(instagram_username)=LOWER(%s) LIMIT 1",
                (username,),
            )
            existing = cur.fetchone()
            if existing:
                return existing, False
    slug, agent_code = ensure_unique_slug_and_code(username)
    create_agent(username, agent_code, commission_rate)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO campaign_links (instagram_username, slug, agent_code, is_active)
                VALUES (%s, %s, %s, TRUE)
                RETURNING *
                """,
                (username, slug, agent_code),
            )
            row = cur.fetchone()
            conn.commit()
            return row, True


def creator_urls(row):
    landing = f"{PUBLIC_BASE_URL}/{row['slug']}"
    telegram = f"https://t.me/{BOT_USERNAME}?start=agent_{row['agent_code']}"
    return landing, telegram


def _client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or ""


def record_landing_click(slug):
    link = campaign_link_by_slug(slug)
    if not link:
        return False
    code = link["agent_code"]

    ip = _client_ip()
    ua = request.headers.get("User-Agent", "")[:500]
    referer = request.headers.get("Referer", "")[:1000]
    fingerprint = hashlib.sha256(f"{ip}|{ua}".encode()).hexdigest()[:32]

    with get_db() as conn:
        with conn.cursor() as cur:
            # Ignore obvious reload/duplicate hits from the same device for 30 seconds.
            cur.execute(
                """
                SELECT 1
                FROM landing_clicks
                WHERE slug=%s
                  AND ip_hash=%s
                  AND clicked_at >= NOW() - INTERVAL '30 seconds'
                LIMIT 1
                """,
                (slug, fingerprint),
            )
            if cur.fetchone():
                return True

            cur.execute(
                """
                INSERT INTO landing_clicks
                    (slug, agent_code, ip_hash, user_agent, referer)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (slug, code, fingerprint, ua, referer),
            )
        conn.commit()
    return True


def _period_sql(period):
    if period == "today":
        return "AND created_at >= DATE_TRUNC('day', NOW())"
    if period == "7d":
        return "AND created_at >= NOW() - INTERVAL '7 days'"
    if period == "30d":
        return "AND created_at >= NOW() - INTERVAL '30 days'"
    return ""


def instagram_tracker_stats(code=None, period="all"):
    period_filter = _period_sql(period)
    with get_db() as conn:
        with conn.cursor() as cur:
            where = ""
            params = []
            if code:
                where = "WHERE LOWER(a.code)=LOWER(%s)"
                params = [code]

            cur.execute(
                f"""
                WITH visits AS (
                    SELECT
                        agent_code,
                        COUNT(*) AS landing_visits,
                        COUNT(DISTINCT visitor_hash) FILTER (WHERE visitor_hash IS NOT NULL) AS unique_visitors
                    FROM landing_events
                    WHERE 1=1 {period_filter}
                    GROUP BY agent_code
                ),
                outbound AS (
                    SELECT
                        agent_code,
                        COUNT(*) FILTER (WHERE destination='telegram') AS telegram_clicks,
                        COUNT(*) FILTER (WHERE destination='website') AS website_clicks
                    FROM outbound_events
                    WHERE 1=1 {period_filter}
                    GROUP BY agent_code
                ),
                starts AS (
                    SELECT
                        a2.code AS agent_code,
                        COUNT(*) AS telegram_starts
                    FROM referrals r
                    JOIN agents a2 ON a2.id=r.agent_id
                    WHERE r.agent_id IS NOT NULL
                    {"AND r.joined_at >= DATE_TRUNC('day', NOW())" if period == "today" else ""}
                    {"AND r.joined_at >= NOW() - INTERVAL '7 days'" if period == "7d" else ""}
                    {"AND r.joined_at >= NOW() - INTERVAL '30 days'" if period == "30d" else ""}
                    GROUP BY a2.code
                ),
                conv AS (
                    SELECT
                        agent_code,
                        COUNT(*) FILTER (WHERE event_type='registration') AS registrations,
                        COUNT(*) FILTER (WHERE event_type='deposit') AS deposits,
                        COALESCE(SUM(amount) FILTER (WHERE event_type='deposit'), 0) AS deposit_amount
                    FROM conversion_events
                    WHERE 1=1 {period_filter}
                    GROUP BY agent_code
                )
                SELECT
                    a.code,
                    a.name,
                    COALESCE(v.landing_visits, 0) AS landing_visits,
                    COALESCE(v.unique_visitors, 0) AS unique_visitors,
                    COALESCE(o.telegram_clicks, 0) AS telegram_clicks,
                    COALESCE(o.website_clicks, 0) AS website_clicks,
                    COALESCE(s.telegram_starts, 0) AS telegram_starts,
                    COALESCE(c.registrations, 0) AS registrations,
                    COALESCE(c.deposits, 0) AS deposits,
                    COALESCE(c.deposit_amount, 0) AS deposit_amount
                FROM agents a
                LEFT JOIN visits v ON LOWER(v.agent_code)=LOWER(a.code)
                LEFT JOIN outbound o ON LOWER(o.agent_code)=LOWER(a.code)
                LEFT JOIN starts s ON LOWER(s.agent_code)=LOWER(a.code)
                LEFT JOIN conv c ON LOWER(c.agent_code)=LOWER(a.code)
                {where}
                ORDER BY landing_visits DESC, telegram_clicks DESC, website_clicks DESC, a.name
                """,
                params,
            )
            return cur.fetchall()


def tracker_today_totals():
    rows = instagram_tracker_stats(period="today")
    return {
        "landing_visits": sum(int(r["landing_visits"] or 0) for r in rows),
        "unique_visitors": sum(int(r["unique_visitors"] or 0) for r in rows),
        "telegram_clicks": sum(int(r["telegram_clicks"] or 0) for r in rows),
        "website_clicks": sum(int(r["website_clicks"] or 0) for r in rows),
        "starts": sum(int(r["telegram_starts"] or 0) for r in rows),
        "registrations": sum(int(r["registrations"] or 0) for r in rows),
        "deposits": sum(int(r["deposits"] or 0) for r in rows),
        "deposit_amount": sum(float(r["deposit_amount"] or 0) for r in rows),
    }


def _visitor_fingerprint():
    ip = _client_ip()
    ua = request.headers.get("User-Agent", "")[:500]
    raw = f"{ip}|{ua}".encode("utf-8", "ignore")
    return hashlib.sha256(raw).hexdigest(), ua


def record_landing_visit(slug):
    link = campaign_link_by_slug(slug)
    if not link:
        return False
    fingerprint, ua = _visitor_fingerprint()
    referer = request.headers.get("Referer", "")[:1000]
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM landing_events
                WHERE slug=%s AND visitor_hash=%s
                  AND created_at >= NOW() - INTERVAL '30 minutes'
                LIMIT 1
                """,
                (slug, fingerprint),
            )
            if not cur.fetchone():
                cur.execute(
                    """
                    INSERT INTO landing_events
                        (slug, agent_code, visitor_hash, user_agent, referer)
                    VALUES (%s,%s,%s,%s,%s)
                    """,
                    (slug, link["agent_code"], fingerprint, ua, referer),
                )
        conn.commit()
    return True


def record_outbound_click(slug, destination):
    link = campaign_link_by_slug(slug)
    if not link or destination not in {"telegram", "website", "casino", "sportsbook", "popular", "promotions"}:
        return False
    fingerprint, _ = _visitor_fingerprint()
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM outbound_events
                WHERE slug=%s AND visitor_hash=%s AND destination=%s
                  AND created_at >= NOW() - INTERVAL '5 seconds'
                LIMIT 1
                """,
                (slug, fingerprint, destination),
            )
            if not cur.fetchone():
                cur.execute(
                    """
                    INSERT INTO outbound_events
                        (slug, agent_code, destination, visitor_hash)
                    VALUES (%s,%s,%s,%s)
                    """,
                    (slug, link["agent_code"], destination, fingerprint),
                )
        conn.commit()
    return True


async def igstats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update):
        return

    if context.args:
        code_or_slug = context.args[0].strip().lower().lstrip("@")
        campaign = campaign_link_by_slug(code_or_slug)
        code = campaign["agent_code"] if campaign else code_or_slug
        # Also allow Instagram username
        for name, c in INSTAGRAM_AFFILIATES:
            if name.lower() == code_or_slug:
                code = c
                break
        rows = instagram_tracker_stats(code)
        if not rows:
            await update.message.reply_text("❌ Creator / agent not found.")
            return
        r = rows[0]
        clicks = int(r["landing_visits"] or 0)
        starts = int(r["telegram_starts"] or 0)
        rate = (starts / clicks * 100) if clicks else 0
        await update.message.reply_text(
            "📊 <b>Instagram Link Tracker</b>\n\n"
            f"Page: <b>@{r['name']}</b>\n"
            f"Code: <code>{r['code']}</code>\n"
            f"Landing clicks: <b>{clicks}</b>\n"
            f"Telegram starts: <b>{starts}</b>\n"
            f"Click → Telegram: <b>{rate:.1f}%</b>\n"
            f"Registrations: <b>{int(r['registrations'] or 0)}</b>\n"
            f"Deposits: <b>{int(r['deposits'] or 0)}</b>\n"
            f"Deposit amount: <b>{r['deposit_amount'] or 0}</b>",
            parse_mode=ParseMode.HTML,
        )
        return

    rows = instagram_tracker_stats()
    total_clicks = sum(int(r["landing_visits"] or 0) for r in rows)
    total_starts = sum(int(r["telegram_starts"] or 0) for r in rows)
    total_regs = sum(int(r["registrations"] or 0) for r in rows)
    total_deps = sum(int(r["deposits"] or 0) for r in rows)

    lines = [
        "📊 <b>27-Link Instagram Tracker</b>",
        "",
        f"Landing clicks: <b>{total_clicks}</b>",
        f"Telegram starts: <b>{total_starts}</b>",
        f"Registrations: <b>{total_regs}</b>",
        f"Deposits: <b>{total_deps}</b>",
        "",
        "<b>Top pages</b>",
    ]
    for r in rows[:15]:
        clicks = int(r["landing_visits"] or 0)
        starts = int(r["telegram_starts"] or 0)
        lines.append(f"@{r['name']}: {clicks} clicks • {starts} TG")
    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
    )


async def igtoday_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update):
        return
    s = tracker_today_totals()
    await update.message.reply_text(
        "📅 <b>Instagram Tracker — Today</b>\n\n"
        f"Landing clicks: <b>{s['clicks']}</b>\n"
        f"Telegram starts: <b>{s['starts']}</b>\n"
        f"Registrations: <b>{s['registrations']}</b>\n"
        f"Deposits: <b>{s['deposits']}</b>\n"
        f"Deposit amount: <b>{s['deposit_amount']}</b>",
        parse_mode=ParseMode.HTML,
    )



ALLOWED_THEME_EXTENSIONS = {
    ".html", ".htm", ".css", ".js", ".json", ".txt",
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".ico",
    ".woff", ".woff2", ".ttf", ".otf", ".mp4", ".webm"
}


def active_theme():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM landing_themes WHERE is_active=TRUE ORDER BY id DESC LIMIT 1")
            return cur.fetchone()


def get_theme(theme_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM landing_themes WHERE id=%s LIMIT 1", (theme_id,))
            return cur.fetchone()


def recent_themes(limit=8):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM landing_themes ORDER BY id DESC LIMIT %s", (limit,))
            return cur.fetchall()


def save_theme_zip(zip_bytes, filename, created_by):
    if len(zip_bytes) > THEME_UPLOAD_MAX_MB * 1024 * 1024:
        raise ValueError(f"ZIP is larger than {THEME_UPLOAD_MAX_MB} MB")

    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    members = [m for m in zf.infolist() if not m.is_dir()]
    if len(members) > 200:
        raise ValueError("Theme has too many files (max 200)")

    safe_files = {}
    index_name = None
    for info in members:
        name = info.filename.replace("\\", "/").lstrip("/")
        if ".." in name.split("/"):
            raise ValueError("Unsafe path in ZIP")
        ext = Path(name).suffix.lower()
        if ext not in ALLOWED_THEME_EXTENSIONS:
            continue
        data = zf.read(info)
        if len(data) > 8 * 1024 * 1024:
            raise ValueError(f"Asset too large: {name}")
        safe_files[name] = data
        if name.lower() == "index.html" or name.lower().endswith("/index.html"):
            if index_name is None or name.count("/") < index_name.count("/"):
                index_name = name

    if not index_name:
        raise ValueError("ZIP must contain index.html")

    root_prefix = index_name[:-len("index.html")]
    normalized = {}
    for name, data in safe_files.items():
        if root_prefix and name.startswith(root_prefix):
            rel = name[len(root_prefix):]
        else:
            rel = name
        if rel:
            normalized[rel] = data

    index_bytes = normalized.pop("index.html", None)
    if index_bytes is None:
        index_bytes = safe_files[index_name]
    index_html = index_bytes.decode("utf-8", "replace")
    theme_name = Path(filename).stem[:100] or "Landing Theme"

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO landing_themes (name, index_html, created_by)
                VALUES (%s,%s,%s)
                RETURNING *
                """,
                (theme_name, index_html, created_by),
            )
            theme = cur.fetchone()
            for path, content in normalized.items():
                mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
                cur.execute(
                    """
                    INSERT INTO landing_theme_assets (theme_id, path, mime_type, content)
                    VALUES (%s,%s,%s,%s)
                    ON CONFLICT (theme_id, path)
                    DO UPDATE SET mime_type=EXCLUDED.mime_type, content=EXCLUDED.content
                    """,
                    (theme["id"], path, mime, content),
                )
        conn.commit()
    return theme


def publish_theme(theme_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM landing_themes WHERE id=%s", (theme_id,))
            if not cur.fetchone():
                return False
            cur.execute("UPDATE landing_themes SET is_active=FALSE WHERE is_active=TRUE")
            cur.execute(
                "UPDATE landing_themes SET is_active=TRUE, published_at=NOW() WHERE id=%s",
                (theme_id,),
            )
            cur.execute(
                "INSERT INTO theme_publish_history (theme_id) VALUES (%s)",
                (theme_id,),
            )
        conn.commit()
    return True



def ensure_polished_builtin_theme_once():
    """
    One-time migration:
    The earlier Super Platform may already have a simplified landing theme
    saved as the active theme in PostgreSQL. That database theme overrides
    DEFAULT_LANDING_HTML even after bot.py is updated.

    Create/publish the polished built-in theme only once. After it exists,
    admins remain free to publish another uploaded theme later; restarts will
    not force this theme again.
    """
    theme_name = "BETROXY Polished Built-in v2"

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, is_active FROM landing_themes WHERE name=%s ORDER BY id DESC LIMIT 1",
                (theme_name,),
            )
            existing = cur.fetchone()

            if existing:
                return existing["id"]

            cur.execute(
                """
                INSERT INTO landing_themes (name, index_html, created_by)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (theme_name, DEFAULT_LANDING_HTML, ADMIN_ID),
            )
            theme_id = cur.fetchone()["id"]

            cur.execute("UPDATE landing_themes SET is_active=FALSE WHERE is_active=TRUE")
            cur.execute(
                "UPDATE landing_themes SET is_active=TRUE, published_at=NOW() WHERE id=%s",
                (theme_id,),
            )
            cur.execute(
                "INSERT INTO theme_publish_history (theme_id) VALUES (%s)",
                (theme_id,),
            )
        conn.commit()

    logger.info("Published one-time polished built-in landing theme id=%s", theme_id)
    return theme_id


def rollback_theme():
    current = active_theme()
    with get_db() as conn:
        with conn.cursor() as cur:
            if current:
                cur.execute(
                    """
                    SELECT h.theme_id
                    FROM theme_publish_history h
                    WHERE h.theme_id <> %s
                    ORDER BY h.id DESC
                    LIMIT 1
                    """,
                    (current["id"],),
                )
            else:
                cur.execute(
                    "SELECT theme_id FROM theme_publish_history ORDER BY id DESC LIMIT 1"
                )
            row = cur.fetchone()
    if not row:
        return None
    publish_theme(row["theme_id"])
    return get_theme(row["theme_id"])


def inject_theme(theme, link, preview=False):
    slug = link["slug"]
    instagram = link["instagram_username"]
    telegram_url = f"{PUBLIC_BASE_URL}/go/{slug}/telegram"
    website_url = f"{PUBLIC_BASE_URL}/go/{slug}/website"

    raw = theme["index_html"] if theme else DEFAULT_LANDING_HTML
    replacements = {
        "{{INSTAGRAM_PAGE}}": html.escape("@" + instagram),
        "{{LANDING_SLUG}}": html.escape(slug),
        "{{TELEGRAM_URL}}": telegram_url,
        "{{WEBSITE_URL}}": website_url,
        "{{BETROXY_WEB_URL}}": BETROXY_WEB_URL,
        "{{BETROXY_BOT_URL}}": BETROXY_BOT_URL,
    }
    for k, v in replacements.items():
        raw = raw.replace(k, v)

    if theme:
        base_tag = f'<base href="{PUBLIC_BASE_URL}/theme-assets/{theme["id"]}/">'
        if "<head" in raw.lower():
            raw = re.sub(r"(<head[^>]*>)", r"\1" + base_tag, raw, count=1, flags=re.I)
        else:
            raw = base_tag + raw

    # Gives uploaded designs a code-free way to mark buttons.
    helper = f"""
<script>
(function() {{
  const tg = {telegram_url!r};
  const web = {website_url!r};
  document.querySelectorAll('[data-betroxy="telegram"],[data-track="telegram"]').forEach(el => {{
    if (el.tagName === 'A') el.href = tg;
    else el.addEventListener('click', () => location.href = tg);
  }});
  document.querySelectorAll('[data-betroxy="website"],[data-track="website"]').forEach(el => {{
    if (el.tagName === 'A') el.href = web;
    else el.addEventListener('click', () => location.href = web);
  }});
}})();
</script>
"""
    if "</body>" in raw.lower():
        pos = raw.lower().rfind("</body>")
        raw = raw[:pos] + helper + raw[pos:]
    else:
        raw += helper
    return raw


DEFAULT_LANDING_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#06140f">
<title>BETROXY — Official Access</title>
<style>
*{box-sizing:border-box}
html,body{margin:0;min-height:100%;font-family:Inter,Arial,Helvetica,sans-serif;background:#04110c;color:#fff}
body{
  min-height:100vh;
  background:
    radial-gradient(circle at 80% 15%,rgba(32,211,132,.13),transparent 34%),
    radial-gradient(circle at 12% 85%,rgba(31,157,104,.10),transparent 32%),
    linear-gradient(145deg,#020b08 0%,#061810 52%,#03100b 100%);
}
.page{width:min(100%,760px);margin:0 auto;padding:22px 18px 34px}
.source{
  display:inline-flex;align-items:center;gap:8px;
  padding:8px 12px;border-radius:999px;
  background:rgba(255,255,255,.055);border:1px solid rgba(255,255,255,.09);
  color:#c9d8d1;font-size:12px;font-weight:700;letter-spacing:.1px
}
.dot{width:7px;height:7px;border-radius:50%;background:#22dc8a;box-shadow:0 0 16px #22dc8a}
.hero{text-align:center;padding:28px 8px 22px}
.logo{
  font-size:clamp(40px,9vw,68px);line-height:.95;font-weight:950;letter-spacing:-2px;
  text-shadow:0 10px 35px rgba(0,0,0,.35)
}
.logo .o{color:#20e39a}
.kicker{
  margin-top:12px;color:#80efbd;font-weight:800;font-size:12px;
  letter-spacing:2.2px;text-transform:uppercase
}
.hero h1{font-size:clamp(27px,6vw,43px);margin:18px 0 8px;letter-spacing:-1px}
.hero p{margin:0 auto;color:#a8bbb2;font-size:15px;max-width:520px;line-height:1.55}
.trust{
  margin:18px auto 0;display:flex;justify-content:center;gap:10px;flex-wrap:wrap
}
.badge{
  font-size:11px;font-weight:750;color:#b9c9c1;padding:7px 10px;border-radius:999px;
  background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.075)
}
.panel{
  background:linear-gradient(180deg,rgba(11,34,25,.94),rgba(6,24,17,.96));
  border:1px solid rgba(46,224,148,.22);border-radius:25px;padding:18px;
  box-shadow:0 28px 80px rgba(0,0,0,.36)
}
.label{color:#86a99a;text-transform:uppercase;font-weight:850;font-size:10px;letter-spacing:1.8px;margin:3px 2px 11px}
.primary,.secondary{
  display:flex;align-items:center;justify-content:center;gap:10px;width:100%;
  min-height:58px;padding:15px 18px;border-radius:15px;text-decoration:none;
  font-size:15px;font-weight:900;letter-spacing:.15px;transition:.18s ease
}
.primary{background:linear-gradient(135deg,#20e39a,#16c579);color:#03100b;box-shadow:0 13px 34px rgba(24,207,127,.18)}
.primary:hover{transform:translateY(-1px);filter:brightness(1.05)}
.secondary{margin-top:11px;background:#f6faf8;color:#07150f}
.secondary:hover{transform:translateY(-1px)}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:15px}
.tile{
  min-height:86px;padding:14px;border-radius:15px;text-decoration:none;color:#fff;
  background:rgba(255,255,255,.043);border:1px solid rgba(255,255,255,.075);
  display:flex;flex-direction:column;justify-content:center;transition:.18s ease
}
.tile:hover{background:rgba(36,219,139,.08);border-color:rgba(36,219,139,.25);transform:translateY(-1px)}
.icon{font-size:22px;margin-bottom:8px}.tile b{font-size:13px}.tile span{font-size:10px;color:#849c91;margin-top:4px}
.info{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:14px}
.info div{text-align:center;padding:10px 6px;border-radius:12px;background:rgba(0,0,0,.14);border:1px solid rgba(255,255,255,.045)}
.info strong{display:block;font-size:11px}.info small{display:block;color:#789185;font-size:9px;margin-top:4px}
.footer{text-align:center;padding:22px 12px 0;color:#71867c;font-size:10px;line-height:1.6}
.footer b{color:#a4b7ae}
@media(max-width:480px){
  .page{padding:15px 13px 26px}.hero{padding:22px 4px 17px}.panel{padding:14px;border-radius:21px}
  .primary,.secondary{min-height:55px}.grid{gap:8px}.tile{min-height:82px}.info{gap:6px}
}
</style>
</head>
<body>
<main class="page">
  <div class="source"><span class="dot"></span> Exclusive access from {{INSTAGRAM_PAGE}}</div>

  <section class="hero">
    <div class="logo">BETR<span class="o">O</span>XY</div>
    <div class="kicker">Casino • Sportsbook • Exchange</div>
    <h1>Your game. Your way.</h1>
    <p>Choose how you want to continue through Betroxy official access.</p>
    <div class="trust">
      <span class="badge">⚡ Fast Access</span>
      <span class="badge">✓ Official Access</span>
      <span class="badge">💬 Support</span>
    </div>
  </section>

  <section class="panel">
    <div class="label">Official Access</div>

    <a class="primary" href="{{WEBSITE_URL}}" data-betroxy="website">🚀 PLAY ON WEBSITE</a>
    <a class="secondary" href="{{TELEGRAM_URL}}" data-betroxy="telegram">✈ OPEN TELEGRAM BOT</a>

    <div class="grid">
      <a class="tile" href="/go/{{LANDING_SLUG}}/casino">
        <span class="icon">🎰</span><b>Casino</b><span>Open casino lobby</span>
      </a>
      <a class="tile" href="/go/{{LANDING_SLUG}}/sportsbook">
        <span class="icon">⚽</span><b>Sportsbook</b><span>Sports markets</span>
      </a>
      <a class="tile" href="/go/{{LANDING_SLUG}}/popular">
        <span class="icon">🔥</span><b>Popular Games</b><span>Trending games</span>
      </a>
      <a class="tile" href="/go/{{LANDING_SLUG}}/promotions">
        <span class="icon">🎁</span><b>Promotions</b><span>Latest offers</span>
      </a>
    </div>

    <div class="info">
      <div><strong>Official</strong><small>Verified links</small></div>
      <div><strong>Mobile Ready</strong><small>Fast access</small></div>
      <div><strong>18+</strong><small>Play responsibly</small></div>
    </div>
  </section>

  <div class="footer">
    <b>BETROXY</b><br>
    18+ • Play Responsibly • Terms and eligibility apply.
  </div>
</main>
</body>
</html>"""


def sample_creator_for_preview():
    links = list_campaign_links(limit=1)
    return links[0] if links else None



tracker_api = Flask("betroxy_tracker_api")


@tracker_api.get("/health")
def tracker_health():
    return jsonify({"ok": True, "service": "betroxy-instagram-tracker"})


@tracker_api.get("/")
def landing_root():
    return Response(
        "<h2>BETROXY Campaign Platform</h2><p>Use a creator landing URL.</p>",
        mimetype="text/html",
    )


@tracker_api.get("/theme-assets/<int:theme_id>/<path:asset_path>")
def theme_asset(theme_id, asset_path):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT mime_type, content
                FROM landing_theme_assets
                WHERE theme_id=%s AND path=%s
                LIMIT 1
                """,
                (theme_id, asset_path),
            )
            row = cur.fetchone()
    if not row:
        return Response("Not found", status=404)
    return Response(bytes(row["content"]), mimetype=row["mime_type"])


@tracker_api.get("/preview/<int:theme_id>/<slug>")
def preview_theme(theme_id, slug):
    if TRACKER_API_SECRET:
        key = request.args.get("key", "")
        if not secrets.compare_digest(key, TRACKER_API_SECRET):
            return Response("Unauthorized", status=401)
    theme = get_theme(theme_id)
    link = campaign_link_by_slug(slug)
    if not theme or not link:
        return Response("Not found", status=404)
    return Response(inject_theme(theme, link, preview=True), mimetype="text/html")


@tracker_api.get("/go/<slug>/<destination>")
def outbound_redirect(slug, destination):
    slug = slug.strip().lower()
    destination = destination.strip().lower()
    link = campaign_link_by_slug(slug)
    if not link or destination not in {"telegram", "website", "casino", "sportsbook", "popular", "promotions"}:
        return Response("Not found", status=404)

    record_outbound_click(slug, destination)

    if destination == "telegram":
        # Preserve the creator/affiliate code when opening the affiliate bot.
        return redirect(
            f"https://t.me/{BOT_USERNAME}?start=agent_{link['agent_code']}",
            code=302,
        )

    if destination == "casino":
        return redirect("https://t.me/BetroxyBot/casino", code=302)

    if destination == "sportsbook":
        return redirect("https://t.me/BetroxyBot/sportsbook", code=302)

    if destination == "popular":
        return redirect("https://t.me/BetroxyBot/populargames", code=302)

    params = {
        "utm_source": "instagram",
        "utm_medium": "creator_landing",
        "utm_campaign": "batraxy",
        "utm_content": slug,
    }

    if destination == "promotions":
        target = "https://betroxy.com/promotions"
    else:
        target = BETROXY_WEB_URL

    separator = "&" if "?" in target else "?"
    return redirect(target + separator + urlencode(params), code=302)


@tracker_api.get("/<slug>")
def dynamic_creator_landing(slug):
    slug = slug.strip().lower()
    link = campaign_link_by_slug(slug)
    if not link:
        return Response("Landing page not found", status=404)
    record_landing_visit(slug)
    return Response(inject_theme(active_theme(), link), mimetype="text/html")


@tracker_api.route("/track/click/<slug>", methods=["GET", "POST", "OPTIONS"])
def tracker_click(slug):
    if request.method == "OPTIONS":
        resp = jsonify({"ok": True})
    else:
        ok = record_landing_click(slug.strip().lower())
        if not ok:
            return jsonify({"ok": False, "error": "unknown_slug"}), 404
        resp = jsonify({"ok": True})

    # Allows a landing page on batraxy.com to POST via fetch().
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


@tracker_api.post("/track/event")
def tracker_event():
    if not TRACKER_API_SECRET:
        return jsonify({"ok": False, "error": "TRACKER_API_SECRET not configured"}), 503

    supplied = request.headers.get("X-Tracker-Secret", "")
    if not secrets.compare_digest(supplied, TRACKER_API_SECRET):
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    code = (data.get("agent_code") or "").strip().lower()
    event_type = (data.get("event_type") or "").strip().lower()
    external_user_id = str(data.get("external_user_id") or "")[:200]
    amount = data.get("amount") or 0

    if not find_agent_by_code(code):
        return jsonify({"ok": False, "error": "unknown_agent"}), 404
    if event_type not in {"registration", "deposit"}:
        return jsonify({"ok": False, "error": "invalid_event_type"}), 400

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO conversion_events
                    (agent_code, event_type, external_user_id, amount)
                VALUES (%s, %s, %s, %s)
                """,
                (code, event_type, external_user_id, amount),
            )
        conn.commit()

    return jsonify({"ok": True})


def run_tracker_api():
    tracker_api.run(host="0.0.0.0", port=PORT, threaded=True, use_reloader=False)




def campaign_overview_text():
    rows = instagram_tracker_stats()
    links = list_campaign_links()
    visits = sum(int(r["landing_visits"] or 0) for r in rows)
    uniques = sum(int(r["unique_visitors"] or 0) for r in rows)
    tg = sum(int(r["telegram_clicks"] or 0) for r in rows)
    web = sum(int(r["website_clicks"] or 0) for r in rows)
    regs = sum(int(r["registrations"] or 0) for r in rows)
    deps = sum(int(r["deposits"] or 0) for r in rows)
    amount = sum(float(r["deposit_amount"] or 0) for r in rows)
    return (
        "📈 <b>BETROXY Instagram Control Center</b>\n\n"
        f"🔗 Active landing pages: <b>{len(links)}</b>\n"
        f"👁 Landing visits: <b>{visits}</b>\n"
        f"👤 Unique visitors: <b>{uniques}</b>\n"
        f"✈️ BetroxyBot clicks: <b>{tg}</b> "
        f"(<b>{(tg/visits*100 if visits else 0):.1f}%</b>)\n"
        f"🌐 Betroxy.com clicks: <b>{web}</b> "
        f"(<b>{(web/visits*100 if visits else 0):.1f}%</b>)\n"
        f"📝 Registrations: <b>{regs}</b>\n"
        f"💳 Deposits: <b>{deps}</b>\n"
        f"💰 Deposit amount: <b>{amount:,.2f}</b>\n\n"
        "Everything below is button-controlled. No report commands are required."
    )


def campaign_rows_by_code():
    return {r["code"].lower(): r for r in instagram_tracker_stats()}


def creator_report_text(code):
    rows = instagram_tracker_stats(code)
    if not rows:
        return "❌ No statistics found."
    r = rows[0]
    campaign = campaign_link_by_code(code)
    visits = int(r["landing_visits"] or 0)
    tg = int(r["telegram_clicks"] or 0)
    web = int(r["website_clicks"] or 0)
    starts = int(r["telegram_starts"] or 0)
    regs = int(r["registrations"] or 0)
    deps = int(r["deposits"] or 0)
    text = (
        f"📊 <b>@{r['name']}</b>\n\n"
        f"Agent code: <code>{r['code']}</code>\n"
        f"👁 Landing visits: <b>{visits}</b>\n"
        f"👤 Unique visitors: <b>{int(r['unique_visitors'] or 0)}</b>\n"
        f"✈️ BetroxyBot clicks: <b>{tg}</b> ({(tg/visits*100 if visits else 0):.1f}%)\n"
        f"🌐 Betroxy.com clicks: <b>{web}</b> ({(web/visits*100 if visits else 0):.1f}%)\n"
        f"🚀 Bot starts captured: <b>{starts}</b>\n"
        f"📝 Registrations: <b>{regs}</b>\n"
        f"💳 Deposits: <b>{deps}</b>\n"
        f"💰 Deposit amount: <b>{float(r['deposit_amount'] or 0):,.2f}</b>"
    )
    if campaign:
        landing, _ = creator_urls(campaign)
        text += (
            f"\n\n🌐 Landing:\n<code>{landing}</code>"
            f"\n\n✈️ Telegram destination:\n<code>{BETROXY_BOT_URL}</code>"
            f"\n\n🌍 Website destination:\n<code>{BETROXY_WEB_URL}</code>"
        )
    return text


def campaign_links_keyboard(page=0, per_page=8):
    links = list_campaign_links()
    total_pages = max(1, (len(links)+per_page-1)//per_page)
    page = max(0, min(page, total_pages-1))
    subset = links[page*per_page:(page+1)*per_page]
    rows = []
    for item in subset:
        rows.append([InlineKeyboardButton(f"@{item['instagram_username']}", callback_data=f"campaign_creator:{item['agent_code']}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"campaign_links_page:{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="campaign_noop"))
    if page < total_pages-1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"campaign_links_page:{page+1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton("🏠 Campaign Tracker", callback_data="campaign_home")])
    return InlineKeyboardMarkup(rows), page, total_pages


def make_campaign_csv():
    links = list_campaign_links()
    stat_map = campaign_rows_by_code()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow([
        "Instagram Page","Landing Link","Agent Code",
        "Landing Visits","Unique Visitors","BetroxyBot Clicks","Betroxy.com Clicks",
        "Telegram CTR %","Website CTR %","Bot Starts","Registrations","Deposits","Deposit Amount"
    ])
    for item in links:
        r = stat_map.get(item["agent_code"].lower(), {})
        landing, _ = creator_urls(item)
        visits = int(r.get("landing_visits") or 0)
        tg = int(r.get("telegram_clicks") or 0)
        web = int(r.get("website_clicks") or 0)
        w.writerow([
            f"@{item['instagram_username']}", landing, item["agent_code"],
            visits, int(r.get("unique_visitors") or 0), tg, web,
            round(tg/visits*100, 2) if visits else 0,
            round(web/visits*100, 2) if visits else 0,
            int(r.get("telegram_starts") or 0),
            int(r.get("registrations") or 0), int(r.get("deposits") or 0),
            r.get("deposit_amount") or 0
        ])
    return out.getvalue()



async def campaign_edit_username_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return ConversationHandler.END

    code_value = q.data.split(":", 1)[1]
    row = campaign_link_by_code(code_value)
    if not row:
        await q.message.reply_text("❌ Creator link not found.")
        return ConversationHandler.END

    context.user_data["campaign_edit_code"] = code_value
    await q.message.reply_text(
        f"👤 <b>Edit Instagram Name</b>\n\n"
        f"Current: <code>@{row['instagram_username']}</code>\n\n"
        "Send the new Instagram username:",
        parse_mode=ParseMode.HTML,
    )
    return CAMPAIGN_EDIT_USERNAME


async def campaign_edit_username_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update):
        return ConversationHandler.END
    code_value = context.user_data.get("campaign_edit_code")
    try:
        row = update_campaign_username(code_value, update.message.text or "")
        if not row:
            raise ValueError("Creator link not found")
        await update.message.reply_text(
            f"✅ Instagram name updated to <b>@{row['instagram_username']}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=campaign_creator_menu(row["agent_code"]),
        )
    except Exception as exc:
        await update.message.reply_text(f"❌ {exc}")
        return CAMPAIGN_EDIT_USERNAME
    finally:
        if code_value:
            context.user_data.pop("campaign_edit_code", None)
    return ConversationHandler.END


async def campaign_edit_slug_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return ConversationHandler.END

    code_value = q.data.split(":", 1)[1]
    row = campaign_link_by_code(code_value)
    if not row:
        await q.message.reply_text("❌ Creator link not found.")
        return ConversationHandler.END

    context.user_data["campaign_edit_code"] = code_value
    await q.message.reply_text(
        f"🔗 <b>Edit Landing Slug</b>\n\n"
        f"Current: <code>{row['slug']}</code>\n"
        f"Current URL: <code>{PUBLIC_BASE_URL}/{row['slug']}</code>\n\n"
        "Send the new slug, for example: <code>5wides-new</code>",
        parse_mode=ParseMode.HTML,
    )
    return CAMPAIGN_EDIT_SLUG


async def campaign_edit_slug_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update):
        return ConversationHandler.END
    code_value = context.user_data.get("campaign_edit_code")
    try:
        row = update_campaign_slug(code_value, update.message.text or "")
        if not row:
            raise ValueError("Creator link not found")
        landing, _ = creator_urls(row)
        await update.message.reply_text(
            f"✅ Landing slug updated.\n\n"
            f"New URL:\n<code>{landing}</code>\n\n"
            "⚠️ The old landing URL will no longer open this creator page.",
            parse_mode=ParseMode.HTML,
            reply_markup=campaign_creator_menu(row["agent_code"]),
            disable_web_page_preview=True,
        )
    except Exception as exc:
        await update.message.reply_text(f"❌ {exc}")
        return CAMPAIGN_EDIT_SLUG
    finally:
        if code_value:
            context.user_data.pop("campaign_edit_code", None)
    return ConversationHandler.END


async def campaign_edit_code_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return ConversationHandler.END

    old_code = q.data.split(":", 1)[1]
    row = campaign_link_by_code(old_code)
    if not row:
        await q.message.reply_text("❌ Creator link not found.")
        return ConversationHandler.END

    context.user_data["campaign_edit_old_code"] = old_code
    await q.message.reply_text(
        f"🆔 <b>Edit Affiliate Code</b>\n\n"
        f"Current: <code>{old_code}</code>\n\n"
        "Send the new code.\n"
        "Use letters, numbers or underscore only.\n\n"
        "This also updates the Telegram referral link and preserves existing tracker history.",
        parse_mode=ParseMode.HTML,
    )
    return CAMPAIGN_EDIT_CODE


async def campaign_edit_code_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update):
        return ConversationHandler.END
    old_code = context.user_data.get("campaign_edit_old_code")
    try:
        row = update_campaign_agent_code(old_code, update.message.text or "")
        if not row:
            raise ValueError("Creator link not found")
        _, telegram = creator_urls(row)
        await update.message.reply_text(
            f"✅ Affiliate code updated.\n\n"
            f"New code: <code>{row['agent_code']}</code>\n"
            f"Telegram:\n<code>{telegram}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=campaign_creator_menu(row["agent_code"]),
            disable_web_page_preview=True,
        )
    except Exception as exc:
        await update.message.reply_text(f"❌ {exc}")
        return CAMPAIGN_EDIT_CODE
    finally:
        context.user_data.pop("campaign_edit_old_code", None)
    return ConversationHandler.END


async def campaign_add_single_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        await q.message.reply_text("❌ Admin access required.")
        return ConversationHandler.END
    await q.message.reply_text(
        "➕ <b>Create Creator Link</b>\n\nSend Instagram username, e.g. <code>new_cricket_page</code>.",
        parse_mode=ParseMode.HTML,
    )
    return CAMPAIGN_ADD_SINGLE


async def campaign_add_single_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update):
        return ConversationHandler.END
    username = (update.message.text or "").strip().lstrip("@")
    try:
        row, created = create_campaign_creator(username)
        landing, telegram = creator_urls(row)
        await update.message.reply_text(
            ("✅ Created" if created else "ℹ️ Already existed") +
            f"\n\nInstagram: <b>@{row['instagram_username']}</b>"
            f"\nLanding:\n<code>{landing}</code>"
            f"\n\nTelegram:\n<code>{telegram}</code>"
            f"\n\nAgent code: <code>{row['agent_code']}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=campaign_menu(),
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.exception("Single creator creation failed")
        await update.message.reply_text(f"❌ Could not create: {e}", reply_markup=campaign_menu())
    return ConversationHandler.END


async def campaign_add_bulk_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        await q.message.reply_text("❌ Admin access required.")
        return ConversationHandler.END
    await q.message.reply_text(
        "📚 <b>Bulk Create Links</b>\n\nPaste Instagram usernames, one per line. Up to 100 at once.",
        parse_mode=ParseMode.HTML,
    )
    return CAMPAIGN_ADD_BULK


async def campaign_add_bulk_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update):
        return ConversationHandler.END
    raw = update.message.text or ""
    names, seen = [], set()
    for line in raw.splitlines():
        item = line.strip().lstrip("@").split(",")[0].strip()
        if item and item.lower() not in seen:
            seen.add(item.lower())
            names.append(item)
    names = names[:100]
    created_rows, existing_rows, failed = [], [], []
    for username in names:
        try:
            row, created = create_campaign_creator(username)
            (created_rows if created else existing_rows).append(row)
        except Exception as e:
            failed.append((username, str(e)))
    lines = [
        "✅ <b>Bulk Generation Complete</b>",
        "",
        f"Created: <b>{len(created_rows)}</b>",
        f"Already existed: <b>{len(existing_rows)}</b>",
        f"Failed: <b>{len(failed)}</b>",
    ]
    for row in created_rows[:15]:
        landing, _ = creator_urls(row)
        lines.append(f"\n@{row['instagram_username']}\n<code>{landing}</code>")
    if len(created_rows) > 15:
        lines.append(f"\n…and {len(created_rows)-15} more. Use Export CSV for all.")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=campaign_menu(), disable_web_page_preview=True)
    return ConversationHandler.END


async def campaign_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("Cancelled.", reply_markup=campaign_menu())
    return ConversationHandler.END




def theme_menu():
    active = active_theme()
    rows = [
        [InlineKeyboardButton("⬆️ Upload New Design ZIP", callback_data="theme_upload")],
    ]
    recent = recent_themes(6)
    for t in recent:
        marker = "✅" if t["is_active"] else "🎨"
        rows.append([
            InlineKeyboardButton(
                f"{marker} #{t['id']} {t['name'][:28]}",
                callback_data=f"theme_view:{t['id']}"
            )
        ])
    rows.append([
        InlineKeyboardButton("↩️ Roll Back Previous Design", callback_data="theme_rollback")
    ])
    rows.append([InlineKeyboardButton("⬅️ Campaign Tracker", callback_data="campaign_home")])
    return InlineKeyboardMarkup(rows)


def theme_detail_menu(theme_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👁 Preview", callback_data=f"theme_preview:{theme_id}"),
            InlineKeyboardButton("🚀 Publish", callback_data=f"theme_publish:{theme_id}"),
        ],
        [InlineKeyboardButton("⬅️ Design Manager", callback_data="theme_home")],
    ])


async def theme_upload_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        await q.message.reply_text("❌ Admin access required.")
        return ConversationHandler.END
    await q.message.reply_text(
        "🎨 <b>Upload New Landing Design</b>\n\n"
        "Send a ZIP containing <code>index.html</code> and any CSS/JS/images.\n\n"
        "For tracked buttons, use either:\n"
        '<code>href="{{TELEGRAM_URL}}"</code> / <code>href="{{WEBSITE_URL}}"</code>\n'
        "or add <code>data-betroxy=\"telegram\"</code> / <code>data-betroxy=\"website\"</code>.\n\n"
        "The upload is saved as a DRAFT first. You can preview it before publishing.",
        parse_mode=ParseMode.HTML,
    )
    return THEME_UPLOAD


async def theme_upload_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update):
        return ConversationHandler.END
    doc = update.message.document
    if not doc:
        await update.message.reply_text("❌ Please send the theme as a ZIP file.")
        return THEME_UPLOAD
    filename = doc.file_name or "theme.zip"
    if not filename.lower().endswith(".zip"):
        await update.message.reply_text("❌ File must be a .zip.")
        return THEME_UPLOAD
    if doc.file_size and doc.file_size > THEME_UPLOAD_MAX_MB * 1024 * 1024:
        await update.message.reply_text(f"❌ ZIP is larger than {THEME_UPLOAD_MAX_MB} MB.")
        return ConversationHandler.END

    try:
        tg_file = await context.bot.get_file(doc.file_id)
        data = bytes(await tg_file.download_as_bytearray())
        theme = save_theme_zip(data, filename, update.effective_user.id)
        sample = sample_creator_for_preview()
        preview = "No creator page exists yet."
        if sample:
            key = f"?key={TRACKER_API_SECRET}" if TRACKER_API_SECRET else ""
            preview = f"{PUBLIC_BASE_URL}/preview/{theme['id']}/{sample['slug']}{key}"
        await update.message.reply_text(
            "✅ <b>Design uploaded as DRAFT</b>\n\n"
            f"Theme: <b>#{theme['id']} {html.escape(theme['name'])}</b>\n\n"
            f"Preview:\n<code>{preview}</code>\n\n"
            "Nothing live has changed yet. Tap Publish when ready.",
            parse_mode=ParseMode.HTML,
            reply_markup=theme_detail_menu(theme["id"]),
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.exception("Theme upload failed")
        await update.message.reply_text(
            f"❌ Theme upload failed:\n<code>{html.escape(str(e))}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=theme_menu(),
        )
    return ConversationHandler.END


async def theme_upload_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("Upload cancelled.", reply_markup=theme_menu())
    return ConversationHandler.END



# ============================================================
# CALLBACKS
# ============================================================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "home":
        await q.message.reply_text(
            public_welcome_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=public_menu(q.from_user.id),
            disable_web_page_preview=True,
        )
        return

    if data == "support":
        await q.message.reply_text(
            "🎧 <b>Betroxy Support</b>\n\nChoose your preferred support channel:",
            parse_mode=ParseMode.HTML,
            reply_markup=support_menu(),
        )
        return

    if data == "refer_friend":
        agent = find_agent_by_telegram_user_id(q.from_user.id)
        if agent:
            link = f"https://t.me/{BOT_USERNAME}?start=agent_{agent['code']}"
        else:
            link = f"https://t.me/{BOT_USERNAME}"

        await q.message.reply_text(
            "👥 <b>Refer a Friend</b>\n\n"
            "Share this link:\n\n"
            f"<code>{link}</code>",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
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

    if not is_admin(q.from_user.id):
        await q.message.reply_text("❌ Admin access required.")
        return

    if data == "theme_home":
        active = active_theme()
        active_text = f"#{active['id']} {active['name']}" if active else "Default built-in design"
        await q.message.reply_text(
            "🎨 <b>Landing Design Manager</b>\n\n"
            f"Currently live: <b>{html.escape(active_text)}</b>\n\n"
            "Upload, preview, publish or roll back designs directly from Telegram.",
            parse_mode=ParseMode.HTML,
            reply_markup=theme_menu(),
        )
        return

    if data.startswith("theme_view:"):
        theme_id = int(data.split(":",1)[1])
        t = get_theme(theme_id)
        if not t:
            await q.message.reply_text("❌ Theme not found.", reply_markup=theme_menu())
            return
        await q.message.reply_text(
            f"🎨 <b>Theme #{t['id']}</b>\n"
            f"Name: <b>{html.escape(t['name'])}</b>\n"
            f"Status: <b>{'LIVE' if t['is_active'] else 'DRAFT / HISTORY'}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=theme_detail_menu(t["id"]),
        )
        return

    if data.startswith("theme_preview:"):
        theme_id = int(data.split(":",1)[1])
        sample = sample_creator_for_preview()
        if not sample:
            await q.message.reply_text("Create at least one creator landing page first.")
            return
        key = f"?key={TRACKER_API_SECRET}" if TRACKER_API_SECRET else ""
        url = f"{PUBLIC_BASE_URL}/preview/{theme_id}/{sample['slug']}{key}"
        await q.message.reply_text(
            f"👁 <b>Preview Theme #{theme_id}</b>\n\n<code>{url}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=theme_detail_menu(theme_id),
            disable_web_page_preview=True,
        )
        return

    if data.startswith("theme_publish:"):
        theme_id = int(data.split(":",1)[1])
        if not publish_theme(theme_id):
            await q.message.reply_text("❌ Theme not found.", reply_markup=theme_menu())
            return
        await q.message.reply_text(
            f"🚀 <b>Theme #{theme_id} is now LIVE</b>\n\n"
            "All creator landing pages use it immediately. No Railway redeploy is required.",
            parse_mode=ParseMode.HTML,
            reply_markup=theme_menu(),
        )
        return

    if data == "theme_rollback":
        t = rollback_theme()
        if not t:
            await q.message.reply_text(
                "No previous published design is available.",
                reply_markup=theme_menu(),
            )
            return
        await q.message.reply_text(
            f"↩️ Rolled back successfully.\n\nNow live: <b>#{t['id']} {html.escape(t['name'])}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=theme_menu(),
        )
        return

    if data == "campaign_noop":
        return

    if data == "campaign_home":
        await q.message.reply_text(campaign_overview_text(), parse_mode=ParseMode.HTML, reply_markup=campaign_menu())
        return

    if data == "campaign_report":
        rows = instagram_tracker_stats()
        lines = ["📊 <b>Full Campaign Report</b>", ""]
        for r in rows[:25]:
            lines.append(f"@{r['name']}: <b>{int(r['landing_visits'] or 0)}</b> visits • {int(r['telegram_clicks'] or 0)} bot • {int(r['website_clicks'] or 0)} web • {int(r['deposits'] or 0)} dep")
        if len(rows) > 25:
            lines.append("\nUse Creator Links for all pages.")
        await q.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=campaign_menu())
        return

    if data == "campaign_today":
        s = tracker_today_totals()
        await q.message.reply_text(
            "📅 <b>Today's Campaign</b>\n\n"
            f"👁 Landing visits: <b>{s['landing_visits']}</b>\n"
            f"👤 Unique visitors: <b>{s['unique_visitors']}</b>\n"
            f"✈️ BetroxyBot clicks: <b>{s['telegram_clicks']}</b>\n"
            f"🌐 Betroxy.com clicks: <b>{s['website_clicks']}</b>\n"
            f"🚀 Bot starts captured: <b>{s['starts']}</b>\n"
            f"📝 Registrations: <b>{s['registrations']}</b>\n"
            f"💳 Deposits: <b>{s['deposits']}</b>\n"
            f"💰 Deposit amount: <b>{float(s['deposit_amount'] or 0):,.2f}</b>",
            parse_mode=ParseMode.HTML, reply_markup=campaign_menu()
        )
        return

    if data == "campaign_top":
        rows = instagram_tracker_stats()
        lines = ["🏆 <b>Top Performing Pages</b>", ""]
        for i, r in enumerate(rows[:10], 1):
            lines.append(f"{i}. @{r['name']} — <b>{int(r['landing_visits'] or 0)}</b> visits • {int(r['telegram_clicks'] or 0)} bot • {int(r['website_clicks'] or 0)} web")
        await q.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=campaign_menu())
        return

    if data == "campaign_links":
        kb, page, total_pages = campaign_links_keyboard(0)
        await q.message.reply_text(f"🔗 <b>Creator Tracking Links</b>\n\nPage {page+1} of {total_pages}. Tap a creator.", parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if data.startswith("campaign_links_page:"):
        page = int(data.split(":",1)[1])
        kb, page, total_pages = campaign_links_keyboard(page)
        await q.message.reply_text(f"🔗 <b>Creator Tracking Links</b>\n\nPage {page+1} of {total_pages}.", parse_mode=ParseMode.HTML, reply_markup=kb)
        return


    if data.startswith("campaign_edit:"):
        c = data.split(":", 1)[1]
        row = campaign_link_by_code(c)
        if not row:
            await q.message.reply_text("❌ Creator link not found.", reply_markup=campaign_menu())
            return
        await q.message.reply_text(
            f"✏️ <b>Edit Creator Link</b>\n\n"
            f"Instagram: <b>@{row['instagram_username']}</b>\n"
            f"Slug: <code>{row['slug']}</code>\n"
            f"Affiliate code: <code>{row['agent_code']}</code>\n\n"
            "Choose what you want to edit:",
            parse_mode=ParseMode.HTML,
            reply_markup=campaign_edit_menu(c),
        )
        return

    if data.startswith("campaign_delete_confirm:"):
        c = data.split(":", 1)[1]
        row = campaign_link_by_code(c)
        if not row:
            await q.message.reply_text("❌ Creator link not found.", reply_markup=campaign_menu())
            return
        await q.message.reply_text(
            "⚠️ <b>Delete Creator Link?</b>\n\n"
            f"Instagram: <b>@{row['instagram_username']}</b>\n"
            f"Landing: <code>{PUBLIC_BASE_URL}/{row['slug']}</code>\n\n"
            "This removes the creator landing link from the campaign.\n"
            "The affiliate account and historical tracking data will be kept.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "🗑 YES, DELETE LINK",
                    callback_data=f"campaign_delete_yes:{c}"
                )],
                [InlineKeyboardButton("❌ Cancel", callback_data=f"campaign_creator:{c}")],
            ]),
        )
        return

    if data.startswith("campaign_delete_yes:"):
        c = data.split(":", 1)[1]
        deleted = delete_campaign_link(c)
        if not deleted:
            await q.message.reply_text("❌ Creator link not found.", reply_markup=campaign_menu())
            return
        await q.message.reply_text(
            f"🗑 <b>Creator Link Deleted</b>\n\n"
            f"Instagram: <b>@{deleted['instagram_username']}</b>\n"
            f"Old landing: <code>{PUBLIC_BASE_URL}/{deleted['slug']}</code>\n\n"
            "Affiliate account and historical tracking data were preserved.",
            parse_mode=ParseMode.HTML,
            reply_markup=campaign_menu(),
            disable_web_page_preview=True,
        )
        return

    if data.startswith("campaign_creator:"):
        c = data.split(":",1)[1]
        await q.message.reply_text(creator_report_text(c), parse_mode=ParseMode.HTML, reply_markup=campaign_creator_menu(c), disable_web_page_preview=True)
        return

    if data == "campaign_export":
        b = io.BytesIO(make_campaign_csv().encode("utf-8-sig"))
        b.name = "BETROXY_Instagram_Campaign_Live_Report.csv"
        await q.message.reply_document(document=b, caption="📥 Campaign report + creator links")
        return

    if data == "admin_home":
        await q.message.reply_text(
            "🛠 <b>Betroxy Admin Control Center</b>\n\nManage affiliates and Instagram campaigns using the buttons below.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_menu(),
        )
        return

    if data == "admin_agents":
        agents = list_agents()
        if not agents:
            await q.message.reply_text("No affiliates created yet.", reply_markup=admin_menu())
            return

        rows = []
        for a in agents[:20]:
            status = "✅" if a["is_active"] else "⛔"
            linked = "🔐" if a.get("telegram_user_id") else ""
            label = f"{status}{linked} {a['name']} • {a['referral_count']} users"
            rows.append(
                [
                    InlineKeyboardButton(
                        label,
                        callback_data=f"agent_view:{a['code']}",
                    )
                ]
            )
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

        writer.writerow(
            [
                "Agent Name",
                "Agent Code",
                "Commission %",
                "Active",
                "Telegram User ID",
                "Username",
                "First Name",
                "Last Name",
                "Joined At",
            ]
        )

        for r in rows:
            writer.writerow(
                [
                    r["agent_name"],
                    r["agent_code"],
                    r["commission_rate"],
                    r["is_active"],
                    r["telegram_user_id"],
                    r["telegram_username"],
                    r["first_name"],
                    r["last_name"],
                    r["joined_at"],
                ]
            )

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
            f"<code>{link}</code>",
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
        code = data.split(":", 1)[1]
        stats = get_agent_stats(code)
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
            "assigned to this affiliate.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🗑 YES, DELETE PERMANENTLY",
                            callback_data=f"agent_delete_yes:{a['code']}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "❌ Cancel",
                            callback_data=f"agent_view:{a['code']}",
                        )
                    ],
                ]
            ),
        )
        return

    if data.startswith("agent_delete_yes:"):
        code = data.split(":", 1)[1]
        deleted, count = delete_agent_permanently(code)
        if not deleted:
            await q.message.reply_text("❌ Agent not found.")
            return

        await q.message.reply_text(
            "🗑 <b>Affiliate Permanently Deleted</b>\n\n"
            f"Name: {deleted['name']}\n"
            f"Code: <code>{deleted['code']}</code>\n"
            f"Referral records deleted: {count}",
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
        "Enter affiliate code.\n\n"
        "Example: samratking\n\n"
        "Use letters, numbers or underscore only."
    )
    return ADD_CODE


async def add_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip().lower()

    if not re.fullmatch(r"[a-z0-9_]{2,40}", code):
        await update.message.reply_text(
            "❌ Invalid code. Use only letters, numbers or underscore."
        )
        return ADD_CODE

    if find_agent_by_code(code):
        await update.message.reply_text(
            "❌ This code already exists. Enter another code:"
        )
        return ADD_CODE

    context.user_data["new_agent_code"] = code
    await update.message.reply_text(
        "Enter commission rate.\nExamples: 0, 5, 7.5, 10"
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
# SEARCH
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
# EDIT NAME / CODE / URL / RATE
# ============================================================

async def edit_name_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not is_admin(q.from_user.id):
        return ConversationHandler.END

    code = q.data.split(":", 1)[1]
    context.user_data["edit_code"] = code

    await q.message.reply_text(
        f"✏️ Enter new name for <code>{code}</code>:",
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


async def edit_code_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not is_admin(q.from_user.id):
        return ConversationHandler.END

    old_code = q.data.split(":", 1)[1]
    context.user_data["edit_code_old"] = old_code

    await q.message.reply_text(
        f"🆔 Enter new affiliate code for <code>{old_code}</code>:\n\n"
        "Changing the code also changes the referral link.",
        parse_mode=ParseMode.HTML,
    )
    return EDIT_CODE


async def edit_code_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    old_code = context.user_data["edit_code_old"]
    new_code = update.message.text.strip().lower()

    if not re.fullmatch(r"[a-z0-9_]{2,40}", new_code):
        await update.message.reply_text("❌ Invalid code.")
        return EDIT_CODE

    existing = find_agent_by_code(new_code)
    if existing and existing["code"].lower() != old_code.lower():
        await update.message.reply_text("❌ Code already exists.")
        return EDIT_CODE

    try:
        a = update_agent_code(old_code, new_code)
    except psycopg.errors.UniqueViolation:
        await update.message.reply_text("❌ Code already exists.")
        return EDIT_CODE

    if not a:
        await update.message.reply_text("❌ Agent not found.")
        context.user_data.clear()
        return ConversationHandler.END

    new_link = f"https://t.me/{BOT_USERNAME}?start=agent_{a['code']}"

    await update.message.reply_text(
        "✅ <b>Affiliate Code Updated</b>\n\n"
        f"New Code: <code>{a['code']}</code>\n\n"
        f"New Referral Link:\n<code>{new_link}</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=agent_action_menu(a["code"], a["is_active"]),
        disable_web_page_preview=True,
    )

    context.user_data.clear()
    return ConversationHandler.END


async def edit_url_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not is_admin(q.from_user.id):
        return ConversationHandler.END

    code = q.data.split(":", 1)[1]
    context.user_data["edit_url_code"] = code

    await q.message.reply_text(
        f"🔗 Enter custom URL for <code>{code}</code>.\n\n"
        "Example:\nhttps://betroxy.com/creator\n\n"
        "Send <code>clear</code> to remove the URL.",
        parse_mode=ParseMode.HTML,
    )
    return EDIT_URL


async def edit_url_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = context.user_data["edit_url_code"]
    value = update.message.text.strip()

    if value.lower() == "clear":
        value = ""
    elif not re.fullmatch(r"https://[^\s]+", value):
        await update.message.reply_text("❌ Enter a valid HTTPS URL.")
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
# NORMAL CHAT
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
        public_welcome_text(),
        parse_mode=ParseMode.HTML,
        reply_markup=public_menu(user_id),
        disable_web_page_preview=True,
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Bot error", exc_info=context.error)


# ============================================================
# OPEN APP BUTTON
# ============================================================

async def post_init(app: Application):
    await app.bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(
            text="Open App",
            web_app=WebAppInfo(url=APP_URL),
        )
    )

    await app.bot.set_my_commands(
        [
            ("start", "Open Betroxy"),
            ("affiliate", "Affiliate dashboard"),
            ("igstats", "Instagram link tracker"),
            ("igtoday", "Today Instagram tracking"),
        ]
    )


# ============================================================
# MAIN
# ============================================================

def main():
    init_db()

    ensure_polished_builtin_theme_once()
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    add_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(add_start, pattern=r"^admin_add$")
        ],
        states={
            ADD_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_name)
            ],
            ADD_CODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_code)
            ],
            ADD_RATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_rate)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    campaign_single_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(campaign_add_single_start, pattern=r"^campaign_add_single$")],
        states={CAMPAIGN_ADD_SINGLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, campaign_add_single_save)]},
        fallbacks=[CommandHandler("cancel", campaign_cancel)],
    )

    campaign_edit_username_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                campaign_edit_username_start,
                pattern=r"^campaign_edit_username:"
            )
        ],
        states={
            CAMPAIGN_EDIT_USERNAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, campaign_edit_username_save)
            ]
        },
        fallbacks=[CommandHandler("cancel", campaign_cancel)],
    )

    campaign_edit_slug_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                campaign_edit_slug_start,
                pattern=r"^campaign_edit_slug:"
            )
        ],
        states={
            CAMPAIGN_EDIT_SLUG: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, campaign_edit_slug_save)
            ]
        },
        fallbacks=[CommandHandler("cancel", campaign_cancel)],
    )

    campaign_edit_code_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                campaign_edit_code_start,
                pattern=r"^campaign_edit_code:"
            )
        ],
        states={
            CAMPAIGN_EDIT_CODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, campaign_edit_code_save)
            ]
        },
        fallbacks=[CommandHandler("cancel", campaign_cancel)],
    )

    campaign_bulk_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(campaign_add_bulk_start, pattern=r"^campaign_add_bulk$")],
        states={CAMPAIGN_ADD_BULK: [MessageHandler(filters.TEXT & ~filters.COMMAND, campaign_add_bulk_save)]},
        fallbacks=[CommandHandler("cancel", campaign_cancel)],
    )

    theme_upload_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(theme_upload_start, pattern=r"^theme_upload$")],
        states={
            THEME_UPLOAD: [
                MessageHandler(filters.Document.ALL, theme_upload_save)
            ],
        },
        fallbacks=[CommandHandler("cancel", theme_upload_cancel)],
    )

    search_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(search_start, pattern=r"^admin_search$")
        ],
        states={
            SEARCH_CODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, search_code)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    edit_name_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(edit_name_start, pattern=r"^agent_edit_name:")
        ],
        states={
            EDIT_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_name_save)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    edit_code_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(edit_code_start, pattern=r"^agent_edit_code:")
        ],
        states={
            EDIT_CODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_code_save)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    edit_url_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(edit_url_start, pattern=r"^agent_edit_url:")
        ],
        states={
            EDIT_URL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_url_save)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    edit_rate_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(edit_rate_start, pattern=r"^agent_edit_rate:")
        ],
        states={
            EDIT_RATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_rate_save)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("affiliate", affiliate_command))
    app.add_handler(CommandHandler("agent", agent_stats_command))
    app.add_handler(CommandHandler("agent_rate", agent_rate_command))
    app.add_handler(CommandHandler("agent_access", agent_access_command))
    app.add_handler(CommandHandler("igstats", igstats_command))
    app.add_handler(CommandHandler("igtoday", igtoday_command))
    app.add_handler(
        CommandHandler(
            "create_instagram_affiliates",
            create_instagram_affiliates,
        )
    )

    app.add_handler(add_conv)
    app.add_handler(campaign_single_conv)
    app.add_handler(campaign_edit_username_conv)
    app.add_handler(campaign_edit_slug_conv)
    app.add_handler(campaign_edit_code_conv)
    app.add_handler(campaign_bulk_conv)
    app.add_handler(theme_upload_conv)
    app.add_handler(search_conv)
    app.add_handler(edit_name_conv)
    app.add_handler(edit_code_conv)
    app.add_handler(edit_url_conv)
    app.add_handler(edit_rate_conv)

    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            chat_handler,
        )
    )
    app.add_error_handler(error_handler)

    Thread(target=run_tracker_api, daemon=True).start()
    logger.info("Betroxy Official Bot + Instagram tracker starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
