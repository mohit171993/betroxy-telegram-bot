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
# BETROXY OFFICIAL BOT — PROMOTION / MARKETING FUNNEL
# @betroxyofficialbot
#
# Actual play/product bot:
# @betroxybot
# ============================================================

BRAND_NAME = "Betroxy"

PLAY_BOT_URL = os.getenv("PLAY_BOT_URL", "https://t.me/betroxybot")
WEBSITE_URL = os.getenv("WEBSITE_URL", "https://www.betroxy.com")
SUPPORT_HANDLE = os.getenv("SUPPORT_HANDLE", "@betroxysports")
SUPPORT_URL = os.getenv("SUPPORT_URL", "https://t.me/betroxysports")
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/betroxycasino")

PROMOTIONS_URL = os.getenv("PROMOTIONS_URL", WEBSITE_URL)
WELCOME_OFFER_URL = os.getenv("WELCOME_OFFER_URL", PROMOTIONS_URL)
SPORTS_PROMO_URL = os.getenv("SPORTS_PROMO_URL", PROMOTIONS_URL)
CASINO_PROMO_URL = os.getenv("CASINO_PROMO_URL", PROMOTIONS_URL)
VIP_URL = os.getenv("VIP_URL", WEBSITE_URL)
LIMITED_OFFER_URL = os.getenv("LIMITED_OFFER_URL", PROMOTIONS_URL)

TERMS_URL = os.getenv("TERMS_URL", "")
PRIVACY_URL = os.getenv("PRIVACY_URL", "")

BASE_DIR = Path(__file__).resolve().parent
WELCOME_BANNER = BASE_DIR / "welcome_banner.jpg"
PROMOTION_BANNER = BASE_DIR / "promotion_banner.png"
NEWS_BANNER = BASE_DIR / "news_banner.png"

WELCOME_TEXT = (
    "👑 <b>Welcome to Betroxy</b>\n\n"
    "Get the latest promotions, updates and official support.\n\n"
    "Ready to play? Open the Betroxy play bot below 👇"
)

PROMOTIONS_TEXT = (
    "🎁 <b>Betroxy Promotions</b>\n\n"
    "Discover the latest rewards, special offers and limited-time promotions.\n\n"
    "Choose a category below 👇"
)

UPDATES_TEXT = (
    "📢 <b>Betroxy Latest Updates</b>\n\n"
    "Stay informed about promotions, announcements and important platform updates."
)

SUPPORT_TEXT = (
    "💬 <b>Betroxy Support</b>\n\n"
    f"For personal assistance, contact official support at <b>{SUPPORT_HANDLE}</b>."
)

HELP_TEXT = (
    "❓ <b>Help & FAQ</b>\n\n"
    "<b>Where do I play?</b>\n"
    "Use the 🎮 Play on Betroxy button to open @betroxybot.\n\n"
    "<b>Where can I see promotions?</b>\n"
    "Open 🎁 Promotions from the main menu.\n\n"
    "<b>How do I get support?</b>\n"
    f"Contact {SUPPORT_HANDLE}.\n\n"
    "<b>How do I receive updates?</b>\n"
    "Join the official Betroxy updates channel."
)

FALLBACK_TEXT = (
    "I can help with Betroxy promotions, updates, support and general navigation.\n\n"
    "To play, use the 🎮 Play on Betroxy button."
)


def main_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🎮 Play on Betroxy",
                url=PLAY_BOT_URL
            )
        ],
        [
            InlineKeyboardButton(
                "🎁 Promotions",
                callback_data="promotions"
            ),
            InlineKeyboardButton(
                "📢 Latest Updates",
                callback_data="updates"
            ),
        ],
        [
            InlineKeyboardButton(
                "💬 Support",
                callback_data="support"
            ),
            InlineKeyboardButton(
                "❓ Help & FAQ",
                callback_data="faq"
            ),
        ],
        [
            InlineKeyboardButton(
                "🌐 Website",
                url=WEBSITE_URL
            )
        ],
        [
            InlineKeyboardButton(
                "📄 Terms",
                callback_data="terms"
            ),
            InlineKeyboardButton(
                "🔒 Privacy",
                callback_data="privacy"
            ),
        ],
    ])


def promotions_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🎁 Welcome Offer",
                url=WELCOME_OFFER_URL
            )
        ],
        [
            InlineKeyboardButton(
                "⚽ Sports Promotions",
                url=SPORTS_PROMO_URL
            ),
            InlineKeyboardButton(
                "🎰 Casino Promotions",
                url=CASINO_PROMO_URL
            ),
        ],
        [
            InlineKeyboardButton(
                "👑 VIP Rewards",
                url=VIP_URL
            )
        ],
        [
            InlineKeyboardButton(
                "⏳ Limited-Time Offers",
                url=LIMITED_OFFER_URL
            )
        ],
        [
            InlineKeyboardButton(
                "🌐 View All Promotions",
                url=PROMOTIONS_URL
            )
        ],
        [
            InlineKeyboardButton(
                "🎮 Play on Betroxy",
                url=PLAY_BOT_URL
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Back to Main Menu",
                callback_data="home"
            )
        ],
    ])


def updates_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📢 Join Official Updates",
                url=CHANNEL_URL
            )
        ],
        [
            InlineKeyboardButton(
                "🎮 Play on Betroxy",
                url=PLAY_BOT_URL
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Back to Main Menu",
                callback_data="home"
            )
        ],
    ])


def support_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "💬 Chat with Support",
                url=SUPPORT_URL
            )
        ],
        [
            InlineKeyboardButton(
                "🎮 Play on Betroxy",
                url=PLAY_BOT_URL
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Back to Main Menu",
                callback_data="home"
            )
        ],
    ])


def help_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🎮 Play on Betroxy",
                url=PLAY_BOT_URL
            )
        ],
        [
            InlineKeyboardButton(
                "💬 Contact Support",
                url=SUPPORT_URL
            )
        ],
        [
            InlineKeyboardButton(
                "📢 Latest Updates",
                url=CHANNEL_URL
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Back to Main Menu",
                callback_data="home"
            )
        ],
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
        rows.append([
            InlineKeyboardButton(
                label,
                url=url
            )
        ])

    rows.append([
        InlineKeyboardButton(
            "💬 Contact Support",
            url=SUPPORT_URL
        )
    ])

    rows.append([
        InlineKeyboardButton(
            "⬅️ Back to Main Menu",
            callback_data="home"
        )
    ])

    return InlineKeyboardMarkup(rows)


def normalize(text):
    return re.sub(
        r"\s+",
        " ",
        text.lower().strip()
    )


def contains_any(text, keywords):
    return any(
        keyword in text
        for keyword in keywords
    )


async def send_photo_or_text(
    message,
    image_path,
    text,
    keyboard
):
    if image_path.exists():
        with image_path.open("rb") as photo:
            await message.reply_photo(
                photo=photo,
                caption=text,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
    else:
        await message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await send_photo_or_text(
        update.message,
        WELCOME_BANNER,
        WELCOME_TEXT,
        main_menu()
    )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.message.reply_text(
        HELP_TEXT,
        parse_mode=ParseMode.HTML,
        reply_markup=help_menu(),
        disable_web_page_preview=True,
    )


async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    if query.data == "home":
        await query.message.reply_text(
            WELCOME_TEXT,
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(),
            disable_web_page_preview=True,
        )
        return

    if query.data == "promotions":
        await send_photo_or_text(
            query.message,
            PROMOTION_BANNER,
            PROMOTIONS_TEXT,
            promotions_menu()
        )
        return

    if query.data == "updates":
        await send_photo_or_text(
            query.message,
            NEWS_BANNER,
            UPDATES_TEXT,
            updates_menu()
        )
        return

    if query.data == "support":
        await query.message.reply_text(
            SUPPORT_TEXT,
            parse_mode=ParseMode.HTML,
            reply_markup=support_menu(),
            disable_web_page_preview=True,
        )
        return

    if query.data == "faq":
        await query.message.reply_text(
            HELP_TEXT,
            parse_mode=ParseMode.HTML,
            reply_markup=help_menu(),
            disable_web_page_preview=True,
        )
        return

    if query.data == "terms":
        if TERMS_URL:
            text = (
                "📄 <b>Terms & Conditions</b>\n\n"
                "Use the button below to review the official "
                "Betroxy Terms & Conditions."
            )
        else:
            text = (
                "📄 <b>Terms & Conditions</b>\n\n"
                "The official Terms link has not been configured yet."
            )

        await query.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=legal_menu("terms"),
            disable_web_page_preview=True,
        )
        return

    if query.data == "privacy":
        if PRIVACY_URL:
            text = (
                "🔒 <b>Privacy Policy</b>\n\n"
                "Use the button below to review the official "
                "Betroxy Privacy Policy."
            )
        else:
            text = (
                "🔒 <b>Privacy Policy</b>\n\n"
                "The official Privacy Policy link has not been configured yet."
            )

        await query.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=legal_menu("privacy"),
            disable_web_page_preview=True,
        )


def detect_reply(user_text):
    text = normalize(user_text)

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
            "Welcome to Betroxy. What would you like to do?",
            main_menu(),
        )

    if contains_any(
        text,
        [
            "play",
            "bet",
            "casino",
            "sportsbook",
            "exchange",
            "game",
            "games",
        ]
    ):
        return (
            "🎮 <b>Play on Betroxy</b>\n\n"
            "Open the Betroxy play bot to access Casino, "
            "Sportsbook and Exchange.",
            InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🎮 Open @betroxybot",
                        url=PLAY_BOT_URL
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

    if contains_any(
        text,
        [
            "promotion",
            "promotions",
            "offer",
            "offers",
            "bonus",
            "reward",
            "rewards",
            "vip",
        ]
    ):
        return (
            PROMOTIONS_TEXT,
            promotions_menu()
        )

    if contains_any(
        text,
        [
            "update",
            "updates",
            "news",
            "announcement",
            "channel",
        ]
    ):
        return (
            UPDATES_TEXT,
            updates_menu()
        )

    if contains_any(
        text,
        [
            "support",
            "help me",
            "agent",
            "human",
            "customer care",
            "contact",
            "problem",
            "issue",
            "withdraw",
            "withdrawal",
            "deposit",
            "payment",
            "verification",
            "kyc",
            "login",
            "password",
        ]
    ):
        return (
            SUPPORT_TEXT,
            support_menu()
        )

    if contains_any(
        text,
        [
            "terms",
            "condition",
        ]
    ):
        return (
            "📄 <b>Terms & Conditions</b>",
            legal_menu("terms")
        )

    if contains_any(
        text,
        [
            "privacy",
            "data policy",
        ]
    ):
        return (
            "🔒 <b>Privacy Policy</b>",
            legal_menu("privacy")
        )

    return (
        FALLBACK_TEXT,
        main_menu()
    )


async def chat_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not update.message:
        return

    if not update.message.text:
        return

    text, keyboard = detect_reply(
        update.message.text
    )

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )


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


def main():
    token = os.getenv("BOT_TOKEN")

    if not token:
        raise RuntimeError(
            "BOT_TOKEN is missing. "
            "Add it in Railway > Variables."
        )

    app = (
        Application
        .builder()
        .token(token)
        .build()
    )

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
        "%s promotional bot starting...",
        BRAND_NAME
    )

    app.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
