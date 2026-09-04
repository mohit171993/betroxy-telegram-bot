# Betroxy Telegram Bot

A simple Telegram bot for Betroxy with:
- Welcome screen
- Website button
- Explore section
- Updates section
- Support button
- Terms and Privacy buttons

## 1. Create the Telegram bot

1. Open Telegram.
2. Search for `@BotFather`.
3. Send `/newbot`.
4. Choose your bot name.
5. Choose a username ending in `bot`.
6. BotFather will give you a bot token.

Keep the token private.

## 2. Install Python

Use Python 3.10 or newer.

## 3. Install dependencies

```bash
pip install -r requirements.txt
```

## 4. Configure

Create a `.env` file based on `.env.example`.

Example:

```env
BOT_TOKEN=your_token_here
WEBSITE_URL=https://betroxy.com
SUPPORT_URL=https://betroxy.com/support
TERMS_URL=https://betroxy.com/terms
PRIVACY_URL=https://betroxy.com/privacy
```

Then either export those variables in your server environment or load them using your hosting provider.

## 5. Run

```bash
python bot.py
```

## Recommended BotFather commands

Set these with `/setcommands`:

```text
start - Open main menu
website - Open Betroxy website
support - Contact support
help - Show help
```

## Deployment

This bot can run on a VPS, Railway, Render, Fly.io, or another Python host.

For production, use environment variables for the Telegram bot token and do not hard-code secrets in the code.
