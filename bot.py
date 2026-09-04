import os
import re
import logging
from pathlib import Path

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

# ============================================================
# BETROXY BOT — FINAL CLEAN VERSION
# ============================================================

BRAND_NAME = "Betroxy"

# Railway variables
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
RESPONSIBLE_PLAY_URL = os.getenv("RESPONSIBLE_PLAY_URL", "")

# Banner file must be in GitHub root with this exact name
BASE_DIR = Path(__file__).resolve().parent
WELCOME_BANNER = BASE_DIR / "welcome_banner.jpg"

# ============================================================
# TEXT
# ============================================================

WELCOME_TEXT = (
    "👑 <b>Welcome to Betroxy</b>\n\n"
    "Explore games, promotions, updates and support.\n\n"
    "Choose an option below 👇"
)

HELP_TEXT = (
    "❓ <b>Betroxy Help</b>\n\n"
    "You can type your question directly in this chat.\n\n"
    "Examples:\n"
    "• I need support\n"
    "• Show promotions\n"
    "• New games\n"
    "• Latest updates\n"
    "• Account help\n"
    "• Verification help\n"
    "• Technical problem\n\n"
    "Or use the menu below."
)

FALLBACK_TEXT = (
    "I can help with Betroxy navigation, promotions, account guidance, "
    "verification, updates and support.\n\n"
    "For account-specific or payment-related issues, please use official support."
)

# ============================================================
# MENUS
# ============================================================

def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Open Betroxy", url=WEBSITE_URL)],
        [
            InlineKeyboardButton("🎮 Games & Sports", callback_data="explore"),
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


def support_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Chat with Support", url=SUPPORT_URL)],
        [InlineKeyboardButton("🌐 Open Betroxy", url=WEBSITE_URL)],
        [InlineKeyboardButton("⬅️ Back to Menu", callback_data="home")],
    ])


def explore_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔥 Popular", url=POPULAR_URL)],
        [InlineKeyboardButton("🆕 New Games", url=NEW_GAMES_URL)],
        [InlineKeyboardButton("⭐ Featured", url=FEATURED_URL)],
        [InlineKeyboardButton("🌐 Open Betroxy", url=WEBSITE_URL)],
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
        [InlineKeyboardButton("⬅️ Back to Menu", callback_data="home")],
    ])


def legal_menu(kind):
    if kind == "terms":
        url = TERMS_URL
        label = "📄 Open Terms & Conditions"
    else:
        url = PRIVACY_URL
        label = "🔒 Open Privacy Policy"

    rows = []

    if url:
        rows.append([InlineKeyboardButton(label, url=url)])

    rows.append([
        InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="home")
    ])

    return InlineKeyboardMarkup(rows)


def responsible_menu():
    rows = []

    if RESPONSIBLE_PLAY_URL:
        rows.append([
            InlineKeyboardButton(
                "🛡 Responsible Play",
                url=RESPONSIBLE_PLAY_URL
            )
        ])

    rows.append([
        InlineKeyboardButton("💬 Contact Support", url=SUPPORT_URL)
    ])

    rows.append([
        InlineKeyboardButton("⬅️ Main Menu", callback_data="home")
    ])

    return InlineKeyboardMarkup(rows)

# ============================================================
# TEXT DETECTION
# ============================================================

def normalize(text):
    text = text.lower().strip()
    return re.sub(r"\s+", " ", text)


def contains_any(text, keywords):
    return any(keyword in text for keyword in keywords)


def detect_reply(user_text):
    text = normalize(user_text)

    # Greeting
    if text in {
        "hi",
        "hello",
        "hey",
        "hii",
        "hiii",
        "good morning",
        "good evening",
    }:
        return (
            "👋 <b>Hello!</b>\n\n"
            "Welcome to Betroxy. How can I help you today?",
            main_menu(),
        )

    # Thanks
    if contains_any(text, [
        "thank you",
        "thanks",
        "thx",
    ]):
        return (
            "You're welcome. If you need anything else, use the menu below.",
            main_menu(),
        )

    # Support
    if contains_any(text, [
        "support",
        "help me",
        "agent",
        "human",
        "representative",
        "customer care",
        "contact support",
        "speak to someone",
    ]):
        return (
            "💬 <b>Betroxy Support</b>\n\n"
            f"For personal assistance, contact official support at "
            f"<b>{SUPPORT_HANDLE}</b>.",
            support_menu(),
        )

    # Account help
    if contains_any(text, [
        "account",
        "login",
        "log in",
        "password",
        "forgot password",
        "locked",
        "blocked account",
        "cannot login",
        "can't login",
    ]):
        return (
            "👤 <b>Account Help</b>\n\n"
            "For login, password, account access or account-status issues, "
            "please use official support so your account can be checked securely.",
            support_menu(),
        )

    # Verification
    if contains_any(text, [
        "verify",
        "verification",
        "kyc",
        "document",
        "documents",
        "id verification",
        "identity",
    ]):
        return (
            "🪪 <b>Verification Help</b>\n\n"
            "For verification or document-review questions, please use official support.\n\n"
            "Do not send sensitive personal documents directly in this bot chat.",
            support_menu(),
        )

    # Payment / withdrawal
    if contains_any(text, [
        "deposit",
        "withdraw",
        "withdrawal",
        "payment",
        "payout",
        "money",
        "fund",
        "funds",
        "transaction",
        "bank",
    ]):
        return (
            "💳 <b>Payment & Withdrawal Help</b>\n\n"
            "For account-specific deposits, withdrawals, transaction status "
            "or payment issues, please contact official support.",
            support_menu(),
        )

    # Technical
    if contains_any(text, [
        "technical",
        "error",
        "not working",
        "issue",
        "bug",
        "loading",
        "crash",
        "website problem",
        "app problem",
    ]):
        return (
            "🛠 <b>Technical Support</b>\n\n"
            "Please try reopening the website or app first.\n\n"
            "If the issue continues, contact support and include a screenshot of the error.",
            support_menu(),
        )

    # Promotions
    if contains_any(text, [
        "promotion",
        "promotions",
        "offer",
        "offers",
        "bonus",
        "bonuses",
        "reward",
        "rewards",
    ]):
        return (
            "🎁 <b>Betroxy Promotions</b>\n\n"
            "View the latest Betroxy promotions and offers below.",
            promotions_menu(),
        )

    # Games
    if contains_any(text, [
        "new game",
        "new games",
        "popular",
        "featured",
        "games",
        "sports",
        "explore",
    ]):
        return (
            "🎮 <b>Games & Sports</b>\n\n"
            "Choose a section below.",
            explore_menu(),
        )

    # Updates
    if contains_any(text, [
        "update",
        "updates",
        "news",
        "announcement",
        "announcements",
        "channel",
    ]):
        return (
            "📢 <b>Latest Updates</b>\n\n"
            "Join the official Betroxy channel for news and platform updates.",
            updates_menu(),
        )

    # Website
    if contains_any(text, [
        "website",
        "site",
        "betroxy.com",
        "open betroxy",
        "visit betroxy",
    ]):
        return (
            "🌐 <b>Betroxy Website</b>\n\n"
            "Open the official Betroxy website below.",
            InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🌐 Open Betroxy",
                        url=WEBSITE_URL
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ Main Menu",
                        callback_data="home"
                    )
                ],
            ]),
        )

    # Terms
    if contains_any(text, [
        "terms",
        "terms and conditions",
        "conditions",
    ]):
        if TERMS_URL:
            return (
                "📄 <b>Terms & Conditions</b>\n\n"
                "Use the button below to review the official Terms & Conditions.",
                legal_menu("terms"),
            )

        return (
            "📄 <b>Terms & Conditions</b>\n\n"
            "The official Terms link has not been added yet.\n\n"
            "Please contact support if needed.",
            support_menu(),
        )

    # Privacy
    if contains_any(text, [
        "privacy",
        "privacy policy",
        "data policy",
    ]):
        if PRIVACY_URL:
            return (
                "🔒 <b>Privacy Policy</b>\n\n"
                "Use the button below to review the official Privacy Policy.",
                legal_menu("privacy"),
            )

        return (
            "🔒 <b>Privacy Policy</b>\n\n"
            "The official Privacy Policy link has not been added yet.",
            support_menu(),
        )

    # Responsible use
    if contains_any(text, [
        "responsible",
        "responsible play",
        "age",
        "18",
        "underage",
        "gambling problem",
        "self exclude",
        "self-exclude",
    ]):
        return (
            "🛡 <b>Responsible Play</b>\n\n"
            "Betroxy services should only be used where lawful and by users "
            "who meet the applicable age requirement.\n\n"
            "If you need help managing your play or account access, "
            "use the options below.",
            responsible_menu(),
        )

    # Fallback
    return (
        FALLBACK_TEXT,
        InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "💬 Talk to Support",
                    url=SUPPORT_URL
                )
            ],
            [
                InlineKeyboardButton(
                    "🏠 Main Menu",
                    callback_data="home"
                )
            ],
        ]),
    )

# ============================================================
# COMMANDS
# ============================================================

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


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.message.reply_text(
        HELP_TEXT,
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu(),
        disable_web_page_preview=True,
    )

# ============================================================
# BUTTON HANDLER
# ============================================================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    screens = {
        "home": (
            WELCOME_TEXT,
            main_menu()
        ),

        "explore": (
            "🎮 <b>Games & Sports</b>\n\n"
            "Choose a section below 👇",
            explore_menu(),
        ),

        "promotions": (
            "🎁 <b>Betroxy Promotions</b>\n\n"
            "View the latest promotions and offers below.",
            promotions_menu(),
        ),

        "updates": (
            "📢 <b>Latest Updates</b>\n\n"
            "Join the official Betroxy channel.",
            updates_menu(),
        ),

        "support": (
            "💬 <b>Betroxy Support</b>\n\n"
            f"Official Telegram support: "
            f"<b>{SUPPORT_HANDLE}</b>",
            support_menu(),
        ),

        "faq": (
            HELP_TEXT,
            back_menu(),
        ),

        "terms": (
            "📄 <b>Terms & Conditions</b>",
            legal_menu("terms"),
        ),

        "privacy": (
            "🔒 <b>Privacy Policy</b>",
            legal_menu("privacy"),
        ),
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

# ============================================================
# NORMAL CHAT
# ============================================================

async def chat_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not update.message:
        return

    if not update.message.text:
        return

    text, keyboard = detect_reply(update.message.text)

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )

# ============================================================
# LOGGING / ERROR
# ============================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):
    logger.exception(
        "Unhandled bot error",
        exc_info=context.error
    )

# ============================================================
# START BOT
# ============================================================

def main():
    token = os.getenv("BOT_TOKEN")

    if not token:
        raise RuntimeError(
            "BOT_TOKEN is missing. Add it in Railway > Variables."
        )

    app = Application.builder().token(token).build()

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    app.add_handler(
        CommandHandler(
            "help",
            help_command
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            button_handler
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            chat_handler
        )
    )

    app.add_error_handler(
        error_handler
    )

    logger.info(
        "%s final hybrid bot starting...",
        BRAND_NAME
    )

    app.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
