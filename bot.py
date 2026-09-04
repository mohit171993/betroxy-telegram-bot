import os
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBSITE_URL = os.getenv("WEBSITE_URL", "https://betroxy.com")
SUPPORT_URL = os.getenv("SUPPORT_URL", "https://betroxy.com")
TERMS_URL = os.getenv("TERMS_URL", "https://betroxy.com")
PRIVACY_URL = os.getenv("PRIVACY_URL", "https://betroxy.com")

WELCOME_TEXT = (
    "Welcome to Betroxy.\n\n"
    "Explore the platform, get the latest updates, or contact support."
)

def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Visit Betroxy", url=WEBSITE_URL)],
        [InlineKeyboardButton("🆕 Explore", callback_data="explore"),
         InlineKeyboardButton("📣 Updates", callback_data="updates")],
        [InlineKeyboardButton("💬 Support", url=SUPPORT_URL)],
        [InlineKeyboardButton("📄 Terms", url=TERMS_URL),
         InlineKeyboardButton("🔒 Privacy", url=PRIVACY_URL)],
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME_TEXT, reply_markup=main_menu())

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Available commands:\n"
        "/start - Open the main menu\n"
        "/website - Open Betroxy\n"
        "/support - Contact support\n"
        "/help - Show this help message"
    )

async def website(update: Update, context: ContextTypes.DEFAULT_TYPE):
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("Open Betroxy", url=WEBSITE_URL)]
    ])
    await update.message.reply_text(
        "Tap below to visit Betroxy:",
        reply_markup=markup
    )

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("Contact Support", url=SUPPORT_URL)]
    ])
    await update.message.reply_text(
        "Need help? Use the button below:",
        reply_markup=markup
    )

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "explore":
        text = (
            "Explore Betroxy\n\n"
            "Sign up and explore our exclusive collection.\n"
            "Use the button below to continue."
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("Explore Betroxy", url=WEBSITE_URL)],
            [InlineKeyboardButton("⬅️ Back", callback_data="back")]
        ])
        await query.edit_message_text(text, reply_markup=markup)

    elif query.data == "updates":
        text = (
            "Betroxy Updates\n\n"
            "Stay connected for platform news, new features and announcements."
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🌐 Visit Website", url=WEBSITE_URL)],
            [InlineKeyboardButton("⬅️ Back", callback_data="back")]
        ])
        await query.edit_message_text(text, reply_markup=markup)

    elif query.data == "back":
        await query.edit_message_text(WELCOME_TEXT, reply_markup=main_menu())

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Add it to your environment variables.")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("website", website))
    app.add_handler(CommandHandler("support", support))
    app.add_handler(CallbackQueryHandler(callbacks))

    print("Betroxy Telegram bot is running...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
