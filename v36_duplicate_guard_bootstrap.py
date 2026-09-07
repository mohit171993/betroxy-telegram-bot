import bot
import v35_test_bootstrap  # applies current production patches without starting bot.main()

_ACTIVE_RUN_STATES = {"requested", "running", "apify_fallback"}
_original_callback_handler = bot.callback_handler


async def guarded_callback_handler(update, context):
    q = update.callback_query
    data = q.data if q else ""

    if data == "verify_request_local_run":
        control = bot.get_local_verifier_control() or {}
        state = str(control.get("status") or "").strip().lower()
        if state in _ACTIVE_RUN_STATES:
            await q.answer()
            await q.message.reply_text(
                "⏳ <b>Smart Check already running</b>\n\n"
                "Please wait for the current Browser → Apify cycle to finish. "
                "A second run was not started.",
                parse_mode=bot.ParseMode.HTML,
                reply_markup=bot.campaign_menu(),
            )
            bot.logger.warning("SMART_CHECK_DUPLICATE_BLOCKED state=%s", state)
            return

    return await _original_callback_handler(update, context)


bot.callback_handler = guarded_callback_handler
bot.logger.warning("SMART_CHECK_DUPLICATE_GUARD_ACTIVE")


if __name__ == "__main__":
    bot.main()
