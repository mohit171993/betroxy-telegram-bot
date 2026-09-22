"""Text-only OfficialBot greeting; all existing product buttons remain intact.

No database schema changes, banner deletions, new polling or outbound campaigns.
Install after the historical welcome and Weekly Mega menu layers have loaded.
Specialized deep links, verification, admin/affiliate and Business paths delegate
unchanged. Only the normal private customer greeting and its text are replaced.
"""
from __future__ import annotations

import importlib
import logging

log = logging.getLogger(__name__)
WELCOME_TEXT = (
    "👋 <b>Welcome to BETROXY</b>\n\n"
    "Sportsbook • Daily &amp; Sunday Quizzes • Rewards\n\n"
    "Choose an option below 👇"
)


def install(bot, welcome, touch_user):
    """Preserve the final menu factory; do not copy/rebuild any buttons."""
    previous_start = bot.start
    if getattr(previous_start, "_betroxy_text_welcome", False) is True:
        return previous_start

    async def text_welcome_start(update, context):
        user = getattr(update, "effective_user", None)
        message = getattr(update, "effective_message", None)
        chat = getattr(update, "effective_chat", None)
        args = list(getattr(context, "args", None) or [])
        normal_private = bool(
            user and message and chat
            and str(chat.type) == "private"
            and int(chat.id) == int(user.id)
            and not getattr(message, "business_connection_id", None)
        )
        if not normal_private or args:
            return await previous_start(update, context)
        if bot.is_admin(user.id) or bot.find_agent_by_telegram_user_id(user.id):
            return await previous_start(update, context)

        # Same activity update as the existing Banner Manager welcome.
        try:
            touch_user(user.id, "banner_manager_start")
        except Exception as exc:
            log.warning("BTX_TEXT_WELCOME_TOUCH_SKIPPED type=%s", type(exc).__name__)

        # Do not fall back to an image-sending route on a Telegram send failure.
        await message.reply_text(
            WELCOME_TEXT,
            parse_mode="HTML",
            reply_markup=welcome.bot_menu(user.id),
            disable_web_page_preview=True,
        )
        log.warning("BTX_TEXT_WELCOME_SENT uid=%s banner=off buttons=existing", user.id)

    text_welcome_start._betroxy_text_welcome = True
    text_welcome_start._previous_start = previous_start
    # Existing home callbacks use this function; Business has its own function.
    welcome._bot_welcome_text = lambda: WELCOME_TEXT
    bot.start = text_welcome_start
    log.warning(
        "BTX_TEXT_WELCOME_READY normal_private_start=text_only existing_buttons=on "
        "verification=unchanged deep_links=unchanged business=unchanged "
        "channel_banners=unchanged stored_banners=retained"
    )
    return text_welcome_start


def prepare(production):
    """Delay presentation wiring until production.main has installed its menus."""
    bot = production.bot
    if getattr(bot.main, "_betroxy_text_welcome_prepared", False) is True:
        return
    previous_main = bot.main

    def main_with_text_welcome():
        welcome = importlib.import_module("welcome_experience_v2")
        install(bot, welcome, production.v83._touch_user)
        return previous_main()

    main_with_text_welcome._betroxy_text_welcome_prepared = True
    bot.main = main_with_text_welcome
