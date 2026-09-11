"""Keep /start and Telegram Business first-reply menus aligned."""
import bot

MINIAPP_DEEPLINK = "https://t.me/BetroxyBot/sportsbook?startapp=sportsbook"
OFFICIAL_BOT = "BetroxyOfficialBot"


def _bot_deeplink(payload):
    return f"https://t.me/{OFFICIAL_BOT}?start={payload}"


def install(compact_menu, business_module):
    """Use the six-action customer navigation in both customer entry paths.

    Telegram Business replies use URL/deep-link equivalents, while every action
    resolves to the same working destination as the direct-bot customer menu.

    The dailyquiz deep link is conversion-critical and deliberately bypasses the
    generic Welcome menu: registered users go straight to today's quiz; users
    missing mobile/consent go straight to the required registration step.
    """
    v75 = business_module
    biz51 = v75.biz51
    previous_payload = v75._business_reply_payload
    previous_start = bot.start

    def business_customer_menu(styled=True):
        return bot.InlineKeyboardMarkup([
            [bot.InlineKeyboardButton("🚀 Open BETROXY", url=MINIAPP_DEEPLINK)],
            [bot.InlineKeyboardButton("🏆 Daily Quiz", url=_bot_deeplink("dailyquiz"))],
            [bot.InlineKeyboardButton("🎁 My Rewards", url=_bot_deeplink("rewards"))],
            [bot.InlineKeyboardButton("👤 My Account", url=_bot_deeplink("account"))],
            [bot.InlineKeyboardButton("📢 Updates & Promotions", url=_bot_deeplink("updates"))],
            [bot.InlineKeyboardButton("🎧 Help & Support", url=_bot_deeplink("support"))],
        ])

    def unified_business_payload(intent, first_reply=False):
        text, keyboard, stage = previous_payload(intent, first_reply=first_reply)
        if first_reply or intent in {"greeting", "general"}:
            keyboard = business_customer_menu(styled=True)
        return text, keyboard, stage

    async def direct_customer_start(update, context):
        args = list(getattr(context, "args", []) or [])
        payload = str(args[0]).strip().lower() if args else ""
        if payload != "dailyquiz":
            return await previous_start(update, context)

        msg = getattr(update, "effective_message", None)
        user = getattr(update, "effective_user", None)
        if not msg or not user:
            return await previous_start(update, context)

        uid = int(user.id)
        username = getattr(user, "username", None)
        try:
            # Preserve the engagement touch without rendering the generic menu.
            try:
                compact_menu.v83._touch_user(uid, "daily_quiz_deeplink")
            except Exception:
                pass

            campaign = compact_menu.daily_schedule._today_campaign_windowed(test_mode=False)
            if not campaign or str(campaign.get("status") or "") != "open":
                await msg.reply_text("⏰ Today's quiz is closed. The next daily quiz will open tomorrow.")
                bot.logger.warning("DAILYQUIZ_DEEPLINK uid=%s route=closed welcome_menu=off", uid)
                return

            campaign_id = int(campaign["id"])
            if not compact_menu.v110._mobile(uid):
                await compact_menu.v110._registration_prompt(msg, uid, campaign_id)
                bot.logger.warning("DAILYQUIZ_DEEPLINK uid=%s route=mobile_registration welcome_menu=off", uid)
                return

            if not compact_menu.v110._has_consent(uid):
                await compact_menu.v110._consent_prompt(msg, uid, campaign_id)
                bot.logger.warning("DAILYQUIZ_DEEPLINK uid=%s route=consent welcome_menu=off", uid)
                return

            await compact_menu.v110._start_quiz(uid, username, campaign, source="dailyquiz_deeplink")
            bot.logger.warning("DAILYQUIZ_DEEPLINK uid=%s route=quiz welcome_menu=off", uid)
            return
        except Exception:
            bot.logger.exception("DAILYQUIZ_DEEPLINK_FAILED uid=%s", uid)
            await msg.reply_text("⚠️ I couldn't open today's quiz just now. Please try again in a moment.")
            return

    # Patch the functions resolved by the live Business sender.
    v75._business_menu = business_customer_menu
    v75._business_reply_payload = unified_business_payload
    biz51._reply_payload = unified_business_payload

    # Patch the live /start route after the compact menu has been installed.
    bot.start = direct_customer_start

    bot.logger.warning(
        "UNIFIED_CUSTOMER_MENU active=on start=compact6 business_dm=compact6 "
        "business_open=telegram_miniapp business_account=bot_deeplink "
        "business_updates=bot_deeplink business_support=bot_deeplink "
        "dailyquiz_deeplink=direct_registration_or_quiz welcome_menu=off"
    )
    return business_customer_menu
