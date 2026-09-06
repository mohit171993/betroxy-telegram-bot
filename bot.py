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
import json
import time
from urllib.parse import urlencode, urlparse
from datetime import datetime, timezone
from threading import Thread

from flask import Flask, jsonify, request, redirect, Response

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

import requests
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
IG_SESSIONID = os.getenv("IG_SESSIONID", "").strip()
IG_CHECK_INTERVAL_SECONDS = int(os.getenv("IG_CHECK_INTERVAL_SECONDS", "3600"))
IG_CHECK_TIMEOUT = int(os.getenv("IG_CHECK_TIMEOUT", "18"))
IG_WEB_APP_ID = os.getenv("IG_WEB_APP_ID", "936619743392459").strip()
ENABLE_RAILWAY_FREE_CHECKER = os.getenv("ENABLE_RAILWAY_FREE_CHECKER", "0").strip() == "1"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing")
if not ADMIN_ID:
    raise RuntimeError("ADMIN_ID is missing")

BOT_USERNAME = "BetroxyOfficialBot"  # admin/referral bot; landing CTA uses @BetroxyBot

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
CAMPAIGN_EDIT_SOURCE = 35
CAMPAIGN_DISABLE_BY_LINK = 36
CAMPAIGN_DELETE_BY_LINK = 37
VERIFY_PROOF_UPLOAD = 38
CAMPAIGN_SYNC_FINAL = 39
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
                ALTER TABLE campaign_links
                ADD COLUMN IF NOT EXISTS source_type TEXT DEFAULT 'instagram'
                """
            )
            cur.execute(
                """
                UPDATE campaign_links
                SET source_type='instagram'
                WHERE source_type IS NULL OR TRIM(source_type)=''
                """
            )

            cur.execute(
                """
                ALTER TABLE campaign_links
                ADD COLUMN IF NOT EXISTS source_url TEXT
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
                CREATE TABLE IF NOT EXISTS campaign_verification (
                    id BIGSERIAL PRIMARY KEY,
                    campaign_link_id BIGINT NOT NULL REFERENCES campaign_links(id) ON DELETE CASCADE,
                    campaign_day INTEGER DEFAULT 1,
                    bio_status TEXT DEFAULT 'pending',
                    only_our_link_status TEXT DEFAULT 'pending',
                    story_status TEXT DEFAULT 'pending',
                    story_link_status TEXT DEFAULT 'pending',
                    proof_file_id TEXT,
                    proof_type TEXT,
                    proof_caption TEXT,
                    checked_by BIGINT,
                    checked_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(campaign_link_id, campaign_day)
                )
                """
            )
            cur.execute(
                """
                ALTER TABLE campaign_verification
                ADD COLUMN IF NOT EXISTS auto_checked_at TIMESTAMPTZ
                """
            )
            cur.execute(
                """
                ALTER TABLE campaign_verification
                ADD COLUMN IF NOT EXISTS auto_check_status TEXT DEFAULT 'pending'
                """
            )
            cur.execute(
                """
                ALTER TABLE campaign_verification
                ADD COLUMN IF NOT EXISTS auto_check_detail TEXT
                """
            )
            cur.execute(
                """
                ALTER TABLE campaign_verification
                ADD COLUMN IF NOT EXISTS detected_bio_links TEXT
                """
            )
            cur.execute(
                """
                ALTER TABLE campaign_verification
                ADD COLUMN IF NOT EXISTS detected_story_count INTEGER DEFAULT 0
                """
            )
            cur.execute(
                """
                ALTER TABLE campaign_verification
                ADD COLUMN IF NOT EXISTS checker_mode TEXT
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_campaign_verification_link_day
                ON campaign_verification(campaign_link_id, campaign_day)
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
                CREATE TABLE IF NOT EXISTS verifier_control (
                    id INTEGER PRIMARY KEY,
                    run_token TEXT,
                    requested_at TIMESTAMPTZ,
                    claimed_at TIMESTAMPTZ,
                    completed_at TIMESTAMPTZ,
                    status TEXT DEFAULT 'idle',
                    result_summary TEXT
                )
                """
            )
            cur.execute(
                """
                INSERT INTO verifier_control (id, status)
                VALUES (1, 'idle')
                ON CONFLICT (id) DO NOTHING
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
                InlineKeyboardButton("⛔ Disable by Link", callback_data="campaign_disable_by_link"),
                InlineKeyboardButton("🗑 Delete by Link", callback_data="campaign_delete_by_link"),
            ],
            [
                InlineKeyboardButton("📚 Bulk Create Links", callback_data="campaign_add_bulk"),
                InlineKeyboardButton("✅ Sync Final Promoted Links", callback_data="campaign_sync_final"),
            ],
            [
                InlineKeyboardButton("📥 Export CSV", callback_data="campaign_export"),
            ],
            [
                InlineKeyboardButton("🔄 Run Bio Check Now", callback_data="verify_request_local_run"),
                InlineKeyboardButton("📊 Auto Report", callback_data="verify_auto_report:1"),
            ],
            [
                InlineKeyboardButton("✅ Verification Center", callback_data="verify_home"),
                InlineKeyboardButton("💻 Checker Status", callback_data="verify_local_status"),
            ],
            [
                InlineKeyboardButton("📄 Download PDF", callback_data="campaign_pdf"),
                InlineKeyboardButton("🎨 Landing Design", callback_data="theme_home"),
            ],
            [InlineKeyboardButton("⬅️ Admin Panel", callback_data="admin_home")],
        ]
    )



VERIFY_STATUS_LABEL = {
    "verified": "✅",
    "missing": "❌",
    "issue": "⚠️",
    "pending": "⏳",
}


def get_verification_row(campaign_link_id, day=1):
    day = max(1, min(int(day), 7))
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO campaign_verification (campaign_link_id, campaign_day)
                VALUES (%s, %s)
                ON CONFLICT (campaign_link_id, campaign_day) DO NOTHING
                """,
                (campaign_link_id, day),
            )
            cur.execute(
                """
                SELECT * FROM campaign_verification
                WHERE campaign_link_id=%s AND campaign_day=%s
                LIMIT 1
                """,
                (campaign_link_id, day),
            )
            row = cur.fetchone()
            conn.commit()
            return row


def update_verification_status(campaign_link_id, day, field, status, checked_by=None):
    allowed_fields = {
        "bio_status",
        "only_our_link_status",
        "story_status",
        "story_link_status",
    }
    if field not in allowed_fields:
        raise ValueError("Invalid verification field")
    if status not in {"verified", "missing", "issue", "pending"}:
        raise ValueError("Invalid verification status")

    get_verification_row(campaign_link_id, day)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE campaign_verification
                SET {field}=%s, checked_by=%s, checked_at=NOW()
                WHERE campaign_link_id=%s AND campaign_day=%s
                RETURNING *
                """,
                (status, checked_by, campaign_link_id, day),
            )
            row = cur.fetchone()
            conn.commit()
            return row


def save_verification_proof(campaign_link_id, day, file_id, proof_type, caption, checked_by=None):
    get_verification_row(campaign_link_id, day)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE campaign_verification
                SET proof_file_id=%s,
                    proof_type=%s,
                    proof_caption=%s,
                    checked_by=%s,
                    checked_at=NOW()
                WHERE campaign_link_id=%s AND campaign_day=%s
                RETURNING *
                """,
                (
                    file_id,
                    proof_type,
                    caption,
                    checked_by,
                    campaign_link_id,
                    day,
                ),
            )
            row = cur.fetchone()
            conn.commit()
            return row



def _normalized_url_for_compare(value):
    value = (value or "").strip()
    if not value:
        return ""
    if not re.match(r"^https?://", value, flags=re.I):
        value = "https://" + value
    try:
        p = urlparse(value)
        host = (p.netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        path = re.sub(r"/+$", "", p.path or "")
        return f"{host}{path}".lower()
    except Exception:
        return value.lower().rstrip("/")


def _collect_instagram_bio_links(user_obj):
    links = []

    def add(v):
        if isinstance(v, str) and v.strip():
            links.append(v.strip())

    add(user_obj.get("external_url"))
    add(user_obj.get("external_url_linkshimmed"))

    for key in ("bio_links", "bio_links_with_metadata"):
        val = user_obj.get(key)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    for k in ("url", "lynx_url", "link_url", "external_url"):
                        add(item.get(k))
                elif isinstance(item, str):
                    add(item)

    biography = user_obj.get("biography") or ""
    for found in re.findall(r"https?://[^\s<>\"]+", biography):
        add(found)

    # De-duplicate by normalized destination.
    out = []
    seen = set()
    for link in links:
        normalized = _normalized_url_for_compare(link)
        if normalized and normalized not in seen:
            seen.add(normalized)
            out.append(link)
    return out


def _ig_headers():
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "X-IG-App-ID": IG_WEB_APP_ID,
        "Referer": "https://www.instagram.com/",
    }


def _ig_cookies():
    return {"sessionid": IG_SESSIONID} if IG_SESSIONID else {}


def fetch_instagram_profile_free(username):
    """
    Best-effort, zero-paid-API profile check using Instagram's web endpoint.
    It is intentionally conservative: any block/rate-limit becomes UNKNOWN,
    not a false 'missing'.
    """
    username = str(username).strip().lstrip("@")
    urls = [
        f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}",
        f"https://i.instagram.com/api/v1/users/web_profile_info/?username={username}",
    ]
    last_error = None
    for url in urls:
        try:
            r = requests.get(
                url,
                headers=_ig_headers(),
                cookies=_ig_cookies(),
                timeout=IG_CHECK_TIMEOUT,
            )
            if r.status_code == 200:
                data = r.json()
                user = (data.get("data") or {}).get("user")
                if user:
                    return {"ok": True, "user": user, "status_code": 200}
            last_error = f"HTTP {r.status_code}"
        except Exception as exc:
            last_error = str(exc)
    return {"ok": False, "error": last_error or "Instagram profile check unavailable"}


def _extract_user_id(user_obj):
    for key in ("id", "pk", "fbid"):
        val = user_obj.get(key)
        if val:
            return str(val)
    return None


def fetch_instagram_stories_free(user_id):
    """
    Stories generally require an authenticated Instagram web session.
    If IG_SESSIONID is absent, return 'unknown' rather than false missing.
    """
    if not IG_SESSIONID:
        return {
            "ok": False,
            "needs_session": True,
            "error": "IG_SESSIONID not configured; automatic Story inspection unavailable",
        }
    if not user_id:
        return {"ok": False, "error": "Could not resolve Instagram user id"}

    endpoints = [
        f"https://www.instagram.com/api/v1/feed/reels_media/?reel_ids={user_id}",
        f"https://i.instagram.com/api/v1/feed/reels_media/?reel_ids={user_id}",
    ]
    last_error = None
    for url in endpoints:
        try:
            r = requests.get(
                url,
                headers=_ig_headers(),
                cookies=_ig_cookies(),
                timeout=IG_CHECK_TIMEOUT,
            )
            if r.status_code == 200:
                data = r.json()
                reels = data.get("reels") or {}
                reel = reels.get(str(user_id)) or reels.get(int(user_id)) if str(user_id).isdigit() else None
                if not reel and isinstance(reels, dict) and reels:
                    reel = next(iter(reels.values()))
                items = (reel or {}).get("items") or []
                return {"ok": True, "items": items, "raw": data}
            last_error = f"HTTP {r.status_code}"
        except Exception as exc:
            last_error = str(exc)
    return {"ok": False, "error": last_error or "Story check unavailable"}


def _story_contains_assigned_link(item, assigned_url):
    target = _normalized_url_for_compare(assigned_url)
    if not target:
        return False
    try:
        blob = json.dumps(item, ensure_ascii=False).lower()
    except Exception:
        blob = str(item).lower()

    # Check exact normalized target and domain/path variants.
    candidates = {
        target,
        target.replace("https://", "").replace("http://", ""),
        "batraxy.com/" + assigned_url.rstrip("/").split("/")[-1].lower(),
    }
    return any(c and c in blob for c in candidates)


def current_campaign_day(row):
    created = row.get("created_at")
    if not created:
        return 1
    try:
        today = datetime.now(timezone.utc).date()
        created_day = created.date()
        return max(1, min(7, (today - created_day).days + 1))
    except Exception:
        return 1


def save_auto_verification_result(
    campaign_link_id,
    day,
    bio_status=None,
    only_status=None,
    story_status=None,
    story_link_status=None,
    auto_status="checked",
    detail="",
    bio_links=None,
    story_count=0,
    checker_mode="free_web",
):
    get_verification_row(campaign_link_id, day)
    with get_db() as conn:
        with conn.cursor() as cur:
            fields = [
                "auto_checked_at=NOW()",
                "auto_check_status=%s",
                "auto_check_detail=%s",
                "detected_bio_links=%s",
                "detected_story_count=%s",
                "checker_mode=%s",
                "checked_at=NOW()",
            ]
            params = [
                auto_status,
                detail,
                json.dumps(bio_links or [], ensure_ascii=False),
                int(story_count or 0),
                checker_mode,
            ]

            # Only overwrite compliance fields when we actually obtained a
            # trustworthy result for that part of the check.
            if bio_status is not None:
                fields.append("bio_status=%s")
                params.append(bio_status)
            if only_status is not None:
                fields.append("only_our_link_status=%s")
                params.append(only_status)
            if story_status is not None:
                fields.append("story_status=%s")
                params.append(story_status)
            if story_link_status is not None:
                fields.append("story_link_status=%s")
                params.append(story_link_status)

            params.extend([campaign_link_id, day])
            cur.execute(
                f"""
                UPDATE campaign_verification
                SET {", ".join(fields)}
                WHERE campaign_link_id=%s AND campaign_day=%s
                """,
                params,
            )
        conn.commit()


def run_free_instagram_check_for_link(row):
    source = (row.get("source_type") or "instagram").lower()
    if source != "instagram":
        return {
            "ok": False,
            "skipped": True,
            "detail": f"Free Instagram checker skipped source={source}",
        }

    username = str(row["instagram_username"]).strip().lstrip("@")
    assigned_url = f"{PUBLIC_BASE_URL}/{row['slug']}"
    day = current_campaign_day(row)
    profile = fetch_instagram_profile_free(username)

    if not profile.get("ok"):
        save_auto_verification_result(
            row["id"], day,
            auto_status="unknown",
            detail=f"Profile check unavailable: {profile.get('error')}",
            checker_mode="free_web",
        )
        return {"ok": False, "detail": profile.get("error"), "day": day}

    user = profile["user"]
    bio_links = _collect_instagram_bio_links(user)
    normalized_target = _normalized_url_for_compare(assigned_url)
    normalized_links = [_normalized_url_for_compare(x) for x in bio_links]

    bio_ok = normalized_target in normalized_links
    bio_status = "verified" if bio_ok else "missing"

    # "Only our link" means the assigned campaign URL is present and no other
    # distinct external destinations were detected.
    distinct_links = {x for x in normalized_links if x}
    only_status = (
        "verified"
        if bio_ok and distinct_links == {normalized_target}
        else "issue"
    )

    user_id = _extract_user_id(user)
    stories = fetch_instagram_stories_free(user_id)

    if stories.get("ok"):
        items = stories.get("items") or []
        story_count = len(items)
        story_status = "verified" if story_count > 0 else "missing"
        story_has_link = any(
            _story_contains_assigned_link(item, assigned_url)
            for item in items
        )
        story_link_status = (
            "verified" if story_has_link
            else ("issue" if story_count > 0 else "pending")
        )
        story_detail = (
            f"Stories checked={story_count}; assigned link "
            f"{'found' if story_has_link else 'not found'}"
        )
    else:
        story_count = 0
        story_status = None
        story_link_status = None
        story_detail = stories.get("error") or "Story inspection unavailable"

    detail = (
        f"Bio {'OK' if bio_ok else 'MISSING'}; "
        f"detected bio links={len(distinct_links)}; {story_detail}"
    )

    save_auto_verification_result(
        row["id"],
        day,
        bio_status=bio_status,
        only_status=only_status,
        story_status=story_status,
        story_link_status=story_link_status,
        auto_status="checked",
        detail=detail,
        bio_links=bio_links,
        story_count=story_count,
        checker_mode="free_web+session" if IG_SESSIONID else "free_web",
    )
    return {
        "ok": True,
        "day": day,
        "bio_status": bio_status,
        "only_status": only_status,
        "story_status": story_status,
        "story_link_status": story_link_status,
        "story_count": story_count,
        "detail": detail,
    }


def run_free_hourly_verification_once():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM campaign_links
                WHERE is_active=TRUE
                  AND LOWER(COALESCE(source_type,'instagram'))='instagram'
                ORDER BY id
                """
            )
            rows = cur.fetchall()

    checked = 0
    failed = 0
    for row in rows:
        try:
            result = run_free_instagram_check_for_link(row)
            checked += 1
            if not result.get("ok"):
                failed += 1
        except Exception as exc:
            failed += 1
            logger.exception(
                "Free hourly verification failed for %s",
                row.get("instagram_username"),
            )
        # Gentle spacing avoids hammering Instagram.
        time.sleep(1.25)

    logger.info(
        "Free Instagram verification cycle complete: checked=%s failed=%s",
        checked, failed,
    )
    return {"checked": checked, "failed": failed}


def free_hourly_verification_worker():
    # Give the bot/web server time to start before first cycle.
    time.sleep(20)
    while True:
        try:
            run_free_hourly_verification_once()
        except Exception:
            logger.exception("Hourly free Instagram verification cycle failed")
        time.sleep(max(3600, IG_CHECK_INTERVAL_SECONDS))


def verification_summary_rows(day=1):
    day = max(1, min(int(day), 7))
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    cl.id AS campaign_link_id,
                    cl.instagram_username,
                    cl.slug,
                    cl.agent_code,
                    COALESCE(cl.source_type,'instagram') AS source_type,
                    cl.source_url,
                    COALESCE(v.bio_status,'pending') AS bio_status,
                    COALESCE(v.only_our_link_status,'pending') AS only_our_link_status,
                    COALESCE(v.story_status,'pending') AS story_status,
                    COALESCE(v.story_link_status,'pending') AS story_link_status,
                    v.proof_file_id,
                    v.proof_type,
                    v.proof_caption,
                    v.checked_at,
                    v.auto_checked_at,
                    COALESCE(v.auto_check_status,'pending') AS auto_check_status,
                    v.auto_check_detail,
                    v.detected_bio_links,
                    COALESCE(v.detected_story_count,0) AS detected_story_count,
                    v.checker_mode,
                    cl.created_at
                FROM campaign_links cl
                LEFT JOIN campaign_verification v
                    ON v.campaign_link_id=cl.id
                   AND v.campaign_day=%s
                WHERE cl.is_active=TRUE
                ORDER BY cl.instagram_username
                """,
                (day,),
            )
            return cur.fetchall()


def verification_creator_keyboard(row, day=1):
    username = str(row["instagram_username"]).strip().lstrip("@")
    source = (row.get("source_type") or "instagram").lower()
    source_url = row.get("source_url")
    if not source_url:
        source_url = (
            f"https://www.instagram.com/{username}/"
            if source == "instagram"
            else f"https://t.me/{username}"
            if source == "telegram"
            else f"{PUBLIC_BASE_URL}/{row['slug']}"
        )

    code = row["agent_code"]
    cid = row["campaign_link_id"]
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🌐 Open Source Page", url=source_url),
            InlineKeyboardButton("🔗 Open Batraxy Link", url=f"{PUBLIC_BASE_URL}/{row['slug']}"),
        ],
        [
            InlineKeyboardButton("✅ Bio OK", callback_data=f"verify_set:{cid}:{day}:bio_status:verified"),
            InlineKeyboardButton("❌ Bio Missing", callback_data=f"verify_set:{cid}:{day}:bio_status:missing"),
        ],
        [
            InlineKeyboardButton("✅ Only Our Link", callback_data=f"verify_set:{cid}:{day}:only_our_link_status:verified"),
            InlineKeyboardButton("⚠️ Extra Link", callback_data=f"verify_set:{cid}:{day}:only_our_link_status:issue"),
        ],
        [
            InlineKeyboardButton("✅ Story Live", callback_data=f"verify_set:{cid}:{day}:story_status:verified"),
            InlineKeyboardButton("❌ Story Missing", callback_data=f"verify_set:{cid}:{day}:story_status:missing"),
        ],
        [
            InlineKeyboardButton("✅ Story Link OK", callback_data=f"verify_set:{cid}:{day}:story_link_status:verified"),
            InlineKeyboardButton("⚠️ Story Link Issue", callback_data=f"verify_set:{cid}:{day}:story_link_status:issue"),
        ],
        [
            InlineKeyboardButton("📸 Upload Proof", callback_data=f"verify_upload:{cid}:{day}"),
            InlineKeyboardButton("👁 View Proof", callback_data=f"verify_view_proof:{cid}:{day}"),
        ],
        [
            InlineKeyboardButton("⬅️ Verification List", callback_data=f"verify_day:{day}"),
            InlineKeyboardButton("🏠 Campaign", callback_data="campaign_home"),
        ],
    ])


def verification_list_keyboard(rows, day=1, page=0, per_page=8):
    total_pages = max(1, (len(rows) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    subset = rows[page * per_page:(page + 1) * per_page]
    buttons = []

    for r in subset:
        bio = VERIFY_STATUS_LABEL.get(r["bio_status"], "⏳")
        only = VERIFY_STATUS_LABEL.get(r["only_our_link_status"], "⏳")
        story = VERIFY_STATUS_LABEL.get(r["story_status"], "⏳")
        name = str(r["instagram_username"])[:22]
        buttons.append([
            InlineKeyboardButton(
                f"{bio}{only}{story} @{name}",
                callback_data=f"verify_creator:{r['campaign_link_id']}:{day}",
            )
        ])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"verify_page:{day}:{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="campaign_noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"verify_page:{day}:{page+1}"))
    buttons.append(nav)

    day_buttons = []
    for d in range(1, 8):
        day_buttons.append(InlineKeyboardButton(str(d), callback_data=f"verify_day:{d}"))
        if len(day_buttons) == 4:
            buttons.append(day_buttons)
            day_buttons = []
    if day_buttons:
        buttons.append(day_buttons)

    buttons.append([
        InlineKeyboardButton("📊 Auto Report", callback_data=f"verify_auto_report:{day}"),
        InlineKeyboardButton("💻 Checker Status", callback_data="verify_local_status"),
    ])
    buttons.append([
        InlineKeyboardButton("📄 Compliance PDF", callback_data=f"verify_pdf:{day}"),
        InlineKeyboardButton("⬅️ Campaign Tracker", callback_data="campaign_home"),
    ])
    return InlineKeyboardMarkup(buttons)


def verification_creator_text(row, day):
    bio = VERIFY_STATUS_LABEL.get(row["bio_status"], "⏳")
    only = VERIFY_STATUS_LABEL.get(row["only_our_link_status"], "⏳")
    story = VERIFY_STATUS_LABEL.get(row["story_status"], "⏳")
    story_link = VERIFY_STATUS_LABEL.get(row["story_link_status"], "⏳")
    checked = row.get("checked_at")
    checked_text = checked.strftime("%d %b %Y %H:%M UTC") if checked else "Not checked"

    auto_checked = row.get("auto_checked_at")
    auto_checked_text = (
        auto_checked.strftime("%d %b %Y %H:%M UTC")
        if auto_checked else "Not run yet"
    )
    auto_status = str(row.get("auto_check_status") or "pending")
    auto_icon = {
        "checked": "🤖✅",
        "unknown": "🤖⚠️",
        "pending": "🤖⏳",
    }.get(auto_status, "🤖⏳")
    mode = row.get("checker_mode") or "—"
    detail = html.escape(str(row.get("auto_check_detail") or "No automatic result yet"))

    return (
        f"✅ <b>Campaign Verification</b> — Day {day}/7\n\n"
        f"Creator: <b>@{html.escape(str(row['instagram_username']))}</b>\n"
        f"Source: <b>{CAMPAIGN_SOURCE_LABELS.get(row.get('source_type') or 'instagram', 'Instagram')}</b>\n"
        f"Assigned link: <code>{PUBLIC_BASE_URL}/{html.escape(str(row['slug']))}</code>\n\n"
        f"Bio Link: <b>{bio} {html.escape(str(row['bio_status']).title())}</b>\n"
        f"Only Our Link: <b>{only} {html.escape(str(row['only_our_link_status']).title())}</b>\n"
        f"Story Live: <b>{story} {html.escape(str(row['story_status']).title())}</b>\n"
        f"Story Link: <b>{story_link} {html.escape(str(row['story_link_status']).title())}</b>\n"
        f"Proof: <b>{'✅ Available' if row.get('proof_file_id') else '⏳ Not uploaded'}</b>\n\n"
        f"{auto_icon} <b>Automatic checker</b>\n"
        f"Mode: <code>{html.escape(str(mode))}</code>\n"
        f"Last auto check: <b>{auto_checked_text}</b>\n"
        f"Result: {detail}\n\n"
        f"Last status update: <b>{checked_text}</b>"
    )




def verification_last_checked(row):
    values = [x for x in (row.get("auto_checked_at"), row.get("checked_at")) if x]
    return max(values) if values else None


def verification_final_result(row):
    statuses = [
        row.get("bio_status") or "pending",
        row.get("only_our_link_status") or "pending",
        row.get("story_status") or "pending",
        row.get("story_link_status") or "pending",
    ]

    # Any confirmed problem takes priority over an unknown/pending item.
    if any(x in {"missing", "issue"} for x in statuses):
        return "ACTION REQUIRED"

    if all(x == "verified" for x in statuses):
        return "PASS"

    return "MANUAL REVIEW"


def verification_source_url(row):
    username = str(row.get("instagram_username") or "").strip().lstrip("@")
    source = (row.get("source_type") or "instagram").lower()
    if row.get("source_url"):
        return str(row["source_url"])
    if source == "instagram":
        return f"https://www.instagram.com/{username}/"
    if source == "telegram":
        return f"https://t.me/{username}"
    return f"{PUBLIC_BASE_URL}/{row['slug']}"


def promoter_report_keyboard(rows, day=1):
    """
    Promoter-friendly direct links:
    left button opens creator/source page; right button opens assigned Batraxy URL.
    """
    buttons = []
    for r in rows:
        username = str(r.get("instagram_username") or "").strip().lstrip("@")
        label = username[:18] + ("…" if len(username) > 18 else "")
        buttons.append([
            InlineKeyboardButton(f"📸 @{label}", url=verification_source_url(r)),
            InlineKeyboardButton("🔗 Assigned Batraxy", url=f"{PUBLIC_BASE_URL}/{r['slug']}"),
        ])

    buttons.append([
        InlineKeyboardButton("🔄 Refresh Report", callback_data=f"verify_auto_report:{day}"),
        InlineKeyboardButton("📄 Compliance PDF", callback_data=f"verify_pdf:{day}"),
    ])
    buttons.append([
        InlineKeyboardButton("✅ Verification Center", callback_data="verify_home"),
        InlineKeyboardButton("⬅️ Campaign Tracker", callback_data="campaign_home"),
    ])
    return InlineKeyboardMarkup(buttons)


def automatic_verification_report_text(rows, day):
    total = len(rows)

    def icon(value):
        return VERIFY_STATUS_LABEL.get(value, "⏳")

    final_counts = {"PASS": 0, "ACTION REQUIRED": 0, "MANUAL REVIEW": 0}
    for r in rows:
        final_counts[verification_final_result(r)] += 1

    # Overall report time is the newest result in this report.
    checked_values = [verification_last_checked(r) for r in rows if verification_last_checked(r)]
    newest = max(checked_values) if checked_values else None
    newest_text = newest.strftime("%d %b %Y %H:%M UTC") if newest else "No completed check yet"

    lines = [
        f"📊 <b>BETROXY Promoter Compliance Report — Day {day}/7</b>",
        "",
        f"<b>Creators:</b> {total}   "
        f"<b>PASS:</b> {final_counts['PASS']}   "
        f"<b>ACTION:</b> {final_counts['ACTION REQUIRED']}   "
        f"<b>MANUAL:</b> {final_counts['MANUAL REVIEW']}",
        f"<b>Latest check:</b> {newest_text}",
        "",
        "<b>Final result</b>",
        "✅ PASS = all required checks confirmed",
        "🚨 ACTION REQUIRED = missing/incorrect link or another confirmed issue",
        "🕵️ MANUAL REVIEW = automatic checker could not confirm everything",
        "",
        "<b>Columns</b>",
        "Bio = assigned Batraxy link is in bio",
        "Only = no additional external/promotional bio link",
        "Story = active Story confirmed",
        "Link = assigned Batraxy link confirmed in Story",
        "",
        "<pre>",
        f"{'Creator':<18} {'B':<2} {'O':<2} {'S':<2} {'L':<2} {'Final':<13} {'Checked':<11}",
        "-" * 61,
    ]

    final_short = {
        "PASS": "PASS",
        "ACTION REQUIRED": "ACTION",
        "MANUAL REVIEW": "MANUAL",
    }

    for r in rows:
        name = str(r.get("instagram_username") or "")
        name = name[:17] + ("…" if len(name) > 17 else "")
        checked = verification_last_checked(r)
        checked_text = checked.strftime("%d %b %H:%M") if checked else "Not checked"
        result = verification_final_result(r)
        lines.append(
            f"@{name:<17} "
            f"{icon(r.get('bio_status')):<2} "
            f"{icon(r.get('only_our_link_status')):<2} "
            f"{icon(r.get('story_status')):<2} "
            f"{icon(r.get('story_link_status')):<2} "
            f"{final_short[result]:<13} "
            f"{checked_text:<11}"
        )

    lines += [
        "</pre>",
        "",
        "<b>Status symbols</b>",
        "✅ Confirmed   ⚠️ Issue   ❌ Missing   ⏳ Manual check needed",
        "",
        "<b>Promoter action</b>",
        "• 🚨 ACTION: fix the flagged item and send updated proof.",
        "• 🕵️ MANUAL: send current Story/screenshot proof so it can be verified.",
        "• Use the buttons below to open each creator page and their assigned Batraxy link.",
    ]
    return "\n".join(lines)


def build_verification_pdf(rows, day):
    output = io.BytesIO()
    doc = SimpleDocTemplate(
        output,
        pagesize=landscape(A4),
        rightMargin=22, leftMargin=22, topMargin=24, bottomMargin=24,
        title=f"BETROXY Promoter Compliance Report Day {day}",
        author="BETROXY",
    )
    styles = getSampleStyleSheet()
    small = ParagraphStyle(
        "small",
        parent=styles["BodyText"],
        fontSize=7,
        leading=9,
    )
    small_center = ParagraphStyle(
        "smallcenter",
        parent=small,
        alignment=TA_CENTER,
    )
    summary_style = ParagraphStyle(
        "summary",
        parent=styles["BodyText"],
        fontSize=9,
        leading=12,
        spaceAfter=5,
    )

    final_counts = {"PASS": 0, "ACTION REQUIRED": 0, "MANUAL REVIEW": 0}
    for r in rows:
        final_counts[verification_final_result(r)] += 1

    checked_values = [verification_last_checked(r) for r in rows if verification_last_checked(r)]
    newest = max(checked_values) if checked_values else None
    newest_text = newest.strftime("%d %b %Y %H:%M UTC") if newest else "No completed check yet"

    story = [
        Paragraph(f"BETROXY - Promoter Compliance Report - Day {day}/7", styles["Title"]),
        Spacer(1, 6),
        Paragraph(
            f"<b>Creators:</b> {len(rows)} &nbsp;&nbsp; "
            f"<b>PASS:</b> {final_counts['PASS']} &nbsp;&nbsp; "
            f"<b>ACTION REQUIRED:</b> {final_counts['ACTION REQUIRED']} &nbsp;&nbsp; "
            f"<b>MANUAL REVIEW:</b> {final_counts['MANUAL REVIEW']}<br/>"
            f"<b>Latest check:</b> {html.escape(newest_text)}",
            summary_style,
        ),
        Paragraph(
            "<b>Definitions:</b> Bio = assigned Batraxy link present in bio; "
            "Only = no additional external/promotional bio link; "
            "Story = active Story confirmed; Link = assigned Batraxy link confirmed in Story.<br/>"
            "<b>Final:</b> PASS = all checks confirmed; ACTION REQUIRED = confirmed issue/missing item; "
            "MANUAL REVIEW = automatic checker could not confirm everything.",
            summary_style,
        ),
        Spacer(1, 6),
    ]

    data = [[
        "Creator / Instagram",
        "Assigned Batraxy Link",
        "Bio",
        "Only",
        "Story",
        "Story Link",
        "Final Result",
        "Last Checked",
    ]]

    for r in rows:
        checked = verification_last_checked(r)
        checked_text = checked.strftime("%d-%m-%Y %H:%M") if checked else "Not checked"
        source_url = verification_source_url(r)
        assigned = f"{PUBLIC_BASE_URL}/{r['slug']}"
        username = str(r.get("instagram_username") or "").strip().lstrip("@")
        result = verification_final_result(r)

        data.append([
            Paragraph(
                f'<link href="{html.escape(source_url, quote=True)}">@{html.escape(username)}</link>',
                small,
            ),
            Paragraph(
                f'<link href="{html.escape(assigned, quote=True)}">{html.escape(assigned)}</link>',
                small,
            ),
            str(r.get("bio_status") or "pending").replace("_", " ").title(),
            str(r.get("only_our_link_status") or "pending").replace("_", " ").title(),
            str(r.get("story_status") or "pending").replace("_", " ").title(),
            str(r.get("story_link_status") or "pending").replace("_", " ").title(),
            result,
            checked_text,
        ])

    table = Table(
        data,
        repeatRows=1,
        colWidths=[105, 190, 50, 55, 55, 62, 90, 92],
    )
    table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#081C15")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,-1), 7),
        ("GRID", (0,0), (-1,-1), .35, colors.HexColor("#B9CCC2")),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("ALIGN", (2,1), (-2,-1), "CENTER"),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [
            colors.HexColor("#F7FBF9"),
            colors.HexColor("#EDF6F1"),
        ]),
        ("TOPPADDING", (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
    ]))
    story.append(table)
    story.append(Spacer(1, 10))
    story.append(Paragraph(
        "<b>Promoter action:</b> Fix every ACTION REQUIRED item. "
        "For MANUAL REVIEW, provide a current Story screenshot/proof. "
        "Creator names and assigned Batraxy URLs in this PDF are clickable.",
        summary_style,
    ))
    doc.build(story)
    output.seek(0)
    return output


def campaign_report_creator_keyboard(rows):
    """
    Keep the report itself as a fixed-width table, while making every creator
    directly clickable via Telegram URL buttons attached to the same report.
    Clicking @creator opens the creator's Instagram page.
    """
    buttons = []
    current = []

    for r in rows:
        username = str(r["name"]).strip().lstrip("@")
        source = (r.get("source_type") or "instagram").lower()
        icon = {
            "instagram": "📸",
            "telegram": "✈️",
            "meta_ads": "Ⓜ️",
            "google_ads": "🔎",
        }.get(source, "🔗")
        target_url = r.get("source_url")
        if not target_url:
            target_url = (
                f"https://www.instagram.com/{username}/"
                if source == "instagram"
                else f"https://t.me/{username}"
                if source == "telegram"
                else f"{PUBLIC_BASE_URL}/{r['slug']}"
            )
        current.append(
            InlineKeyboardButton(
                f"{icon} @{username}",
                url=target_url,
            )
        )
        if len(current) == 2:
            buttons.append(current)
            current = []

    if current:
        buttons.append(current)

    buttons.append([
        InlineKeyboardButton("📊 Full Report", callback_data="campaign_report"),
        InlineKeyboardButton("🏆 Top Pages", callback_data="campaign_top"),
    ])
    buttons.append([
        InlineKeyboardButton("📅 Today", callback_data="campaign_today"),
        InlineKeyboardButton("⬅️ Campaign Tracker", callback_data="campaign_home"),
    ])
    return InlineKeyboardMarkup(buttons)


def campaign_creator_menu(code):
    row = campaign_link_by_code(code)
    landing_url = f"{PUBLIC_BASE_URL}/{row['slug']}" if row else PUBLIC_BASE_URL
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🌐 Open Landing Page", url=landing_url)],
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
                InlineKeyboardButton("🏷 Source", callback_data=f"campaign_edit_source:{code}"),
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



CAMPAIGN_SOURCE_LABELS = {
    "instagram": "Instagram",
    "telegram": "Telegram",
    "meta_ads": "Meta Ads",
    "google_ads": "Google Ads",
}


def parse_campaign_input(raw_value, default_source="instagram"):
    """
    Accepts:
      - Instagram profile URL
      - Telegram profile/channel URL
      - Batraxy landing URL
      - @username
      - plain username

    Returns a normalized dict:
      source_type, handle, source_url, slug_lookup
    """
    raw = (raw_value or "").strip()
    if not raw:
        raise ValueError("Please send a valid Instagram/Telegram link or username")

    # Remove common Telegram/WhatsApp copy noise around a URL.
    match = re.search(r"https?://[^\s]+", raw, flags=re.I)
    value = match.group(0) if match else raw
    value = value.strip().strip("<>\"'")

    # Existing Batraxy creator landing URL.
    m = re.search(r"(?:https?://)?(?:www\.)?batraxy\.com/([^/?#\s]+)", value, flags=re.I)
    if m:
        return {
            "source_type": None,
            "handle": None,
            "source_url": None,
            "slug_lookup": m.group(1).strip(),
        }

    # Instagram profile URL.
    m = re.search(
        r"(?:https?://)?(?:www\.)?instagram\.com/([^/?#\s]+)/?",
        value,
        flags=re.I,
    )
    if m:
        handle = m.group(1).strip().lstrip("@")
        # Avoid treating content route names as creator handles.
        if handle.lower() in {"p", "reel", "reels", "stories", "explore", "accounts"}:
            raise ValueError("Please send the Instagram PROFILE link, not a post/reel link")
        return {
            "source_type": "instagram",
            "handle": handle,
            "source_url": f"https://www.instagram.com/{handle}/",
            "slug_lookup": None,
        }

    # Telegram profile/channel URL.
    m = re.search(
        r"(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/([^/?#\s]+)/?",
        value,
        flags=re.I,
    )
    if m:
        handle = m.group(1).strip().lstrip("@")
        if handle.lower() in {"joinchat", "share", "addstickers"}:
            raise ValueError("Please send the Telegram channel/profile link")
        return {
            "source_type": "telegram",
            "handle": handle,
            "source_url": f"https://t.me/{handle}",
            "slug_lookup": None,
        }

    # Plain @username / username. Keep current default as Instagram.
    handle = value.strip().lstrip("@").split("?")[0].split("#")[0].strip("/")
    if not re.fullmatch(r"[A-Za-z0-9._-]{2,100}", handle):
        raise ValueError("Could not identify the page/channel from that link")

    source_type = normalize_campaign_source(default_source)
    source_url = (
        f"https://www.instagram.com/{handle}/"
        if source_type == "instagram"
        else f"https://t.me/{handle}"
        if source_type == "telegram"
        else None
    )
    return {
        "source_type": source_type,
        "handle": handle,
        "source_url": source_url,
        "slug_lookup": None,
    }


def find_campaign_link_from_input(raw_value):
    parsed = parse_campaign_input(raw_value)

    if parsed["slug_lookup"]:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM campaign_links WHERE LOWER(slug)=LOWER(%s) LIMIT 1",
                    (parsed["slug_lookup"],),
                )
                return cur.fetchone(), parsed

    with get_db() as conn:
        with conn.cursor() as cur:
            # Prefer exact source + handle.
            cur.execute(
                """
                SELECT * FROM campaign_links
                WHERE LOWER(instagram_username)=LOWER(%s)
                  AND LOWER(COALESCE(source_type,'instagram'))=LOWER(%s)
                ORDER BY id DESC
                LIMIT 1
                """,
                (parsed["handle"], parsed["source_type"]),
            )
            row = cur.fetchone()
            if row:
                return row, parsed

            # Fallback by handle only for older rows.
            cur.execute(
                """
                SELECT * FROM campaign_links
                WHERE LOWER(instagram_username)=LOWER(%s)
                ORDER BY id DESC
                LIMIT 1
                """,
                (parsed["handle"],),
            )
            return cur.fetchone(), parsed



def all_campaign_links(include_inactive=True):
    with get_db() as conn:
        with conn.cursor() as cur:
            if include_inactive:
                cur.execute(
                    """
                    SELECT * FROM campaign_links
                    ORDER BY created_at, id
                    """
                )
            else:
                cur.execute(
                    """
                    SELECT * FROM campaign_links
                    WHERE is_active=TRUE
                    ORDER BY created_at, id
                    """
                )
            return cur.fetchall()


def set_campaign_links_active_by_ids(ids, active):
    ids = [int(x) for x in ids]
    if not ids:
        return 0
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE campaign_links
                SET is_active=%s
                WHERE id = ANY(%s)
                """,
                (bool(active), ids),
            )
            count = cur.rowcount
        conn.commit()
    return count


def delete_campaign_links_by_ids(ids):
    ids = [int(x) for x in ids]
    if not ids:
        return 0
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM campaign_links
                WHERE id = ANY(%s)
                """,
                (ids,),
            )
            count = cur.rowcount
        conn.commit()
    return count


def resolve_or_create_final_promoted_links(raw_text):
    """
    Parse a pasted final list of Instagram / Telegram / Batraxy links.
    Existing campaign links are matched; Instagram/Telegram sources that do not
    exist are created automatically. Returns kept rows + unresolved items.
    """
    raw_lines = []
    seen = set()
    for line in (raw_text or "").splitlines():
        item = line.strip()
        if not item:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        raw_lines.append(item)

    kept = []
    unresolved = []
    seen_ids = set()

    for item in raw_lines[:200]:
        try:
            row, parsed = find_campaign_link_from_input(item)
            if not row and not parsed.get("slug_lookup"):
                row, _created = create_campaign_creator(item)
            if row:
                if int(row["id"]) not in seen_ids:
                    kept.append(row)
                    seen_ids.add(int(row["id"]))
            else:
                unresolved.append(item)
        except Exception:
            unresolved.append(item)

    # Re-fetch latest rows in case some were created.
    refreshed = []
    for row in kept:
        latest = campaign_link_by_code(row["agent_code"])
        refreshed.append(latest or row)
    return refreshed, unresolved



def request_local_verifier_run():
    token = secrets.token_hex(16)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE verifier_control
                SET run_token=%s,
                    requested_at=NOW(),
                    claimed_at=NULL,
                    completed_at=NULL,
                    status='requested',
                    result_summary=NULL
                WHERE id=1
                RETURNING *
                """,
                (token,),
            )
            row = cur.fetchone()
        conn.commit()
    return row


def get_local_verifier_control():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM verifier_control WHERE id=1")
            return cur.fetchone()


def set_campaign_link_active(code, active):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE campaign_links
                SET is_active=%s
                WHERE LOWER(agent_code)=LOWER(%s)
                RETURNING *
                """,
                (bool(active), code),
            )
            row = cur.fetchone()
            conn.commit()
            return row


def cleanup_malformed_campaign_sources():
    """
    Repairs older rows where a full Instagram/Telegram URL was accidentally
    saved in instagram_username. It keeps the existing landing slug/code so
    already-shared Batraxy links do not break.
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, instagram_username, source_type, source_url
                FROM campaign_links
                """
            )
            rows = cur.fetchall()

            for row in rows:
                raw = (row["instagram_username"] or "").strip()
                try:
                    parsed = parse_campaign_input(raw)
                except Exception:
                    parsed = None

                if parsed and parsed.get("handle") and (
                    "instagram.com/" in raw.lower()
                    or "t.me/" in raw.lower()
                    or "telegram.me/" in raw.lower()
                ):
                    cur.execute(
                        """
                        UPDATE campaign_links
                        SET instagram_username=%s,
                            source_type=%s,
                            source_url=%s
                        WHERE id=%s
                        """,
                        (
                            parsed["handle"],
                            parsed["source_type"],
                            parsed["source_url"],
                            row["id"],
                        ),
                    )
                elif not row.get("source_url"):
                    source = (row.get("source_type") or "instagram").lower()
                    handle = raw.lstrip("@")
                    if source == "instagram":
                        source_url = f"https://www.instagram.com/{handle}/"
                    elif source == "telegram":
                        source_url = f"https://t.me/{handle}"
                    else:
                        source_url = None
                    if source_url:
                        cur.execute(
                            "UPDATE campaign_links SET source_url=%s WHERE id=%s",
                            (source_url, row["id"]),
                        )
        conn.commit()




def normalize_campaign_source(value):
    value = (value or "").strip().lower().replace(" ", "_")
    aliases = {
        "insta": "instagram",
        "instagram": "instagram",
        "telegram": "telegram",
        "tg": "telegram",
        "meta": "meta_ads",
        "meta_ads": "meta_ads",
        "facebook": "meta_ads",
        "facebook_ads": "meta_ads",
        "google": "google_ads",
        "google_ads": "google_ads",
    }
    source = aliases.get(value)
    if source not in CAMPAIGN_SOURCE_LABELS:
        raise ValueError("Source must be Instagram, Telegram, Meta Ads or Google Ads")
    return source


def update_campaign_source(code, source_type):
    source_type = normalize_campaign_source(source_type)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE campaign_links
                SET source_type=%s
                WHERE LOWER(agent_code)=LOWER(%s)
                RETURNING *
                """,
                (source_type, code),
            )
            row = cur.fetchone()
            conn.commit()
            return row


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


def create_campaign_creator(raw_input, commission_rate=0, source_type=None):
    parsed = parse_campaign_input(
        raw_input,
        default_source=source_type or "instagram",
    )
    if parsed["slug_lookup"]:
        raise ValueError("For creating a new link, send an Instagram or Telegram profile/channel link")

    username = parsed["handle"]
    source_type = parsed["source_type"]
    source_url = parsed["source_url"]

    if not username:
        raise ValueError("Could not identify the creator/channel")

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM campaign_links
                WHERE LOWER(instagram_username)=LOWER(%s)
                  AND LOWER(COALESCE(source_type,'instagram'))=LOWER(%s)
                LIMIT 1
                """,
                (username, source_type),
            )
            existing = cur.fetchone()
            if existing:
                # Repair missing URL on an old row.
                if not existing.get("source_url") and source_url:
                    cur.execute(
                        "UPDATE campaign_links SET source_url=%s WHERE id=%s RETURNING *",
                        (source_url, existing["id"]),
                    )
                    existing = cur.fetchone()
                    conn.commit()
                return existing, False

    slug, agent_code = ensure_unique_slug_and_code(username)
    create_agent(username, agent_code, commission_rate)

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO campaign_links
                    (instagram_username, slug, agent_code, is_active, source_type, source_url)
                VALUES (%s, %s, %s, TRUE, %s, %s)
                RETURNING *
                """,
                (username, slug, agent_code, source_type, source_url),
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
            where = "WHERE 1=1"
            params = []
            if code:
                where = "WHERE LOWER(cl.agent_code)=LOWER(%s)"
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
                    cl.agent_code AS code,
                    cl.instagram_username AS name,
                    cl.slug,
                    COALESCE(cl.source_type, 'instagram') AS source_type,
                    cl.source_url,
                    COALESCE(v.landing_visits, 0) AS landing_visits,
                    COALESCE(v.unique_visitors, 0) AS unique_visitors,
                    COALESCE(o.telegram_clicks, 0) AS telegram_clicks,
                    COALESCE(o.website_clicks, 0) AS website_clicks,
                    COALESCE(s.telegram_starts, 0) AS telegram_starts,
                    COALESCE(c.registrations, 0) AS registrations,
                    COALESCE(c.deposits, 0) AS deposits,
                    COALESCE(c.deposit_amount, 0) AS deposit_amount
                FROM campaign_links cl
                LEFT JOIN agents a ON LOWER(a.code)=LOWER(cl.agent_code)
                LEFT JOIN visits v ON LOWER(v.agent_code)=LOWER(cl.agent_code)
                LEFT JOIN outbound o ON LOWER(o.agent_code)=LOWER(cl.agent_code)
                LEFT JOIN starts s ON LOWER(s.agent_code)=LOWER(cl.agent_code)
                LEFT JOIN conv c ON LOWER(c.agent_code)=LOWER(cl.agent_code)
                {where}
                AND cl.is_active=TRUE
                ORDER BY landing_visits DESC, telegram_clicks DESC, website_clicks DESC, cl.instagram_username
                """,
                params,
            )
            return cur.fetchall()



def build_campaign_report_pdf(rows):
    """
    Build a professional landscape PDF report in memory and return BytesIO.
    """
    output = io.BytesIO()
    doc = SimpleDocTemplate(
        output,
        pagesize=landscape(A4),
        rightMargin=26,
        leftMargin=26,
        topMargin=28,
        bottomMargin=28,
        title="BETROXY Instagram Campaign Report",
        author="BETROXY",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "BetroxyTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=24,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#081C15"),
        spaceAfter=6,
    )
    sub_style = ParagraphStyle(
        "BetroxySub",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#5F746A"),
        spaceAfter=14,
    )
    foot_style = ParagraphStyle(
        "BetroxyFoot",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#6A7E74"),
        spaceBefore=10,
    )

    story = [
        Paragraph("BETROXY - Instagram Campaign Report", title_style),
        Paragraph(
            "Creator performance summary generated from the live campaign tracker",
            sub_style,
        ),
    ]

    data = [[
        "No.",
        "Creator",
        "Source",
        "Landing Visits",
        "Unique",
        "Bot Clicks",
        "Web Clicks",
        "Bot Starts",
        "Registrations",
        "Deposits",
        "Deposit Amount",
    ]]

    total_vis = total_unique = total_bot = total_web = 0
    total_starts = total_reg = total_dep = 0
    total_amount = 0.0

    for i, r in enumerate(rows, 1):
        vis = int(r["landing_visits"] or 0)
        unique = int(r["unique_visitors"] or 0)
        bot_clicks = int(r["telegram_clicks"] or 0)
        web_clicks = int(r["website_clicks"] or 0)
        starts = int(r["telegram_starts"] or 0)
        regs = int(r["registrations"] or 0)
        deps = int(r["deposits"] or 0)
        amount = float(r["deposit_amount"] or 0)

        total_vis += vis
        total_unique += unique
        total_bot += bot_clicks
        total_web += web_clicks
        total_starts += starts
        total_reg += regs
        total_dep += deps
        total_amount += amount

        data.append([
            str(i),
            "@" + str(r["name"]),
            CAMPAIGN_SOURCE_LABELS.get(r.get("source_type") or "instagram", "Instagram"),
            str(vis),
            str(unique),
            str(bot_clicks),
            str(web_clicks),
            str(starts),
            str(regs),
            str(deps),
            f"{amount:,.2f}",
        ])

    data.append([
        "",
        "TOTAL",
        "",
        str(total_vis),
        str(total_unique),
        str(total_bot),
        str(total_web),
        str(total_starts),
        str(total_reg),
        str(total_dep),
        f"{total_amount:,.2f}",
    ])

    table = Table(
        data,
        repeatRows=1,
        colWidths=[26, 105, 58, 58, 45, 50, 50, 50, 58, 48, 70],
    )
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#081C15")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 7.5),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("ALIGN", (1, 1), (1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTNAME", (0, 1), (-1, -2), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, -1), 7.5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [
            colors.HexColor("#F7FBF9"),
            colors.HexColor("#EDF6F1"),
        ]),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#D8F3DC")),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("TEXTCOLOR", (0, -1), (-1, -1), colors.HexColor("#081C15")),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#B9CCC2")),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))

    story.append(table)
    story.append(Spacer(1, 8))
    story.append(
        Paragraph(
            f"Total creators: {len(rows)} &nbsp;&nbsp;|&nbsp;&nbsp; "
            "Vis = landing visits &nbsp;|&nbsp; Bot = BetroxyBot clicks &nbsp;|&nbsp; "
            "Web = website clicks",
            foot_style,
        )
    )

    doc.build(story)
    output.seek(0)
    return output


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
    theme_name = "BETROXY Polished Built-in v4"

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, is_active FROM landing_themes WHERE name=%s ORDER BY id DESC LIMIT 1",
                (theme_name,),
            )
            existing = cur.fetchone()

            if existing:
                theme_id = existing["id"]
            else:
                cur.execute(
                    """
                    INSERT INTO landing_themes (name, index_html, created_by)
                    VALUES (%s, %s, %s)
                    RETURNING id
                    """,
                    (theme_name, DEFAULT_LANDING_HTML, ADMIN_ID),
                )
                theme_id = cur.fetchone()["id"]

            # Publish v3 for this corrective deployment.
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
    instagram = str(link["instagram_username"]).strip().lstrip("@")
    # Defensive cleanup for any legacy row that still contains a full source URL.
    try:
        parsed_source = parse_campaign_input(instagram)
        if parsed_source.get("handle"):
            instagram = parsed_source["handle"]
    except Exception:
        pass
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
  color:#c9d8d1;font-size:12px;font-weight:700;letter-spacing:.1px;
  max-width:100%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis
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



def _verifier_authorized():
    if not TRACKER_API_SECRET:
        return False
    supplied = request.headers.get("X-Tracker-Secret", "")
    return bool(supplied) and secrets.compare_digest(supplied, TRACKER_API_SECRET)


@tracker_api.get("/api/verifier/targets")
def verifier_targets():
    if not _verifier_authorized():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, instagram_username, slug, agent_code,
                       COALESCE(source_type,'instagram') AS source_type,
                       source_url, created_at
                FROM campaign_links
                WHERE is_active=TRUE
                ORDER BY id
                """
            )
            rows = cur.fetchall()

    targets = []
    for row in rows:
        source = (row.get("source_type") or "instagram").lower()
        username = str(row["instagram_username"]).strip().lstrip("@")
        targets.append({
            "campaign_link_id": int(row["id"]),
            "username": username,
            "slug": row["slug"],
            "agent_code": row["agent_code"],
            "source_type": source,
            "source_url": row.get("source_url") or (
                f"https://www.instagram.com/{username}/"
                if source == "instagram"
                else f"https://t.me/{username}"
                if source == "telegram"
                else None
            ),
            "assigned_url": f"{PUBLIC_BASE_URL}/{row['slug']}",
            "campaign_day": current_campaign_day(row),
        })

    return jsonify({"ok": True, "targets": targets})


@tracker_api.post("/api/verifier/result")
def verifier_result():
    if not _verifier_authorized():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    payload = request.get_json(silent=True) or {}
    try:
        cid = int(payload.get("campaign_link_id"))
        day = max(1, min(7, int(payload.get("campaign_day") or 1)))
    except Exception:
        return jsonify({"ok": False, "error": "invalid campaign_link_id/day"}), 400

    valid_statuses = {"verified", "missing", "issue", "pending", None}
    bio_status = payload.get("bio_status")
    only_status = payload.get("only_status")
    story_status = payload.get("story_status")
    story_link_status = payload.get("story_link_status")

    if any(x not in valid_statuses for x in [
        bio_status, only_status, story_status, story_link_status
    ]):
        return jsonify({"ok": False, "error": "invalid status"}), 400

    save_auto_verification_result(
        cid,
        day,
        bio_status=bio_status,
        only_status=only_status,
        story_status=story_status,
        story_link_status=story_link_status,
        auto_status=payload.get("auto_status") or "checked",
        detail=str(payload.get("detail") or "")[:4000],
        bio_links=payload.get("detected_bio_links") or [],
        story_count=int(payload.get("story_count") or 0),
        checker_mode="local_browser",
    )
    return jsonify({"ok": True})



@tracker_api.get("/api/verifier/command")
def verifier_command():
    if not _verifier_authorized():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM verifier_control
                WHERE id=1
                FOR UPDATE
                """
            )
            row = cur.fetchone()

            if not row or row.get("status") != "requested" or not row.get("run_token"):
                conn.commit()
                return jsonify({"ok": True, "command": None})

            token = row["run_token"]
            cur.execute(
                """
                UPDATE verifier_control
                SET status='running', claimed_at=NOW()
                WHERE id=1 AND run_token=%s
                """,
                (token,),
            )
        conn.commit()

    return jsonify({"ok": True, "command": "run", "run_token": token})


@tracker_api.post("/api/verifier/complete")
def verifier_complete():
    if not _verifier_authorized():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    payload = request.get_json(silent=True) or {}
    token = str(payload.get("run_token") or "")
    summary = str(payload.get("summary") or "")[:4000]
    if not token:
        return jsonify({"ok": False, "error": "run_token required"}), 400

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE verifier_control
                SET status='completed',
                    completed_at=NOW(),
                    result_summary=%s
                WHERE id=1 AND run_token=%s
                RETURNING id
                """,
                (summary, token),
            )
            row = cur.fetchone()
        conn.commit()

    if not row:
        return jsonify({"ok": False, "error": "run token not found"}), 404
    return jsonify({"ok": True})


@tracker_api.get("/api/verifier/status")
def verifier_status():
    if not _verifier_authorized():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) FILTER (
                        WHERE auto_checked_at >= NOW() - INTERVAL '90 minutes'
                    ) AS recently_checked,
                    MAX(auto_checked_at) AS last_check
                FROM campaign_verification
                WHERE checker_mode='local_browser'
                """
            )
            row = cur.fetchone()

    return jsonify({
        "ok": True,
        "mode": "local_browser",
        "recently_checked": int(row["recently_checked"] or 0),
        "last_check": row["last_check"].isoformat() if row.get("last_check") else None,
    })


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
        # Main landing-page Telegram button opens the actual BETROXY product bot.
        return redirect("https://t.me/BetroxyBot", code=302)

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
        username = str(item["instagram_username"]).strip().lstrip("@")
        source = (item.get("source_type") or "instagram").lower()
        source_icon = {
            "instagram": "📸",
            "telegram": "✈️",
            "meta_ads": "Ⓜ️",
            "google_ads": "🔎",
        }.get(source, "🔗")

        rows.append([
            InlineKeyboardButton(
                f"{source_icon} @{username}",
                url=(
                    item.get("source_url")
                    or (
                        f"https://www.instagram.com/{username}/"
                        if source == "instagram"
                        else f"https://t.me/{username}"
                        if source == "telegram"
                        else f"{PUBLIC_BASE_URL}/{item['slug']}"
                    )
                ),
            ),
            InlineKeyboardButton(
                "⚙️ Manage",
                callback_data=f"campaign_creator:{item['agent_code']}",
            ),
        ])

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



async def campaign_edit_source_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return ConversationHandler.END

    code_value = q.data.split(":", 1)[1]
    row = campaign_link_by_code(code_value)
    if not row:
        await q.message.reply_text("❌ Creator link not found.")
        return ConversationHandler.END

    context.user_data["campaign_edit_source_code"] = code_value
    current = CAMPAIGN_SOURCE_LABELS.get(row.get("source_type") or "instagram", "Instagram")
    await q.message.reply_text(
        f"🏷 <b>Change Traffic Source</b>\\n\\n"
        f"Current: <b>{current}</b>\\n\\n"
        "Choose the source:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📸 Instagram", callback_data="campaign_source_set:instagram"),
                InlineKeyboardButton("✈️ Telegram", callback_data="campaign_source_set:telegram"),
            ],
            [
                InlineKeyboardButton("Ⓜ️ Meta Ads", callback_data="campaign_source_set:meta_ads"),
                InlineKeyboardButton("🔎 Google Ads", callback_data="campaign_source_set:google_ads"),
            ],
            [InlineKeyboardButton("❌ Cancel", callback_data=f"campaign_creator:{code_value}")],
        ]),
    )
    return CAMPAIGN_EDIT_SOURCE


async def campaign_edit_source_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return ConversationHandler.END

    code_value = context.user_data.get("campaign_edit_source_code")
    source_type = q.data.split(":", 1)[1]
    try:
        row = update_campaign_source(code_value, source_type)
        if not row:
            raise ValueError("Creator link not found")
        label = CAMPAIGN_SOURCE_LABELS.get(row["source_type"], row["source_type"])
        await q.message.reply_text(
            f"✅ Source updated to <b>{label}</b>.",
            parse_mode=ParseMode.HTML,
            reply_markup=campaign_creator_menu(row["agent_code"]),
        )
    except Exception as exc:
        await q.message.reply_text(f"❌ {exc}")
    finally:
        context.user_data.pop("campaign_edit_source_code", None)
    return ConversationHandler.END



async def campaign_disable_by_link_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return ConversationHandler.END

    await q.message.reply_text(
        "⛔ <b>Disable Creator Link</b>\\n\\n"
        "Send the Instagram/Telegram profile link, @username, or Batraxy landing link.\\n\\n"
        "Example:\\n<code>https://www.instagram.com/saketeditt/</code>",
        parse_mode=ParseMode.HTML,
    )
    return CAMPAIGN_DISABLE_BY_LINK


async def campaign_disable_by_link_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update):
        return ConversationHandler.END

    raw = (update.message.text or "").strip()
    try:
        row, parsed = find_campaign_link_from_input(raw)
        if not row:
            raise ValueError("No matching creator link found")

        disabled = set_campaign_link_active(row["agent_code"], False)
        await update.message.reply_text(
            "⛔ <b>Creator Link Disabled</b>\\n\\n"
            f"Page/Channel: <b>@{html.escape(str(disabled['instagram_username']))}</b>\\n"
            f"Slug: <code>{html.escape(str(disabled['slug']))}</code>\\n"
            f"Old landing: <code>{PUBLIC_BASE_URL}/{html.escape(str(disabled['slug']))}</code>\\n\\n"
            "Historical tracking data is preserved.",
            parse_mode=ParseMode.HTML,
            reply_markup=campaign_menu(),
            disable_web_page_preview=True,
        )
    except Exception as exc:
        await update.message.reply_text(
            f"❌ {html.escape(str(exc))}",
            parse_mode=ParseMode.HTML,
            reply_markup=campaign_menu(),
        )
    return ConversationHandler.END


async def campaign_delete_by_link_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return ConversationHandler.END

    await q.message.reply_text(
        "🗑 <b>Delete Creator Link</b>\\n\\n"
        "Send the Instagram/Telegram profile link, @username, or Batraxy landing link.\\n\\n"
        "The bot will identify the correct creator and ask for confirmation.",
        parse_mode=ParseMode.HTML,
    )
    return CAMPAIGN_DELETE_BY_LINK


async def campaign_delete_by_link_resolve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update):
        return ConversationHandler.END

    raw = (update.message.text or "").strip()
    try:
        row, parsed = find_campaign_link_from_input(raw)
        if not row:
            raise ValueError("No matching creator link found")

        source_label = CAMPAIGN_SOURCE_LABELS.get(
            row.get("source_type") or "instagram",
            "Instagram",
        )
        await update.message.reply_text(
            "⚠️ <b>Confirm Delete</b>\\n\\n"
            f"Source: <b>{source_label}</b>\\n"
            f"Page/Channel: <b>@{html.escape(str(row['instagram_username']))}</b>\\n"
            f"Landing: <code>{PUBLIC_BASE_URL}/{html.escape(str(row['slug']))}</code>\\n\\n"
            "Delete this campaign link?",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🗑 YES, DELETE",
                        callback_data=f"campaign_delete_yes:{row['agent_code']}",
                    )
                ],
                [InlineKeyboardButton("❌ Cancel", callback_data="campaign_home")],
            ]),
            disable_web_page_preview=True,
        )
    except Exception as exc:
        await update.message.reply_text(
            f"❌ {html.escape(str(exc))}",
            parse_mode=ParseMode.HTML,
            reply_markup=campaign_menu(),
        )
    return ConversationHandler.END



async def verify_upload_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return ConversationHandler.END

    _, cid_s, day_s = q.data.split(":", 2)
    context.user_data["verify_proof_campaign_link_id"] = int(cid_s)
    context.user_data["verify_proof_day"] = int(day_s)

    await q.message.reply_text(
        f"📸 <b>Upload Verification Proof — Day {day_s}/7</b>\n\n"
        "Send a screenshot/photo showing the Story or bio link.\n"
        "You may also send the screenshot as a document.\n\n"
        "Use /cancel to cancel.",
        parse_mode=ParseMode.HTML,
    )
    return VERIFY_PROOF_UPLOAD


async def verify_upload_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update):
        return ConversationHandler.END

    cid = context.user_data.get("verify_proof_campaign_link_id")
    day = context.user_data.get("verify_proof_day")
    if not cid or not day:
        await update.effective_message.reply_text("❌ Verification session expired.")
        return ConversationHandler.END

    file_id = None
    proof_type = None

    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        proof_type = "photo"
    elif update.message.document:
        file_id = update.message.document.file_id
        proof_type = "document"

    if not file_id:
        await update.effective_message.reply_text(
            "Please send a screenshot/photo or image/document file."
        )
        return VERIFY_PROOF_UPLOAD

    caption = update.message.caption or ""
    save_verification_proof(
        cid,
        day,
        file_id,
        proof_type,
        caption,
        update.effective_user.id,
    )

    rows = verification_summary_rows(day)
    row = next((x for x in rows if int(x["campaign_link_id"]) == int(cid)), None)

    context.user_data.pop("verify_proof_campaign_link_id", None)
    context.user_data.pop("verify_proof_day", None)

    await update.effective_message.reply_text(
        "✅ Proof saved.\n\n" + verification_creator_text(row, day),
        parse_mode=ParseMode.HTML,
        reply_markup=verification_creator_keyboard(row, day),
        disable_web_page_preview=True,
    )
    return ConversationHandler.END


async def verify_upload_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("verify_proof_campaign_link_id", None)
    context.user_data.pop("verify_proof_day", None)
    await update.effective_message.reply_text(
        "Verification proof upload cancelled.",
        reply_markup=campaign_menu(),
    )
    return ConversationHandler.END



async def campaign_sync_final_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return ConversationHandler.END

    await q.message.reply_text(
        "✅ <b>Sync Final Promoted Links</b>\n\n"
        "Paste the FINAL list of links actually used by the promoter, one per line.\n\n"
        "Accepted:\n"
        "• Instagram profile links\n"
        "• Telegram channel/profile links\n"
        "• Existing Batraxy landing links\n\n"
        "The bot will KEEP these links active, create any missing valid source links, "
        "then show every other campaign link and ask whether you want to DISABLE or DELETE them.\n\n"
        "Nothing is disabled/deleted until you confirm.",
        parse_mode=ParseMode.HTML,
    )
    return CAMPAIGN_SYNC_FINAL


async def campaign_sync_final_preview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update):
        return ConversationHandler.END

    raw = update.message.text or ""
    kept_rows, unresolved = resolve_or_create_final_promoted_links(raw)

    if not kept_rows:
        await update.message.reply_text(
            "❌ No valid campaign links were identified. Please paste the final links again.",
            reply_markup=campaign_menu(),
        )
        return ConversationHandler.END

    keep_ids = {int(r["id"]) for r in kept_rows}
    all_rows = all_campaign_links(include_inactive=True)
    other_rows = [r for r in all_rows if int(r["id"]) not in keep_ids]

    # Ensure final/promoted list is active.
    set_campaign_links_active_by_ids(list(keep_ids), True)

    context.user_data["sync_keep_ids"] = list(keep_ids)
    context.user_data["sync_other_ids"] = [int(r["id"]) for r in other_rows]

    lines = [
        "🔍 <b>Final Promoted Links Review</b>",
        "",
        f"Final links kept active: <b>{len(kept_rows)}</b>",
        f"Other campaign links found: <b>{len(other_rows)}</b>",
        f"Unresolved pasted items: <b>{len(unresolved)}</b>",
    ]

    if other_rows:
        lines.append("\n<b>Other links:</b>")
        for r in other_rows[:30]:
            status = "ACTIVE" if r.get("is_active") else "DISABLED"
            lines.append(
                f"• @{html.escape(str(r['instagram_username']))} "
                f"— <code>{html.escape(str(r['slug']))}</code> "
                f"({status})"
            )
        if len(other_rows) > 30:
            lines.append(f"…and {len(other_rows)-30} more.")

    if unresolved:
        lines.append("\n<b>Could not identify:</b>")
        for item in unresolved[:10]:
            lines.append(f"• <code>{html.escape(item)}</code>")

    if not other_rows:
        lines.append("\n✅ There are no extra campaign links to clean up.")
        await update.message.reply_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=campaign_menu(),
            disable_web_page_preview=True,
        )
        context.user_data.pop("sync_keep_ids", None)
        context.user_data.pop("sync_other_ids", None)
        return ConversationHandler.END

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                f"⛔ Disable Other {len(other_rows)}",
                callback_data="campaign_sync_disable_others",
            ),
            InlineKeyboardButton(
                f"🗑 Delete Other {len(other_rows)}",
                callback_data="campaign_sync_delete_confirm",
            ),
        ],
        [
            InlineKeyboardButton("✅ Keep Everything", callback_data="campaign_sync_keep_all"),
        ],
        [
            InlineKeyboardButton("❌ Cancel", callback_data="campaign_home"),
        ],
    ])

    lines.append(
        "\n<b>What should I do with the other links?</b>\n"
        "Disable = keeps history and lets you re-enable later.\n"
        "Delete = permanently removes the campaign link rows."
    )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
        disable_web_page_preview=True,
    )
    return ConversationHandler.END


async def campaign_add_single_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        await q.message.reply_text("❌ Admin access required.")
        return ConversationHandler.END
    await q.message.reply_text(
        "➕ <b>Create Creator Link</b>\n\n"
        "Send the Instagram profile link or Telegram channel/profile link.\n\n"
        "Examples:\n"
        "<code>https://www.instagram.com/saketeditt/</code>\n"
        "<code>https://t.me/examplechannel</code>\n\n"
        "The bot will identify the source and create the slug automatically.",
        parse_mode=ParseMode.HTML,
    )
    return CAMPAIGN_ADD_SINGLE


async def campaign_add_single_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update):
        return ConversationHandler.END
    raw_input = (update.message.text or "").strip()
    try:
        row, created = create_campaign_creator(raw_input)
        landing, telegram = creator_urls(row)
        source_label = CAMPAIGN_SOURCE_LABELS.get(
            row.get("source_type") or "instagram",
            row.get("source_type") or "Instagram",
        )
        await update.message.reply_text(
            ("✅ Created" if created else "ℹ️ Already existed") +
            f"\n\nSource: <b>{source_label}</b>"
            f"\nPage/Channel: <b>@{row['instagram_username']}</b>"
            f"\nSlug: <code>{row['slug']}</code>"
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
        "📚 <b>Bulk Create Links</b>\n\n"
        "Paste Instagram or Telegram profile/channel links, one per line. Up to 100 at once.\n"
        "The source and slug will be identified automatically.",
        parse_mode=ParseMode.HTML,
    )
    return CAMPAIGN_ADD_BULK


async def campaign_add_bulk_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update):
        return ConversationHandler.END
    raw = update.message.text or ""
    items, seen = [], set()
    for line in raw.splitlines():
        item = line.strip()
        if item and item.lower() not in seen:
            seen.add(item.lower())
            items.append(item)
    items = items[:100]
    created_rows, existing_rows, failed = [], [], []
    for item in items:
        try:
            row, created = create_campaign_creator(item)
            (created_rows if created else existing_rows).append(row)
        except Exception as e:
            failed.append((item, str(e)))
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




    if data == "verify_request_local_run":
        control = request_local_verifier_run()
        await q.message.reply_text(
            "🔄 <b>Bio check requested.</b>\n\n"
            "The request has been queued for your local/VPS checker.\n"
            "As soon as <code>RUN_REMOTE_LISTENER.bat</code> is running, it will start the check automatically.\n\n"
            "After it finishes, open <b>📊 Auto Report</b> for the latest results.",
            parse_mode=ParseMode.HTML,
            reply_markup=campaign_menu(),
        )
        return

    if data == "verify_local_status":
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        COUNT(*) FILTER (
                            WHERE auto_checked_at >= NOW() - INTERVAL '90 minutes'
                              AND checker_mode='local_browser'
                        ) AS recent,
                        MAX(auto_checked_at) FILTER (
                            WHERE checker_mode='local_browser'
                        ) AS last_check
                    FROM campaign_verification
                    """
                )
                s = cur.fetchone()
        last = s.get("last_check")
        last_text = last.strftime("%d %b %Y %H:%M UTC") if last else "Never"
        await q.message.reply_text(
            "💻 <b>Local Instagram Checker</b>\\n\\n"
            f"Creators checked in last 90 min: <b>{int(s.get('recent') or 0)}</b>\\n"
            f"Last result received: <b>{last_text}</b>\\n\\n"
            "This mode uses a real logged-in browser on your Windows PC, "
            "so it avoids Railway/datacenter blocking and does not require a paid API.",
            parse_mode=ParseMode.HTML,
            reply_markup=campaign_menu(),
        )
        return

    if data.startswith("verify_auto_run:"):
        day = int(data.split(":", 1)[1])
        await q.message.reply_text(
            "🤖 <b>Free verification started.</b>\n\n"
            "Checking active Instagram creators now. "
            "Instagram blocks/rate-limits are recorded as Unknown, not Missing.",
            parse_mode=ParseMode.HTML,
        )
        result = run_free_hourly_verification_once()
        rows = verification_summary_rows(day)
        await q.message.reply_text(
            f"✅ Check finished: {result['checked']} checked, {result['failed']} unavailable.\n\n"
            + automatic_verification_report_text(rows, day),
            parse_mode=ParseMode.HTML,
            reply_markup=verification_list_keyboard(rows, day=day),
        )
        return

    if data.startswith("verify_auto_report:"):
        day = int(data.split(":", 1)[1])
        rows = verification_summary_rows(day)
        await q.message.reply_text(
            automatic_verification_report_text(rows, day),
            parse_mode=ParseMode.HTML,
            reply_markup=promoter_report_keyboard(rows, day=day),
        )
        return

    if data == "verify_home":
        rows = verification_summary_rows(1)
        await q.message.reply_text(
            "✅ <b>Campaign Verification Center</b>\n\n"
            "Track whether each creator has the assigned bio link, only your link, "
            "the agreed Story, and the Story link.\n\n"
            "Status: ✅ verified  ⚠️ issue  ❌ missing  ⏳ pending\n\n"
            "Use <b>📊 Auto Report</b> to see everything in one report.\n"
            "Choose a creator or campaign day:",
            parse_mode=ParseMode.HTML,
            reply_markup=verification_list_keyboard(rows, day=1),
        )
        return

    if data.startswith("verify_day:"):
        day = int(data.split(":", 1)[1])
        rows = verification_summary_rows(day)
        await q.message.reply_text(
            f"✅ <b>Verification — Day {day}/7</b>\n\n"
            "Tap a creator to check/update compliance.",
            parse_mode=ParseMode.HTML,
            reply_markup=verification_list_keyboard(rows, day=day),
        )
        return

    if data.startswith("verify_page:"):
        _, day_s, page_s = data.split(":", 2)
        day, page = int(day_s), int(page_s)
        rows = verification_summary_rows(day)
        await q.message.reply_text(
            f"✅ <b>Verification — Day {day}/7</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=verification_list_keyboard(rows, day=day, page=page),
        )
        return

    if data.startswith("verify_creator:"):
        _, cid_s, day_s = data.split(":", 2)
        cid, day = int(cid_s), int(day_s)
        rows = verification_summary_rows(day)
        row = next((x for x in rows if int(x["campaign_link_id"]) == cid), None)
        if not row:
            await q.message.reply_text("❌ Creator not found.", reply_markup=campaign_menu())
            return
        await q.message.reply_text(
            verification_creator_text(row, day),
            parse_mode=ParseMode.HTML,
            reply_markup=verification_creator_keyboard(row, day),
            disable_web_page_preview=True,
        )
        return

    if data.startswith("verify_set:"):
        _, cid_s, day_s, field, status = data.split(":", 4)
        cid, day = int(cid_s), int(day_s)
        update_verification_status(cid, day, field, status, q.from_user.id)
        rows = verification_summary_rows(day)
        row = next((x for x in rows if int(x["campaign_link_id"]) == cid), None)
        await q.message.reply_text(
            verification_creator_text(row, day),
            parse_mode=ParseMode.HTML,
            reply_markup=verification_creator_keyboard(row, day),
            disable_web_page_preview=True,
        )
        return

    if data.startswith("verify_view_proof:"):
        _, cid_s, day_s = data.split(":", 2)
        cid, day = int(cid_s), int(day_s)
        v = get_verification_row(cid, day)
        if not v or not v.get("proof_file_id"):
            await q.message.reply_text("⏳ No proof uploaded for this day yet.")
            return
        caption = (
            f"📸 <b>Verification Proof — Day {day}/7</b>\n"
            f"{html.escape(v.get('proof_caption') or '')}"
        )
        if v.get("proof_type") == "document":
            await q.message.reply_document(
                document=v["proof_file_id"],
                caption=caption,
                parse_mode=ParseMode.HTML,
            )
        else:
            await q.message.reply_photo(
                photo=v["proof_file_id"],
                caption=caption,
                parse_mode=ParseMode.HTML,
            )
        return

    if data.startswith("verify_pdf:"):
        day = int(data.split(":", 1)[1])
        rows = verification_summary_rows(day)
        pdf_file = build_verification_pdf(rows, day)
        await q.message.reply_document(
            document=pdf_file,
            filename=f"BETROXY_Compliance_Day_{day}.pdf",
            caption=f"📄 <b>Campaign Compliance Report — Day {day}/7</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=verification_list_keyboard(rows, day=day),
        )
        return


    if data == "campaign_sync_disable_others":
        ids = context.user_data.get("sync_other_ids") or []
        count = set_campaign_links_active_by_ids(ids, False)
        context.user_data.pop("sync_keep_ids", None)
        context.user_data.pop("sync_other_ids", None)
        await q.message.reply_text(
            f"⛔ <b>{count} other campaign links disabled.</b>\n\n"
            "Your final promoted list remains active. Historical data is preserved.",
            parse_mode=ParseMode.HTML,
            reply_markup=campaign_menu(),
        )
        return

    if data == "campaign_sync_delete_confirm":
        ids = context.user_data.get("sync_other_ids") or []
        if not ids:
            await q.message.reply_text("No extra links are waiting for deletion.", reply_markup=campaign_menu())
            return
        await q.message.reply_text(
            f"⚠️ <b>Permanent Delete Confirmation</b>\n\n"
            f"You are about to permanently delete <b>{len(ids)}</b> campaign links.\n"
            "The final promoted links will remain active.\n\n"
            "Are you sure?",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    f"🗑 YES, DELETE {len(ids)}",
                    callback_data="campaign_sync_delete_others"
                )],
                [InlineKeyboardButton("❌ Cancel", callback_data="campaign_home")],
            ]),
        )
        return

    if data == "campaign_sync_delete_others":
        ids = context.user_data.get("sync_other_ids") or []
        count = delete_campaign_links_by_ids(ids)
        context.user_data.pop("sync_keep_ids", None)
        context.user_data.pop("sync_other_ids", None)
        await q.message.reply_text(
            f"🗑 <b>{count} other campaign links permanently deleted.</b>\n\n"
            "Your final promoted list remains active.",
            parse_mode=ParseMode.HTML,
            reply_markup=campaign_menu(),
        )
        return

    if data == "campaign_sync_keep_all":
        context.user_data.pop("sync_keep_ids", None)
        context.user_data.pop("sync_other_ids", None)
        await q.message.reply_text(
            "✅ No links were disabled or deleted.\n"
            "Your final promoted links remain active.",
            reply_markup=campaign_menu(),
        )
        return

    if data == "campaign_noop":
        return

    if data == "campaign_home":
        await q.message.reply_text(campaign_overview_text(), parse_mode=ParseMode.HTML, reply_markup=campaign_menu())
        return


    if data == "campaign_pdf":
        rows = instagram_tracker_stats()
        if not rows:
            await q.message.reply_text(
                "📄 No campaign data available for PDF yet.",
                reply_markup=campaign_menu(),
            )
            return

        pdf_file = build_campaign_report_pdf(rows)
        filename = f"BETROXY_Campaign_Report_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.pdf"

        await q.message.reply_document(
            document=pdf_file,
            filename=filename,
            caption=(
                "📄 <b>BETROXY Campaign Report</b>\n"
                f"Creators: {len(rows)}\n"
                "Generated from the live campaign tracker."
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=campaign_menu(),
        )
        return

    if data == "campaign_report":
        rows = instagram_tracker_stats()
        if not rows:
            await q.message.reply_text(
                "📊 <b>Full Campaign Report</b>\n\nNo data yet.",
                parse_mode=ParseMode.HTML,
                reply_markup=campaign_menu(),
            )
            return

        table = [
            f"{'Creator':<16} {'Src':<7} {'Vis':>4} {'Bot':>4} {'Web':>4} {'Dep':>4}",
            "-" * 44,
        ]

        total_vis = total_bot = total_web = total_dep = 0

        for r in rows:
            creator = ("@" + str(r["name"]))[:16]
            src = {
                "instagram": "Insta",
                "telegram": "TG",
                "meta_ads": "Meta",
                "google_ads": "Google",
            }.get(r.get("source_type") or "instagram", "Insta")

            vis = int(r["landing_visits"] or 0)
            bot_clicks = int(r["telegram_clicks"] or 0)
            web_clicks = int(r["website_clicks"] or 0)
            dep = int(r["deposits"] or 0)

            total_vis += vis
            total_bot += bot_clicks
            total_web += web_clicks
            total_dep += dep

            table.append(
                f"{creator:<16} {src:<7} "
                f"{vis:>4} {bot_clicks:>4} {web_clicks:>4} {dep:>4}"
            )

        table.extend([
            "-" * 44,
            f"{'TOTAL':<16} {'':<7} "
            f"{total_vis:>4} {total_bot:>4} {total_web:>4} {total_dep:>4}",
        ])

        text = (
            "📊 <b>Full Campaign Report</b>\n\n"
            "<pre>" + html.escape("\n".join(table)) + "</pre>"
            "\n📸 Tap a creator button below to open that Instagram page."
        )

        await q.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=campaign_report_creator_keyboard(rows),
            disable_web_page_preview=True,
        )
        return

    if data == "campaign_today":
        s = tracker_today_totals()
        table = [
            f"{'Metric':<22} {'Value':>10}",
            "-" * 33,
            f"{'Landing visits':<22} {int(s['landing_visits'] or 0):>10}",
            f"{'Unique visitors':<22} {int(s['unique_visitors'] or 0):>10}",
            f"{'BetroxyBot clicks':<22} {int(s['telegram_clicks'] or 0):>10}",
            f"{'Website clicks':<22} {int(s['website_clicks'] or 0):>10}",
            f"{'Bot starts':<22} {int(s['starts'] or 0):>10}",
            f"{'Registrations':<22} {int(s['registrations'] or 0):>10}",
            f"{'Deposits':<22} {int(s['deposits'] or 0):>10}",
            f"{'Deposit amount':<22} {float(s['deposit_amount'] or 0):>10.2f}",
        ]
        await q.message.reply_text(
            "📅 <b>Today's Campaign</b>\n\n"
            "<pre>" + html.escape("\n".join(table)) + "</pre>",
            parse_mode=ParseMode.HTML,
            reply_markup=campaign_menu(),
        )
        return

    if data == "campaign_top":
        rows = instagram_tracker_stats()
        if not rows:
            await q.message.reply_text(
                "🏆 <b>Top Performing Pages</b>\n\nNo data yet.",
                parse_mode=ParseMode.HTML,
                reply_markup=campaign_menu(),
            )
            return

        top_rows = rows[:10]
        table = [
            f"{'#':<2} {'Creator':<18} {'Vis':>4} {'Bot':>4} {'Web':>4}",
            "-" * 39,
        ]

        for i, r in enumerate(top_rows, 1):
            creator = ("@" + str(r["name"]))[:18]
            table.append(
                f"{i:<2} {creator:<18} "
                f"{int(r['landing_visits'] or 0):>4} "
                f"{int(r['telegram_clicks'] or 0):>4} "
                f"{int(r['website_clicks'] or 0):>4}"
            )

        await q.message.reply_text(
            "🏆 <b>Top Performing Pages</b>\n\n"
            "<pre>" + html.escape("\n".join(table)) + "</pre>"
            "\n📸 Tap a creator button below to open that Instagram page.",
            parse_mode=ParseMode.HTML,
            reply_markup=campaign_report_creator_keyboard(top_rows),
            disable_web_page_preview=True,
        )
        return

    if data == "campaign_links":
        kb, page, total_pages = campaign_links_keyboard(0)
        await q.message.reply_text(f"🔗 <b>Creator Tracking Links</b>\n\nPage {page+1} of {total_pages}. Tap creator name to open Instagram, or Manage to edit.", parse_mode=ParseMode.HTML, reply_markup=kb)
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
            f"Affiliate code: <code>{row['agent_code']}</code>\n"
            f"Source: <b>{CAMPAIGN_SOURCE_LABELS.get(row.get('source_type') or 'instagram', 'Instagram')}</b>\n\n"
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

    cleanup_malformed_campaign_sources()
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

    verify_proof_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                verify_upload_start,
                pattern=r"^verify_upload:"
            )
        ],
        states={
            VERIFY_PROOF_UPLOAD: [
                MessageHandler(
                    (filters.PHOTO | filters.Document.ALL),
                    verify_upload_save,
                )
            ]
        },
        fallbacks=[CommandHandler("cancel", verify_upload_cancel)],
    )

    campaign_sync_final_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                campaign_sync_final_start,
                pattern=r"^campaign_sync_final$"
            )
        ],
        states={
            CAMPAIGN_SYNC_FINAL: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    campaign_sync_final_preview,
                )
            ]
        },
        fallbacks=[CommandHandler("cancel", campaign_cancel)],
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

    campaign_edit_source_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                campaign_edit_source_start,
                pattern=r"^campaign_edit_source:"
            )
        ],
        states={
            CAMPAIGN_EDIT_SOURCE: [
                CallbackQueryHandler(
                    campaign_edit_source_save,
                    pattern=r"^campaign_source_set:"
                )
            ]
        },
        fallbacks=[CommandHandler("cancel", campaign_cancel)],
    )

    campaign_disable_by_link_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                campaign_disable_by_link_start,
                pattern=r"^campaign_disable_by_link$",
            )
        ],
        states={
            CAMPAIGN_DISABLE_BY_LINK: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    campaign_disable_by_link_save,
                )
            ]
        },
        fallbacks=[CommandHandler("cancel", campaign_cancel)],
    )

    campaign_delete_by_link_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                campaign_delete_by_link_start,
                pattern=r"^campaign_delete_by_link$",
            )
        ],
        states={
            CAMPAIGN_DELETE_BY_LINK: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    campaign_delete_by_link_resolve,
                )
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
    app.add_handler(verify_proof_conv)
    app.add_handler(campaign_sync_final_conv)
    app.add_handler(campaign_single_conv)
    app.add_handler(campaign_edit_username_conv)
    app.add_handler(campaign_edit_slug_conv)
    app.add_handler(campaign_edit_code_conv)
    app.add_handler(campaign_edit_source_conv)
    app.add_handler(campaign_disable_by_link_conv)
    app.add_handler(campaign_delete_by_link_conv)
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
    if ENABLE_RAILWAY_FREE_CHECKER:
        Thread(target=free_hourly_verification_worker, daemon=True).start()
    else:
        logger.info("Railway free Instagram checker disabled; local-browser verifier expected.")
    logger.info("Betroxy Official Bot + Instagram tracker starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
