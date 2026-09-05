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

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing")

if not ADMIN_ID:
    raise RuntimeError("ADMIN_ID is missing")


BOT_USERNAME = "BetroxyOfficialBot"

# Exact Web App URL shown in BotFather for the original Betroxy bot
APP_URL = "https://betroxy.com/"

# Public/support URLs
UPDATES_URL = "https://t.me/betroxycasino"
TELEGRAM_SUPPORT_URL = "https://t.me/betroxysports"

WHATSAPP_SUPPORT_URL = (
    "https://api.whatsapp.com/send/"
    "?phone=447777352382"
    "&text=Hi%2C+I+need+support"
    "&type=phone_number"
    "&app_absent=0"
)


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ============================================================
# CONVERSATION STATES
# ============================================================

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
    return psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
    )


def init_db():

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
                    joined_at TIM
