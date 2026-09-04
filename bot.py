import os
import logging
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing")

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
    "https://api.whatsapp.com/send/?phone=447777352382"
    "&text=Hi%2C+I+need+support&type=phone_number&app_absent=0"
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def get_db():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


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
                    joined_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
        conn.commit()


def find_agent_by_code(code):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM agents WHERE LOWER(code)=LOWER(%s) LIMIT 1",
                (code,),
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


def save_referral(user, agent, payload):
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
                "SELECT COUNT(*) AS total_referrals FROM referrals WHERE agent_id=%s",
                (agent["id"],),
            )
            total = cur.fetchone()["total_referrals"]
            return {"agent": agent, "referrals": total}


def get_agent_users(code):
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
                SELECT * FROM referrals
                WHERE agent_id=%s
                ORDER BY joined_at DESC
                LIMIT 20
                """,
                (agent["id"],),
            )
            return cur.fetchall()


def main_menu():
    return InlineKeyboardMarkup([
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
    ])


def support_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Telegram Support", url=TELEGRAM_SUPPORT_URL)],
        [InlineKeyboardButton("🟢 WhatsApp Support", url=WHATSAPP_SUPPORT_URL)],
        [InlineKeyboardButton("⬅️ Main Menu", callback_data="home")],
    ])


def is_admin(user_id):
    return user_id == ADMIN_ID


async def require_admin(update):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("❌ Admin access required.")
        return False
    return True


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    payload = None
    agent = None

    if context.args:
        payload = context.args[0].strip()
        if payload.lower().startswith("agent_"):
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
        reply_markup=main_menu(),
        disable_web_page_preview=True,
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "home":
        await query.message.reply_text(
            "👑 <b>Betroxy Official</b>\n\nChoose an option below 👇",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(),
        )
    elif query.data == "support":
        await query.message.reply_text(
            "💬 <b>Betroxy Support</b>\n\nChoose your preferred support channel:",
            parse_mode=ParseMode.HTML,
            reply_markup=support_menu(),
        )


async def agent_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update):
        return
    if len(context.args) < 3:
        await update.message.reply_text(
            "Usage:\n/agent_add Name code commission_rate\n\n"
            "Example:\n/agent_add Rahul rahul 5"
        )
        return

    name = context.args[0]
    code = context.args[1].lower()
    try:
        commission_rate = float(context.args[2])
    except ValueError:
        await update.message.reply_text("❌ Commission rate must be a number.")
        return

    try:
        agent = create_agent(name, code, commission_rate)
    except psycopg.errors.UniqueViolation:
        await update.message.reply_text("❌ Agent code already exists.")
        return

    link = f"https://t.me/BetroxyOfficialBot?start=agent_{agent['code']}"
    await update.message.reply_text(
        "✅ <b>Affiliate Created</b>\n\n"
        f"Name: {agent['name']}\n"
        f"Code: <code>{agent['code']}</code>\n"
        f"Commission: {agent['commission_rate']}%\n\n"
        f"Referral Link:\n<code>{link}</code>",
        parse_mode=ParseMode.HTML,
    )


async def agent_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update):
        return
    if not context.args:
        await update.message.reply_text("Usage:\n/agent agent_code")
        return

    stats = get_agent_stats(context.args[0])
    if not stats:
        await update.message.reply_text("❌ Agent not found.")
        return

    agent = stats["agent"]
    link = f"https://t.me/BetroxyOfficialBot?start=agent_{agent['code']}"
    await update.message.reply_text(
        "📊 <b>Affiliate Stats</b>\n\n"
        f"Name: {agent['name']}\n"
        f"Code: <code>{agent['code']}</code>\n"
        f"Commission: {agent['commission_rate']}%\n"
        f"Status: {'Active' if agent['is_active'] else 'Inactive'}\n\n"
        f"👥 Referred Users: {stats['referrals']}\n\n"
        f"🔗 Link:\n<code>{link}</code>",
        parse_mode=ParseMode.HTML,
    )


async def agent_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update):
        return
    if not context.args:
        await update.message.reply_text("Usage:\n/agent_users agent_code")
        return

    users = get_agent_users(context.args[0])
    if users is None:
        await update.message.reply_text("❌ Agent not found.")
        return
    if not users:
        await update.message.reply_text("No referred users yet.")
        return

    text = "👥 <b>Recent Referred Users</b>\n\n"
    for user in users:
        username = f"@{user['telegram_username']}" if user["telegram_username"] else "No username"
        text += (
            f"• {user['first_name'] or 'Unknown'} | {username} "
            f"| <code>{user['telegram_user_id']}</code>\n"
        )

    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👑 <b>Betroxy Official</b>\n\nChoose an option below 👇",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu(),
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Bot error", exc_info=context.error)


def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("agent_add", agent_add))
    app.add_handler(CommandHandler("agent", agent_stats))
    app.add_handler(CommandHandler("agent_users", agent_users))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_handler))
    app.add_error_handler(error_handler)
    logger.info("Betroxy affiliate bot starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
