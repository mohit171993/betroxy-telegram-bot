import threading
import time
from datetime import datetime, timezone

import bot
import v105_indian_mobile_rewards as v105

v104 = v105.v104
v102 = v105.v102
v101 = v105.v101
v97 = v105.v97
v96 = v105.v96
v93 = v105.v93
v89 = v105.v89
v88 = v105.v88
v85 = v105.v85
v83 = v105.v83

# V89 used COALESCE(existing_prompt_time, NOW()), which meant the first-ever
# prompt timestamp could remain forever. After 30 minutes, correctly typed +91
# numbers were therefore treated as normal chat and fell through to "Welcome back".
# Always refresh the prompt timestamp whenever the mobile-entry screen is opened.
def _mark_mobile_prompted_fresh(uid):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO user_contact_profiles(
                    telegram_user_id, mobile_prompted_at, updated_at
                ) VALUES (%s, NOW(), NOW())
                ON CONFLICT(telegram_user_id) DO UPDATE SET
                    mobile_prompted_at=NOW(),
                    updated_at=NOW()
                """,
                (int(uid),),
            )
        conn.commit()


v89._mark_mobile_prompted = _mark_mobile_prompted_fresh


def _db_prompt_state_selftest():
    test_uid = -107000000001
    fresh_ok = False
    recent_ok = False
    cleaned = False
    try:
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM user_contact_profiles WHERE telegram_user_id=%s", (test_uid,))
                cur.execute(
                    """
                    INSERT INTO user_contact_profiles(
                        telegram_user_id, mobile_prompted_at, updated_at
                    ) VALUES (%s, NOW() - INTERVAL '2 hours', NOW())
                    """,
                    (test_uid,),
                )
            conn.commit()

        _mark_mobile_prompted_fresh(test_uid)
        row = v89._mobile_row(test_uid) or {}
        prompted_at = row.get("mobile_prompted_at")
        if prompted_at is not None:
            p = prompted_at if getattr(prompted_at, "tzinfo", None) else prompted_at.replace(tzinfo=timezone.utc)
            fresh_ok = (datetime.now(timezone.utc) - p).total_seconds() < 15
        recent_ok = bool(v105._mobile_flow_recent(test_uid))
    finally:
        try:
            with bot.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM user_contact_profiles WHERE telegram_user_id=%s", (test_uid,))
                conn.commit()
            cleaned = True
        except Exception:
            bot.logger.exception("V107_SELFTEST cleanup_failed")

    route_ok = bot.chat_handler is v105.v105_chat_handler
    format_ok = v105._is_indian_mobile("+918962697659") and not v105._is_indian_mobile("+971509430642")
    bot.logger.warning(
        "V107_SELFTEST prompt_refresh=%s recent_flow=%s chat_route=%s india_format=%s cleanup=%s",
        fresh_ok, recent_ok, route_ok, format_ok, cleaned,
    )
    if not all([fresh_ok, recent_ok, route_ok, format_ok, cleaned]):
        raise RuntimeError("V107 reward mobile prompt-state self-test failed")


bot.logger.warning(
    "V107_MOBILE_PROMPT_STATE_FIX active=on prompt_timestamp=refresh_every_open manual_plus91_route=on"
)


if __name__ == "__main__":
    _db_prompt_state_selftest()
    v105._startup_selftest()
    v97._startup_diagnostic()
    v96._startup_diagnostic()
    v93._startup_pdf_diagnostic()
    v101._phonepe_probe()
    v104._startup_mobile_diagnostic()
    v88.v63.apply_signup_cta()
    v85._enable_smart_reply_without_reset()
    threading.Thread(target=v83._worker_loop, name="betroxy-engagement-worker", daemon=True).start()
    bot.logger.warning("V107 polling handover delay=12s")
    time.sleep(12)
    bot.main()
