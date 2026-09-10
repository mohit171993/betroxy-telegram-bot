import threading
import time

import bot
import v99_giftport_webhook as v99

v98 = v99.v98
v97 = v99.v97
v96 = v99.v96
v93 = v99.v93
v89 = v99.v89
v88 = v99.v88
v85 = v99.v85
v83 = v99.v83


def _giftport_readonly_probe():
    """Read-only startup connectivity check. Never calls the buy endpoint."""
    try:
        ok_cat, msg_cat, count = v97._sync_catalogue()
        bot.logger.warning(
            "V100_GIFTPORT_READONLY catalogue_ok=%s count=%s detail=%s",
            bool(ok_cat), int(count or 0), str(msg_cat)[:300],
        )
    except Exception as exc:
        bot.logger.warning("V100_GIFTPORT_READONLY catalogue_ok=false error=%s", str(exc)[:300])

    try:
        ok_bal, balance, currency = v97._get_balance()
        if ok_bal:
            bot.logger.warning(
                "V100_GIFTPORT_READONLY balance_ok=true balance=%s currency=%s",
                balance, currency,
            )
        else:
            bot.logger.warning(
                "V100_GIFTPORT_READONLY balance_ok=false detail=%s",
                str(currency)[:300],
            )
    except Exception as exc:
        bot.logger.warning("V100_GIFTPORT_READONLY balance_ok=false error=%s", str(exc)[:300])


if __name__ == "__main__":
    v97._startup_diagnostic()
    v96._startup_diagnostic()
    v93._startup_pdf_diagnostic()
    _giftport_readonly_probe()
    v88.v63.apply_signup_cta()
    v85._enable_smart_reply_without_reset()
    threading.Thread(target=v83._worker_loop, name="betroxy-engagement-worker", daemon=True).start()
    bot.logger.warning("V100 polling handover delay=12s")
    time.sleep(12)
    bot.main()
