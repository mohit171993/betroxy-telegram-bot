import time

import bot
import v78_business_officialbot_actions as v78

v63 = v78.v63
_previous_callback_handler = bot.callback_handler

HOW_IT_WORKS_POPUP = (
    "BETROXY gives you quick access to games, account services, transactions, "
    "offers and support. Tap Open App to launch the Mini App, choose a section, "
    "and use Help & Support whenever needed."
)

# Telegram callback alert text is intentionally kept short for native popup display.
assert len(HOW_IT_WORKS_POPUP) <= 200


async def v79_callback_handler(update, context):
    q = update.callback_query
    data = (q.data or "") if q else ""

    # Apply the same compact popup UX in both the Telegram Business auto-reply
    # menu and @BetroxyOfficialBot public menu.
    if q and data in {"bizux_how_it_works", "ux_how_it_works"}:
        await q.answer(text=HOW_IT_WORKS_POPUP, show_alert=True)
        return

    return await _previous_callback_handler(update, context)


bot.callback_handler = v79_callback_handler

bot.logger.warning(
    "V79_HOW_IT_WORKS_POPUP active=on business=on officialbot=on show_alert=on"
)

if __name__ == "__main__":
    v63.apply_signup_cta()
    v63.enable_business_smart_auto_reply()
    bot.logger.warning("V79 polling handover delay=12s")
    time.sleep(12)
    bot.main()
