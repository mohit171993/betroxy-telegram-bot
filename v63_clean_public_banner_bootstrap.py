import os

import bot
import v62_ai_admin_assistant_bootstrap as v62

BANNER_PATH = os.path.join(os.path.dirname(__file__), "betroxy_public_banner.jpg")

async def v63_start(update, context):
    if getattr(context, "args", None):
        return await v62.v61.v60.v59.v59_start(update, context)
    msg = update.effective_message
    if not msg:
        return
    try:
        with open(BANNER_PATH, "rb") as banner:
            await msg.reply_photo(
                photo=banner,
                caption="✨ <b>BETROXY</b> • Official Access & Support",
                parse_mode=bot.ParseMode.HTML,
            )
    except Exception as exc:
        bot.logger.exception("V63 approved public banner failed: %s", exc)
    await msg.reply_text(
        v62.v61.v60.v59.v53.v53_public_welcome_text(),
        parse_mode=bot.ParseMode.HTML,
        reply_markup=v62.v61.v60.v59.v53.v53_public_menu(update.effective_user.id if update.effective_user else None),
        disable_web_page_preview=True,
    )

bot.start = v63_start
bot.logger.warning("V63_APPROVED_PUBLIC_BANNER active=on asset=betroxy_public_banner.jpg")

if __name__ == "__main__":
    bot.main()
