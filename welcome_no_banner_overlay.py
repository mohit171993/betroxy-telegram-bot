"""Additive OfficialBot welcome simplifier.

Removes only the normal /start welcome image from the direct OfficialBot.
It does not alter Telegram Business banners, quiz/channel banners, customer
buttons, deep links, verification, CRM, rewards, schedules or product routing.

Installed late (at bot.main handoff) so it wraps the final production /start
router after all historical welcome/banner layers have been composed.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)
WELCOME_TEXT = (
    "👋 <b>Welcome to BETROXY</b>\n\n"
    "Sportsbook • Daily &amp; Sunday Quizzes • Rewards\n\n"
    "Choose an option below 👇"
)


def prepare(production):
    bot = production.bot
    if getattr(bot, "_welcome_no_banner_overlay_prepared", False):
        return

    previous_main = bot.main

    def main_with_text_only_welcome():
        previous_start = bot.start
        if getattr(previous_start, "_welcome_no_banner_overlay", False) is not True:
            import welcome_experience_v2 as welcome

            # Shorten only direct-bot welcome/home text; leave Business copy intact.
            welcome._bot_welcome_text = lambda: WELCOME_TEXT

            async def text_only_start(update, context):
                args = list(getattr(context, "args", []) or [])
                payload = str(args[0]).strip().lower() if args else ""
                user = getattr(update, "effective_user", None)
                msg = getattr(update, "effective_message", None)

                # Keep every specialist/deep-link/admin/affiliate route exactly
                # on the existing final production router.
                if (
                    not msg
                    or payload
                    or (
                        user
                        and (
                            bot.is_admin(user.id)
                            or bot.find_agent_by_telegram_user_id(user.id)
                        )
                    )
                ):
                    return await previous_start(update, context)

                try:
                    # Preserve the same welcome analytics touch used by the
                    # authoritative menu, without introducing any new event.
                    try:
                        import clean_customer_menu

                        if user:
                            clean_customer_menu.v83._touch_user(
                                user.id, "welcome_v2"
                            )
                    except Exception:
                        pass

                    await msg.reply_text(
                        welcome._bot_welcome_text(),
                        parse_mode=bot.ParseMode.HTML,
                        reply_markup=welcome.bot_menu(user.id if user else None),
                        disable_web_page_preview=True,
                    )
                    bot.logger.warning(
                        "WELCOME_NO_BANNER_START_SENT uid=%s "
                        "officialbot_banner=off menu=unchanged",
                        getattr(user, "id", None),
                    )
                    return
                except Exception:
                    # Text-only presentation must never make /start unavailable.
                    bot.logger.exception(
                        "WELCOME_NO_BANNER_START_FAILED uid=%s fallback=existing",
                        getattr(user, "id", None),
                    )
                    return await previous_start(update, context)

            text_only_start._welcome_no_banner_overlay = True
            text_only_start._previous_start = previous_start
            bot.start = text_only_start

        bot.logger.warning(
            "WELCOME_NO_BANNER_OVERLAY active=on officialbot_start_banner=off "
            "business_banner=unchanged channel_banners=unchanged "
            "menu=unchanged deep_links=unchanged welcome_text=compact"
        )
        return previous_main()

    bot.main = main_with_text_only_welcome
    bot._welcome_no_banner_overlay_prepared = True
