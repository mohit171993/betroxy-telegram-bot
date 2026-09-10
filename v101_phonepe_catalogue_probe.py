import threading
import time

import bot
import v100_giftport_readonly_probe as v100

v99 = v100.v99
v98 = v100.v98
v97 = v100.v97
v96 = v100.v96
v93 = v100.v93
v89 = v100.v89
v88 = v100.v88
v85 = v100.v85
v83 = v100.v83


def _phonepe_probe():
    """Read-only lookup of PhonePe catalogue entries. Never calls Giftport buy."""
    try:
        ok, msg, count = v97._sync_catalogue()
        bot.logger.warning(
            "V101_PHONEPE_PROBE catalogue_ok=%s count=%s detail=%s",
            bool(ok), int(count or 0), str(msg)[:250],
        )
        if not ok:
            return
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT brand_name, operator_code, denominations, variable, is_active
                    FROM reward_provider_catalogue
                    WHERE is_active=TRUE
                      AND (brand_name ILIKE %s OR operator_code ILIKE %s)
                    ORDER BY brand_name, operator_code
                    """,
                    ("%PhonePe%", "%PHONE%"),
                )
                rows = cur.fetchall()
        if not rows:
            bot.logger.warning("V101_PHONEPE_PROBE no_matches")
            return
        for row in rows:
            bot.logger.warning(
                "V101_PHONEPE_ENTRY brand=%s operator=%s denominations=%s variable=%s",
                str(row.get("brand_name") or "")[:120],
                str(row.get("operator_code") or "")[:50],
                str(row.get("denominations") or "")[:300],
                bool(row.get("variable")),
            )
    except Exception as exc:
        bot.logger.warning("V101_PHONEPE_PROBE error=%s", str(exc)[:300])


if __name__ == "__main__":
    v97._startup_diagnostic()
    v96._startup_diagnostic()
    v93._startup_pdf_diagnostic()
    _phonepe_probe()
    v88.v63.apply_signup_cta()
    v85._enable_smart_reply_without_reset()
    threading.Thread(target=v83._worker_loop, name="betroxy-engagement-worker", daemon=True).start()
    bot.logger.warning("V101 polling handover delay=12s")
    time.sleep(12)
    bot.main()
