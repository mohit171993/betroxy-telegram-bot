from datetime import datetime, timezone, timedelta

import bot
import v35_test_bootstrap  # applies current production patches without starting bot.main()

_ACTIVE_RUN_STATES = {"requested", "running", "apify_fallback"}
_STALE_AFTER = timedelta(minutes=20)
_original_callback_handler = bot.callback_handler


def _is_stale(control):
    requested_at = control.get("requested_at")
    if not requested_at:
        return True
    if requested_at.tzinfo is None:
        requested_at = requested_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - requested_at > _STALE_AFTER


def _reset_stale_control():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE verifier_control
                SET status='idle',
                    run_token=NULL,
                    claimed_at=NULL,
                    completed_at=NOW(),
                    result_summary='Previous run interrupted/stale; control auto-reset safely'
                WHERE id=1
                """
            )
        conn.commit()


async def guarded_callback_handler(update, context):
    q = update.callback_query
    data = q.data if q else ""

    if data == "verify_request_local_run":
        control = bot.get_local_verifier_control() or {}
        state = str(control.get("status") or "").strip().lower()
        if state in _ACTIVE_RUN_STATES:
            if _is_stale(control):
                _reset_stale_control()
                bot.logger.warning("SMART_CHECK_STALE_RUN_AUTO_RESET previous_state=%s", state)
            else:
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
bot.logger.warning("SMART_CHECK_DUPLICATE_GUARD_ACTIVE stale_after_minutes=20")


if __name__ == "__main__":
    bot.main()
