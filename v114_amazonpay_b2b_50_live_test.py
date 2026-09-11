import threading
import time

import bot
import v113_text_quiz_ux as v113
import v87_single_optin_reminder as v87

v110 = v113.v110
v111 = v113.v111
v97 = v110.v97
v96 = v110.v96
v93 = v110.v93
v101 = v110.v101
v104 = v110.v104
v105 = v110.v105
v88 = v110.v88
v85 = v110.v85
v83 = v110.v83

TEST_USERNAME = "mohit_97saxena"
TEST_OPERATOR = "GPAPGV"
TEST_AMOUNT = 50
TEST_PERIOD_KEY = "manual_test:amazonpay_b2b:inr50:v114"
REMINDER_TEST_KEY = "optin_reminder_test_v114_restore"


def _find_test_user():
    return v110._find_test_user()


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
        bot.logger.warning("V114_AMAZONPAY50 result=blocked reason=test_user_not_found")
        return
    if not lead.get("reachable_bot"):
        bot.logger.warning("V114_AMAZONPAY50 result=blocked reason=test_user_not_reachable uid=%s", lead.get("telegram_user_id"))
        return

    uid = int(lead["telegram_user_id"])
    mobile = v97._giftport_mobile(uid)
    if not mobile:
        bot.logger.warning("V114_AMAZONPAY50 result=blocked reason=verified_indian_mobile_missing uid=%s", uid)
        return

    valid, reason, product = v97._validate_reward_product(TEST_OPERATOR, TEST_AMOUNT)
    if not valid:
        bot.logger.warning("V114_AMAZONPAY50 result=blocked reason=product_validation_failed detail=%s", reason)
        return

    award = _get_or_create_award(uid)
    if not award:
        bot.logger.warning("V114_AMAZONPAY50 result=blocked reason=award_record_failed")
        return

    status = str(award.get("status") or "")
    attempts = int(award.get("issue_attempts") or 0)

    if status == "delivered":
        bot.logger.warning("V114_AMAZONPAY50 result=already_delivered award=%s order=%s", award.get("id"), award.get("provider_order_id"))
        return
    if status in {"issued", "delivery_pending"}:
        delivered = bool(v97._deliver_award(award["id"]))
        final = _refresh_award(award["id"]) or award
        bot.logger.warning(
            "V114_AMAZONPAY50 result=%s award=%s status=%s order=%s tx=%s",
            delivered, award.get("id"), final.get("status"), final.get("provider_order_id"), final.get("provider_transaction_id"),
        )
        return
    if status == "provider_unknown":
        reconciled = bool(v97._reconcile_unknown(award))
        final = _refresh_award(award["id"]) or award
        bot.logger.warning(
            "V114_AMAZONPAY50 result=%s award=%s status=%s order=%s tx=%s error=%s",
            reconciled, award.get("id"), final.get("status"), final.get("provider_order_id"), final.get("provider_transaction_id"), str(final.get("error_detail") or "")[:300],
        )
        return
    if attempts > 0:
        bot.logger.warning(
            "V114_AMAZONPAY50 result=no_retry award=%s status=%s order=%s attempts=%s error=%s",
            award.get("id"), status, award.get("provider_order_id"), attempts, str(award.get("error_detail") or "")[:300],
        )
        return

    bot.logger.warning(
        "V114_AMAZONPAY50 live_test_start award=%s uid=%s operator=%s amount=%s brand=%s mobile=%s",
        award.get("id"), uid, TEST_OPERATOR, TEST_AMOUNT, str((product or {}).get("brand_name") or ""), mobile,
    )
    result = bool(v97._issue_award(award))
    final = _refresh_award(award["id"]) or award
    bot.logger.warning(
        "V114_AMAZONPAY50 result=%s award=%s status=%s order=%s tx=%s has_code=%s error=%s provider_message=%s",
        result,
        award.get("id"),
        final.get("status"),
        final.get("provider_order_id"),
        final.get("provider_transaction_id"),
        bool(final.get("voucher_code") or final.get("voucher_url")),
        str(final.get("error_detail") or "")[:300],
        str(final.get("provider_message") or "")[:300],
    )


def _send_controlled_reminder_test():
    time.sleep(18)
    try:
        lead = _find_test_user()
        if not lead or not lead.get("reachable_bot"):
            bot.logger.warning("V114_REMINDER_TEST result=blocked reason=test_user_unavailable")
            return
        uid = int(lead["telegram_user_id"])
        keyboard = [
            [{"text": "🏆 Join Sports Challenge", "url": v83.PREFERENCES_DEEPLINK}],
            [{"text": "🔔 Choose My Updates", "url": v83.PREFERENCES_DEEPLINK}],
        ]
        text = (
            "🏆 <b>Want to join the BETROXY Sports Challenge?</b>\n\n"
            "Choose only the updates you want — Sports, Promotions or Quiz & Rewards. "
            "Quiz participants can earn points and appear on the weekly leaderboard.\n\n"
            "No selection means no recurring updates, and you can stop anytime."
        )
        sent = bool(v83._send_claimed(uid, "optin", REMINDER_TEST_KEY, text, keyboard))
        bot.logger.warning("V114_REMINDER_TEST sent=%s uid=%s", sent, uid)
    except Exception:
        bot.logger.exception("V114_REMINDER_TEST_FAILED")


bot.logger.warning(
    "V114_AMAZON_PAY_B2B_50_TEST approved=on one_time=on operator=GPAPGV amount=50 target=@mohit_97saxena duplicate_purchase_guard=on text_quiz=v113 v87_reminder=on"
)


if __name__ == "__main__":
    v110._ensure_schema()
    v110._selftest()
    v111._compatibility_selftest()
    v97._startup_diagnostic()
    v96._startup_diagnostic()
    v93._startup_pdf_diagnostic()
    v101._phonepe_probe()
    v104._startup_mobile_diagnostic()
    _run_approved_live_test()
    v88.v63.apply_signup_cta()
    v85._enable_smart_reply_without_reset()
    threading.Thread(target=v110._public_worker, name="betroxy-v110-public-worker", daemon=True).start()
    threading.Thread(target=v83._worker_loop, name="betroxy-engagement-worker", daemon=True).start()
    threading.Thread(target=_send_controlled_reminder_test, name="betroxy-reminder-test", daemon=True).start()
    bot.logger.warning("V114 polling handover delay=12s")
    time.sleep(12)
    bot.main()
