import bot
import v56_styled_public_buttons_bootstrap as v56
import v53_attractive_customer_experience_bootstrap as v53
import v54_theme_banner_open_app_bootstrap as v54

# V57 - reliable banner delivery using a real repository image URL.
# Avoids the corrupted embedded base64 JPEG used by V54/V55.

BANNER_URL = "https://raw.githubusercontent.com/mohit171993/betroxy-telegram-bot/main/design_preview.png"


async def v57_start(update, context):
    # Preserve referral / claim deep-link behavior.
    if getattr(context, "args", None):
        return await v54._original_start(update, context)

    msg = update.effective_message
    if not msg:
        return

    try:
        await msg.reply_photo(
            photo=BANNER_URL,
            caption="👑 <b>BETROXY</b> — Play • Win • Enjoy",
            parse_mode=bot.ParseMode.HTML,
        )
        bot.logger.warning("V57_BANNER_SENT url=%s", BANNER_URL)
    except Exception as exc:
        bot.logger.exception("V57 banner send failed: %s", exc)

    await msg.reply_text(
        v53.v53_public_welcome_text(),
        parse_mode=bot.ParseMode.HTML,
        reply_markup=v53.v53_public_menu(update.effective_user.id if update.effective_user else None),
        disable_web_page_preview=True,
    )


bot.start = v57_start
bot.logger.warning("V57_RELIABLE_BANNER active=on")


if __name__ == "__main__":
    bot.main()
