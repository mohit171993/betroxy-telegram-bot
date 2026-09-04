import os
import logging
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

# ============================================================
# BETROXY BOT — SIMPLE SETTINGS
# Change Railway variables for links. No code change required.
# ============================================================

BRAND_NAME = "Betroxy"

WEBSITE_URL = os.getenv("WEBSITE_URL", "https://www.betroxy.com")
SUPPORT_HANDLE = os.getenv("SUPPORT_HANDLE", "@betroxysports")
SUPPORT_URL = os.getenv("SUPPORT_URL", "https://t.me/betroxysports")
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/betroxycasino")

POPULAR_URL = os.getenv("POPULAR_URL", WEBSITE_URL)
NEW_GAMES_URL = os.getenv("NEW_GAMES_URL", WEBSITE_URL)
FEATURED_URL = os.getenv("FEATURED_URL", WEBSITE_URL)
PROMOTIONS_URL = os.getenv("PROMOTIONS_URL", WEBSITE_URL)
TERMS_URL = os.getenv("TERMS_URL", "")
PRIVACY_URL = os.getenv("PRIVACY_URL", "")

WELCOME_TEXT = (
    "👑 <b>Welcome to Betroxy!</b>\n\n"
    "Your destination for entertainment, rewards and the latest updates.\n\n"
    "Choose an option below 👇"
)

EXPLORE_TEXT = (
    "🎮 <b>Explore Betroxy</b>\n\n"
    "Discover what’s available on Betroxy.\n\n"
    "Choose a category below 👇"
)

PROMOTIONS_TEXT = (
    "🎁 <b>Betroxy Promotions</b>\n\n"
    "Discover the latest promotions and special offers.\n\n"
    "Tap below to view current offers 👇"
)

UPDATES_TEXT = (
    "📢 <b>Latest Betroxy Updates</b>\n\n"
    "Stay informed about important announcements, platform updates and new features.\n\n"
    "Join the official Betroxy channel 👇"
)

SUPPORT_TEXT = (
    "💬 <b>Betroxy Support</b>\n\n"
    "Need assistance? Our support team is ready to help.\n\n"
    f"Official Telegram support: <b>{SUPPORT_HANDLE}</b>"
)

FAQ_TEXT = (
    "❓ <b>Help & FAQ</b>\n\n"
    "<b>How do I start?</b>\n"
    "Use the menu below to explore Betroxy or visit the official website.\n\n"
    "<b>Where can I find promotions?</b>\n"
    "Open the Promotions section.\n\n"
    "<b>How do I get support?</b>\n"
    f"Contact {SUPPORT_HANDLE}.\n\n"
    "<b>How do I stay updated?</b>\n"
    "Join the official Betroxy updates channel."
)

TERMS_TEXT = (
    "📄 <b>Terms & Conditions</b>\n\n"
    "Please review the official Betroxy Terms & Conditions before using the service."
)

PRIVACY_TEXT = (
    "🔒 <b>Privacy Policy</b>\n\n"
    "Please review the official Betroxy Privacy Policy for information about data handling and privacy."
)

BASE_DIR = Path(__file__).resolve().parent
WELCOME_BANNER = BASE_DIR / "welcome_banner.jpg"

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Visit Betroxy", url=WEBSITE_URL)],
        [
            InlineKeyboardButton("🎮 Explore", callback_data="explore"),
            InlineKeyboardButton("🎁 Promotions", callback_data="promotions"),
        ],
        [
            InlineKeyboardButton("📢 Latest Updates", callback_data="updates"),
            InlineKeyboardButton("💬 Support", callback_data="support"),
        ],
        [InlineKeyboardButton("❓ Help & FAQ", callback_data="faq")],
        [
            InlineKeyboardButton("📄 Terms", callback_data="terms"),
            InlineKeyboardButton("🔒 Privacy", callback_data="privacy"),
        ],
    ])

def back_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="home")]
    ])

def explore_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔥 Popular", url=POPULAR_URL)],
        [InlineKeyboardButton("🆕 New Games", url=NEW_GAMES_URL)],
        [InlineKeyboardButton("⭐ Featured", url=FEATURED_URL)],
        [InlineKeyboardButton("🌐 Visit Betroxy", url=WEBSITE_URL)],
        [InlineKeyboardButton("⬅️ Back to Menu", callback_data="home")],
    ])

def promotions_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎁 View Promotions", url=PROMOTIONS_URL)],
        [InlineKeyboardButton("⬅️ Back to Menu", callback_data="home")],
    ])

def updates_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Official Channel", url=CHANNEL_URL)],
        [InlineKeyboardButton("🌐 Visit Betroxy", url=WEBSITE_URL)],
        [InlineKeyboardButton("⬅️ Back to Menu", callback_data="home")],
    ])

def support_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Chat with Support", url=SUPPORT_URL)],
        [InlineKeyboardButton("🌐 Visit Betroxy", url=WEBSITE_URL)],
        [InlineKeyboardButton("⬅️ Back to Menu", callback_data="home")],
    ])

def legal_menu(kind):
    url = TERMS_URL if kind == "terms" else PRIVACY_URL
    label = "📄 Open Terms & Conditions" if kind == "terms" else "🔒 Open Privacy Policy"
    rows = []
    if url:
        rows.append([InlineKeyboardButton(label, url=url)])
    rows.append([InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="home")])
    return InlineKeyboardMarkup(rows)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if WELCOME_BANNER.exists():
        with WELCOME_BANNER.open("rb") as photo:
            await update.message.reply_photo(
                photo=photo,
                caption=WELCOME_TEXT,
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu(),
            )
    else:
        await update.message.reply_text(
            WELCOME_TEXT,
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(),
            disable_web_page_preview=True,
        )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        FAQ_TEXT,
        parse_mode=ParseMode.HTML,
        reply_markup=back_menu(),
        disable_web_page_preview=True,
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    screens = {
        "home": (WELCOME_TEXT, main_menu()),
        "explore": (EXPLORE_TEXT, explore_menu()),
        "promotions": (PROMOTIONS_TEXT, promotions_menu()),
        "updates": (UPDATES_TEXT, updates_menu()),
        "support": (SUPPORT_TEXT, support_menu()),
        "faq": (FAQ_TEXT, back_menu()),
        "terms": (TERMS_TEXT, legal_menu("terms")),
        "privacy": (PRIVACY_TEXT, legal_menu("privacy")),
    }

    if query.data not in screens:
        return

    text, keyboard = screens[query.data]

    if query.message.photo:
        await query.edit_message_caption(
            caption=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )
    else:
        await query.edit_message_text(
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled bot error", exc_info=context.error)

def main():
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is missing. Add it in Railway > Variables.")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_error_handler(error_handler)

    logger.info("%s bot starting...", BRAND_NAME)
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
