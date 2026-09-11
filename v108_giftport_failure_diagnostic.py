import threading
import time

import bot
import v107_mobile_prompt_state_fix as v107

v105 = v107.v105
v104 = v107.v104
v102 = v105.v102
v101 = v105.v101
v97 = v105.v97
v96 = v105.v96
v93 = v105.v93
v88 = v105.v88
v85 = v105.v85
v83 = v105.v83

TEST_ORDER_ID = "BTRX_1"


def _diagnose_phonepe_failure():
    award = None
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id,status,provider_order_id,brand_code,amount,error_detail,
                       provider_message,issue_attempts,last_issue_attempt_at
                FROM reward_awards
                WHERE provider_order_id=%s OR id=1
                ORDER BY CASE WHEN provider_order_id=%s THEN 0 ELSE 1 END, id
                LIMIT 1
                """,
                (TEST_ORDER_ID, TEST_ORDER_ID),
            )
            award = cur.fetchone() or {}

    state = v97._provider_state() or {}
    bot.logger.warning(
        "V108_GIFTPORT_AWARD_DIAG id=%s status=%s order=%s operator=%s amount=%s attempts=%s error=%s provider_message=%s",
        award.get("id"), award.get("status"), award.get("provider_order_id"),
        award.get("brand_code"), award.get("amount"), award.get("issue_attempts"),
        str(award.get("error_detail") or "")[:500],
        str(award.get("provider_message") or "")[:300],
    )
    bot.logger.warning(
        "V108_GIFTPORT_STATE last_error=%s balance=%s currency=%s",
        str(state.get("last_error") or "")[:500], state.get("wallet_balance"), state.get("currency"),
    )

    # Read-only provider check. This does NOT buy or retry a voucher.
    ok, data = v97._giftport_post("status", {"order_id": TEST_ORDER_ID})
    safe = {
        "status": data.get("status"),
        "message": data.get("message"),
        "order_id": data.get("order_id"),
        "transaction_id": data.get("transaction_id"),
        "amount": data.get("amount"),
        "has_redeem_code": bool(data.get("redeem_code")),
        "has_card_no": bool(data.get("card_no")),
    }
    bot.logger.warning("V108_GIFTPORT_STATUS_CHECK ok=%s data=%s", ok, safe)


bot.logger.warning("V108_GIFTPORT_FAILURE_DIAGNOSTIC active=on buy_retry=off status_check=readonly")


if __name__ == "__main__":
    _diagnose_phonepe_failure()
    v107._db_prompt_state_selftest()
    v105._startup_selftest()
    v97._startup_diagnostic()
    v96._startup_diagnostic()
    v93._startup_pdf_diagnostic()
    v101._phonepe_probe()
    v104._startup_mobile_diagnostic()
    v88.v63.apply_signup_cta()
    v85._enable_smart_reply_without_reset()
    threading.Thread(target=v83._worker_loop, name="betroxy-engagement-worker", daemon=True).start()
    bot.logger.warning("V108 polling handover delay=12s")
    time.sleep(12)
    bot.main()
