import threading
import time

import bot
import v107_mobile_prompt_state_fix as v107

v105 = v107.v105
v104 = v107.v104
v103 = v105.v103
v102 = v105.v102
v101 = v105.v101
v97 = v105.v97
v96 = v105.v96
v93 = v105.v93
v89 = v105.v89
v88 = v105.v88
v85 = v105.v85
v83 = v105.v83

TEST_USERNAME = "mohit_97saxena"
TEST_OPERATOR = "GPPHP"
TEST_AMOUNT = 50
TEST_PERIOD_KEY = "manual_test:phonepe_b2b:inr50:v109"


def _find_test_user():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT telegram_user_id, telegram_username, reachable_bot
                FROM intelligence_leads
                WHERE LOWER(telegram_username)=LOWER(%s)
                ORDER BY last_seen_at DESC NULLS LAST
                LIMIT 1
                """,
                (TEST_USERNAME,),
            )
            return cur.fetchone()


def _get_or_create_award(uid):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO reward_awards(
                    telegram_user_id,reward_type,period_key,rank,points,amount,currency,
                    brand_code,provider,status
                ) VALUES (%s,'manual_test',%s,1,0,%s,'INR',%s,'giftport','queued')
                ON CONFLICT(reward_type,period_key,rank) DO NOTHING
                """,
                (int(uid), TEST_PERIOD_KEY, TEST_AMOUNT, TEST_OPERATOR),
            )
            cur.execute(
                """
                SELECT * FROM reward_awards
                WHERE reward_type='manual_test' AND period_key=%s AND rank=1
                LIMIT 1
                """,
                (TEST_PERIOD_KEY,),
            )
            row = cur.fetchone()
        conn.commit()
    return row


def _refresh_award(award_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM reward_awards WHERE id=%s", (int(award_id),))
            return cur.fetchone()


def _run_approved_live_test():
    lead = _find_test_user()
    if not lead:
        bot.logger.warning("V109_PHONEPE50 result=blocked reason=test_user_not_found")
        return
    if not lead.get("reachable_bot"):
        bot.logger.warning("V109_PHONEPE50 result=blocked reason=test_user_not_reachable uid=%s", lead.get("telegram_user_id"))
        return

    uid = int(lead["telegram_user_id"])
    mobile = v97._giftport_mobile(uid)
    if not mobile:
        bot.logger.warning("V109_PHONEPE50 result=blocked reason=verified_indian_mobile_missing uid=%s", uid)
        return

    valid, reason, product = v97._validate_reward_product(TEST_OPERATOR, TEST_AMOUNT)
    if not valid:
        bot.logger.warning("V109_PHONEPE50 result=blocked reason=product_validation_failed detail=%s", reason)
        return

    award = _get_or_create_award(uid)
    if not award:
        bot.logger.warning("V109_PHONEPE50 result=blocked reason=award_record_failed")
        return

    status = str(award.get("status") or "")
    attempts = int(award.get("issue_attempts") or 0)

    # Safe restart behavior: never make a second purchase attempt automatically.
    if status == "delivered":
        bot.logger.warning("V109_PHONEPE50 result=already_delivered award=%s order=%s", award.get("id"), award.get("provider_order_id"))
        return
    if status in {"issued", "delivery_pending"}:
        delivered = bool(v97._deliver_award(award["id"]))
        final = _refresh_award(award["id"]) or award
        bot.logger.warning(
            "V109_PHONEPE50 result=%s award=%s status=%s order=%s tx=%s",
            delivered, award.get("id"), final.get("status"), final.get("provider_order_id"), final.get("provider_transaction_id"),
        )
        return
    if status == "provider_unknown":
        reconciled = bool(v97._reconcile_unknown(award))
        final = _refresh_award(award["id"]) or award
        bot.logger.warning(
            "V109_PHONEPE50 result=%s award=%s status=%s order=%s tx=%s error=%s",
            reconciled, award.get("id"), final.get("status"), final.get("provider_order_id"), final.get("provider_transaction_id"), str(final.get("error_detail") or "")[:300],
        )
        return
    if attempts > 0:
        bot.logger.warning(
            "V109_PHONEPE50 result=no_retry award=%s status=%s order=%s attempts=%s error=%s",
            award.get("id"), status, award.get("provider_order_id"), attempts, str(award.get("error_detail") or "")[:300],
        )
        return

    bot.logger.warning(
        "V109_PHONEPE50 live_test_start award=%s uid=%s operator=%s amount=%s brand=%s",
        award.get("id"), uid, TEST_OPERATOR, TEST_AMOUNT, str((product or {}).get("brand_name") or ""),
    )
    result = bool(v97._issue_award(award))
    final = _refresh_award(award["id"]) or award
    bot.logger.warning(
        "V109_PHONEPE50 result=%s award=%s status=%s order=%s tx=%s has_code=%s error=%s provider_message=%s",
        result,
        award.get("id"),
        final.get("status"),
        final.get("provider_order_id"),
        final.get("provider_transaction_id"),
        bool(final.get("voucher_code") or final.get("voucher_url")),
        str(final.get("error_detail") or "")[:300],
        str(final.get("provider_message") or "")[:300],
    )


bot.logger.warning(
    "V109_PHONEPE_B2B_50_TEST approved=on one_time=on operator=GPPHP amount=50 target=@mohit_97saxena duplicate_purchase_guard=on"
)


if __name__ == "__main__":
    v107._db_prompt_state_selftest()
    v105._startup_selftest()
    v97._startup_diagnostic()
    v96._startup_diagnostic()
    v93._startup_pdf_diagnostic()
    v101._phonepe_probe()
    v104._startup_mobile_diagnostic()
    _run_approved_live_test()
    v88.v63.apply_signup_cta()
    v85._enable_smart_reply_without_reset()
    threading.Thread(target=v83._worker_loop, name="betroxy-engagement-worker", daemon=True).start()
    bot.logger.warning("V109 polling handover delay=12s")
    time.sleep(12)
    bot.main()
