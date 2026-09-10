import threading
import time

import bot
import v97_giftport_live_adapter as v97

v96 = v97.v96
v93 = v97.v93
v89 = v97.v89
v88 = v97.v88
v85 = v97.v85
v83 = v97.v83

_old_start = bot.start


def _validate_reward_product(operator_code, amount):
    row = v97._catalogue_row(operator_code)
    if not row and v97._giftport_ready():
        v97._sync_catalogue()
        row = v97._catalogue_row(operator_code)
    if not row:
        return False, f"Giftport operator {operator_code} is not available in the synced catalogue", None

    # Giftport marks products with variable=Yes when arbitrary supported values can
    # be issued. For those products the denomination list is informative rather
    # than an exclusive allow-list, so do not reject values such as ₹250 merely
    # because they are absent from the sample denomination string.
    if bool(row.get("variable")):
        return True, "ok", row

    denoms = v97._parse_denominations(row.get("denominations"))
    if v97.GIFTPORT_STRICT_DENOMINATIONS and denoms and float(amount) not in {float(x) for x in denoms}:
        options = ", ".join(
            f"₹{int(x):,}" if float(x).is_integer() else f"₹{x}"
            for x in sorted(denoms)
        )
        return False, f"₹{amount:,} is not listed for {row.get('brand_name')}. Available: {options}", row
    return True, "ok", row


async def v98_start(update, context):
    result = await _old_start(update, context)
    user = update.effective_user
    msg = update.effective_message
    args = list(getattr(context, "args", []) or [])
    payload = str(args[0]).lower().strip() if args else ""
    if user and msg and payload == "rewards":
        try:
            await msg.reply_text(
                v89._my_rewards_text(user.id),
                parse_mode=bot.ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except Exception:
            bot.logger.exception("V98_REWARDS_DEEPLINK_FAILED uid=%s", user.id)
    return result


v97._validate_reward_product = _validate_reward_product
bot.start = v98_start

bot.logger.warning(
    "V98_GIFTPORT_REFINEMENT active=on variable_denomination=allowed fixed_denomination=strict rewards_deeplink=on"
)


if __name__ == "__main__":
    v97._startup_diagnostic()
    v96._startup_diagnostic()
    v93._startup_pdf_diagnostic()
    v88.v63.apply_signup_cta()
    v85._enable_smart_reply_without_reset()
    threading.Thread(target=v83._worker_loop, name="betroxy-engagement-worker", daemon=True).start()
    bot.logger.warning("V98 polling handover delay=12s")
    time.sleep(12)
    bot.main()
