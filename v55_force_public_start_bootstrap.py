import base64
import io

import bot
import v54_theme_banner_open_app_bootstrap as v54
import v53_attractive_customer_experience_bootstrap as v53

# V55 - force the public /start experience to use the new themed banner/menu.
# Referral/claim deep links still delegate to the existing start handler so
# affiliate attribution and private access logic are preserved.

_existing_start = bot.start


async def v55_start(update, context):
    # Preserve all existing deep-link behavior.
    if getattr(context, "args", None):
        return await v54._original_start(update, context)

    msg = update.effective_message
    if not msg:
        return

    # Send themed hero banner first.
    try:
        banner = io.BytesIO(base64.b64decode(v54._BANNER_B64))
        banner.name = "betroxy_theme.jpg"
        await msg.reply_photo(
            photo=banner,
            caption=(
                "👑 <b>WELCOME TO BETROXY</b>\n"
                "Casino • Sportsbook • Exchange • Offers"
            ),
            parse_mode=bot.ParseMode.HTML,
        )
    except Exception:
        bot.logger.exception("V55 public banner send failed")

    # Force the redesigned public menu instead of relying on older handler globals.
    await msg.reply_text(
        v53.v53_public_welcome_text(),
        parse_mode=bot.ParseMode.HTML,
        reply_markup=v53.v53_public_menu(update.effective_user.id if update.effective_user else None),
        disable_web_page_preview=True,
    )


bot.start = v55_start
bot.logger.warning("V55_FORCE_PUBLIC_START active=on")


if __name__ == "__main__":
    bot.main()
