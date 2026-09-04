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
# BETROXY OFFICIAL BOT
# Promotion / Discovery / Support Bot
# @betroxyofficialbot
# ============================================================

BRAND_NAME = "Betroxy"

# ------------------------------------------------------------
# Core destinations
# ------------------------------------------------------------

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

WEBSITE_URL = "https://www.betroxy.com"

# Optional legal links can be added later in Railway Variables
TERMS_URL = os.getenv("TERMS_URL", "")
PRIVACY_URL = os.getenv("PRIVACY_URL", "")

# ------------------------------------------------------------
# Optional banners in GitHub root
# ------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent

WELCOME_BANNER = BASE_DIR / "welcome_banner.jpg"
PROMOTION_BANNER = BASE_DIR / "promotion_banner.png"
NEWS_BANNER = BASE_DIR / "news_banner.png"

# ============================================================
# TEXT
# ============================================================

WELCOME_TEXT = (
    "👑 <b>Welcome to Betroxy Official</b>\n\n"
    "Discover games, promotions, VIP rewards, latest updates "
    "and official support.\n\n"
    "Choose an option below 👇"
)

PROMOTION_TEXT = (
    "🎁 <b>Betroxy Promotions</b>\n\n"
    "Discover the latest offers, rewards and special promotions.\n\n"
    "Choose an option below 👇"
)

UPDATES_TEXT = (
    "📢 <b>Betroxy Latest Updates</b>\n\n"
    "Stay informed about promotions, announcements and "
    "important platform updates."
)

SUPPORT_TEXT = (
    "💬 <b>Betroxy Support</b>\n\n"
    "Choose your preferred support channel below."
)

HELP_TEXT = (
    "❓ <b>Help & FAQ</b>\n\n"
    "<b>Where do I play?</b>\n"
    "Use 🎮 Play on Betroxy or choose Casino, Sportsbook, "
    "Exchange, Popular Games or Crash Games.\n\n"
    "<b>Where are the promotions?</b>\n"
    "Tap 🎁 Promotions.\n\n"
    "<b>Where can I see VIP benefits?</b>\n"
    "Tap 👑 VIP Club.\n\n"
    "<b>How do I get support?</b>\n"
    "Tap 💬 Support and choose Telegram or WhatsApp.\n\n"
    "<b>Where do I get updates?</b>\n"
    "Tap 📢 Latest Updates."
)

FALLBACK_TEXT = (
    "I can help you find Betroxy games, promotions, VIP, "
    "updates and support.\n\n"
    "Choose an option below 👇"
)

# ============================================================
# MENUS
# ============================================================

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
                "🎰 Casino",
                url=CASINO_URL
            ),
            InlineKeyboardButton(
                "⚽ Sportsbook",
                url=SPORTSBOOK_URL
            ),
        ],
        [
            InlineKeyboardButton(
                "🔄 Exchange",
                url=EXCHANGE_URL
            ),
            InlineKeyboardButton(
                "🔥 Popular Games",
                url=POPULAR_GAMES_URL
            ),
        ],
        [
            InlineKeyboardButton(
                "🚀 Crash Games",
                url=CRASH_GAMES_URL
            ),
            InlineKeyboardButton(
                "🎁 Promotions",
                callback_data="promotions"
            ),
        ],
        [
            InlineKeyboardButton(
                "👑 VIP Club",
                url=VIP_URL
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
                "🛡 Responsible Play",
                url=RESPONSIBLE_URL
            ),
        ],
        [
            InlineKeyboardButton(
                "❓ Help & FAQ",
                callback_data="faq"
            )
        ],
    ])


def promotions_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🎁 View Promotions",
                url=PROMOTIONS_URL
            )
        ],
        [
            InlineKeyboardButton(
                "👑 VIP Club",
                url=VIP_URL
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
                url=UPDATES_URL
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
                "💬 Telegram Support",
                url=TELEGRAM_SUPPORT_URL
            )
        ],
        [
            InlineKeyboardButton(
                "🟢 WhatsApp Support",
                url=WHATSAPP_SUPPORT_URL
            )
        ],
        [
            InlineKeyboardButton(
                "🛡 Responsible Play",
                url=RESPONSIBLE_URL
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
    rows = [
        [
            InlineKeyboardButton(
                "🎮 Play on Betroxy",
                url=PLAY_BOT_URL
            )
        ],
        [
            InlineKeyboardButton(
                "🎁 Promotions",
                url=PROMOTIONS_URL
            ),
            InlineKeyboardButton(
                "👑 VIP Club",
                url=VIP_URL
            ),
        ],
        [
            InlineKeyboardButton(
                "💬 Support",
                callback_data="support"
            )
        ],
        [
            InlineKeyboardButton(
                "📢 Latest Updates",
                url=UPDATES_URL
            )
        ],
        [
            InlineKeyboardButton(
                "🛡 Responsible Play",
                url=RESPONSIBLE_URL
            )
        ],
    ]

    if TERMS_URL:
        rows.append([
            InlineKeyboardButton(
                "📄 Terms & Conditions",
                url=TERMS_URL
            )
        ])

    if PRIVACY_URL:
        rows.append([
            InlineKeyboardButton(
                "🔒 Privacy Policy",
                url=PRIVACY_URL
            )
        ])

    rows.append([
        InlineKeyboardButton(
            "⬅️ Back to Main Menu",
            callback_data="home"
        )
    ])

    return InlineKeyboardMarkup(rows)


# ============================================================
# HELPERS
# ============================================================

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


# ============================================================
# COMMANDS
# ============================================================

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


# ============================================================
# BUTTON HANDLER
# ============================================================

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
            PROMOTION_TEXT,
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


# ============================================================
# FREE-TEXT CHAT
# ============================================================

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
            "Welcome to Betroxy Official. What would you like to explore?",
            main_menu(),
        )

    if contains_any(
        text,
        ["casino"]
    ):
        return (
            "🎰 <b>Betroxy Casino</b>\n\n"
            "Open Casino directly inside Telegram.",
            InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🎰 Open Casino",
                        url=CASINO_URL
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
        ["sportsbook", "sports book", "sports"]
    ):
        return (
            "⚽ <b>Betroxy Sportsbook</b>\n\n"
            "Open Sportsbook directly inside Telegram.",
            InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⚽ Open Sportsbook",
                        url=SPORTSBOOK_URL
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
        ["exchange"]
    ):
        return (
            "🔄 <b>Betroxy Exchange</b>\n\n"
            "Open Exchange directly inside Telegram.",
            InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔄 Open Exchange",
                        url=EXCHANGE_URL
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
        ["popular", "popular games"]
    ):
        return (
            "🔥 <b>Popular Games</b>\n\n"
            "Open Betroxy Popular Games directly inside Telegram.",
            InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔥 Open Popular Games",
                        url=POPULAR_GAMES_URL
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
        ["crash", "crash games"]
    ):
        return (
            "🚀 <b>Crash Games</b>\n\n"
            "Open Betroxy Crash Games directly inside Telegram.",
            InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🚀 Open Crash Games",
                        url=CRASH_GAMES_URL
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
        ]
    ):
        return (
            PROMOTION_TEXT,
            promotions_menu()
        )

    if contains_any(
        text,
        ["vip", "vip club"]
    ):
        return (
            "👑 <b>Betroxy VIP Club</b>\n\n"
            "Explore VIP rewards and benefits.",
            InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "👑 Open VIP Club",
                        url=VIP_URL
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
            "customer care",
            "whatsapp",
            "telegram support",
            "problem",
            "issue",
        ]
    ):
        return (
            SUPPORT_TEXT,
            support_menu()
        )

    if contains_any(
        text,
        [
            "responsible",
            "responsible gambling",
            "responsible play",
        ]
    ):
        return (
            "🛡 <b>Responsible Play</b>\n\n"
            "Review Betroxy responsible gambling information.",
            InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🛡 Responsible Gambling",
                        url=RESPONSIBLE_URL
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
        ["play", "betroxy"]
    ):
        return (
            "🎮 <b>Play on Betroxy</b>\n\n"
            "Open the Betroxy play bot below.",
            InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🎮 Open @BetroxyBot",
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

    return (
        FALLBACK_TEXT,
        main_menu()
    )


async def chat_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not update.message or not update.message.text:
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


# ============================================================
# LOGGING / STARTUP
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
        "%s official bot starting...",
        BRAND_NAME
    )

    app.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
