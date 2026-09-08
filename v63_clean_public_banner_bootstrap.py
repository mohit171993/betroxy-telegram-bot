import os
import requests

import bot
import v62_ai_admin_assistant_bootstrap as v62

# Use the original high-resolution BETROXY banner already stored in the repo.
# This path is 1.6 MB and avoids the low-quality 117 KB test image.
BANNER_URL = (
    "https://raw.githubusercontent.com/"
    "mohit171993/betroxy-telegram-bot/main/oldwelcome_banner.jpg"
)


def run_banner_self_test_once():
    """Send one deployment-time HQ banner test to ADMIN_ID and log the result."""
    try:
        token = os.getenv("BOT_TOKEN", "").strip()
        admin_id = os.getenv("ADMIN_ID", "").strip()
        if not token or not admin_id:
            bot.logger.warning("V63 HQ banner self-test skipped: BOT_TOKEN/ADMIN_ID missing")
            return

        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            data={
                "chat_id": admin_id,
                "photo": BANNER_URL,
                "caption": "✅ BETROXY HQ banner deployment test",
            },
            timeout=30,
        )
        payload = response.json() if response.content else {}
        if response.ok and payload.get("ok"):
            bot.logger.warning("V63_HQ_BANNER_SELF_TEST SUCCESS")
        else:
            bot.logger.error(
                "V63_HQ_BANNER_SELF_TEST FAILED status=%s response=%s",
                response.status_code,
                payload,
            )
    except Exception as exc:
        bot.logger.exception("V63_HQ_BANNER_SELF_TEST ERROR: %s", exc)


async def v63_start(update, context):
    if getattr(context, "args", None):
        return await v62.v61.v60.v59.v59_start(update, context)

    msg = update.effective_message
    if not msg:
        return

    try:
        await msg.reply_photo(
            photo=BANNER_URL,
            caption="✨ <b>BETROXY</b> • Official Access & Support",
            parse_mode=bot.ParseMode.HTML,
        )
    except Exception as exc:
        bot.logger.exception("V63 HQ public banner failed: %s", exc)

    await msg.reply_text(
        v62.v61.v60.v59.v53.v53_public_welcome_text(),
        parse_mode=bot.ParseMode.HTML,
        reply_markup=v62.v61.v60.v59.v53.v53_public_menu(
            update.effective_user.id if update.effective_user else None
        ),
        disable_web_page_preview=True,
    )


bot.start = v63_start
bot.logger.warning("V63_PUBLIC_BANNER_FIX active=on source=oldwelcome_banner_hq")

if __name__ == "__main__":
    run_banner_self_test_once()
    bot.main()
