import threading
import time
from datetime import datetime, timezone

import bot
import v110_betroxy_daily_challenge as v110

v107 = v110.v107
v105 = v110.v105
v104 = v110.v104
v101 = v110.v101
v97 = v110.v97
v96 = v110.v96
v93 = v110.v93
v89 = v110.v89
v88 = v110.v88
v85 = v110.v85
v83 = v110.v83


def _compatibility_selftest():
    test_uid = -111000000001
    prompt_refresh = False
    recent_flow = False
    chat_route = bot.chat_handler is v110.v110_chat_handler
    callback_route = bot.callback_handler is v110.v110_callback_handler
    india_format = v105._is_indian_mobile("+919876543210") and not v105._is_indian_mobile("+971509430642")
    cleanup = False
    try:
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM user_contact_profiles WHERE telegram_user_id=%s", (test_uid,))
                cur.execute(
                    """
                    INSERT INTO user_contact_profiles(telegram_user_id,mobile_prompted_at,updated_at)
                    VALUES (%s,NOW()-INTERVAL '2 hours',NOW())
                    """,
                    (test_uid,),
                )
            conn.commit()
        v107._mark_mobile_prompted_fresh(test_uid)
        row = v89._mobile_row(test_uid) or {}
        p = row.get("mobile_prompted_at")
        if p is not None:
            if not getattr(p, "tzinfo", None):
                p = p.replace(tzinfo=timezone.utc)
            prompt_refresh = (datetime.now(timezone.utc) - p).total_seconds() < 15
        recent_flow = bool(v105._mobile_flow_recent(test_uid))
    finally:
        try:
            with bot.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM user_contact_profiles WHERE telegram_user_id=%s", (test_uid,))
                    cur.execute("DELETE FROM v110_quiz_sessions WHERE telegram_user_id=%s", (test_uid,))
                conn.commit()
            cleanup = True
        except Exception:
            bot.logger.exception("V111_SELFTEST cleanup_failed")

    bot.logger.warning(
        "V111_SELFTEST prompt_refresh=%s recent_flow=%s chat_route=%s callback_route=%s india_format=%s cleanup=%s",
        prompt_refresh, recent_flow, chat_route, callback_route, india_format, cleanup,
    )
    if not all([prompt_refresh, recent_flow, chat_route, callback_route, india_format, cleanup]):
        raise RuntimeError("V111 quiz compatibility self-test failed")


bot.logger.warning(
    "V111_QUIZ_TEST_STABLE active=on v110_routes=preserved legacy_route_assertions=skipped test_probe=@mohit_97saxena"
)


if __name__ == "__main__":
    v110._ensure_schema()
    v110._selftest()
    _compatibility_selftest()
    v97._startup_diagnostic()
    v96._startup_diagnostic()
    v93._startup_pdf_diagnostic()
    v101._phonepe_probe()
    v104._startup_mobile_diagnostic()
    v88.v63.apply_signup_cta()
    v85._enable_smart_reply_without_reset()
    threading.Thread(target=v83._worker_loop, name="betroxy-engagement-worker", daemon=True).start()
    threading.Thread(target=v110._public_worker, name="betroxy-v110-public-worker", daemon=True).start()
    threading.Thread(target=v110._send_test_probe_once, name="betroxy-v110-test-run", daemon=True).start()
    bot.logger.warning("V111 polling handover delay=12s")
    time.sleep(12)
    bot.main()
