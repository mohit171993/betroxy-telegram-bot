"""One-time, account-scoped revocation of the pinned Betroxy tester's verification.

The saved contact, consent, CRM records, quiz history and rewards are untouched.
The durable marker prevents a later deploy from revoking a newly verified contact.
"""

import logging


LOGGER = logging.getLogger(__name__)
TARGET_USERNAME = "mohit_97saxena"
TARGET_UID = 1456774567
RESET_KEY = "mohit_97saxena_verification_reset_2026_09_29"


def _validated_uid(candidate_ids, pinned_uid):
    ids = {int(uid) for uid in candidate_ids if uid is not None}
    if ids != {TARGET_UID} or int(pinned_uid) != TARGET_UID:
        raise RuntimeError("Betroxy verification reset refused: tester identity changed or is ambiguous")
    return TARGET_UID


def apply_once(store, bot):
    """Revoke only the uniquely identified tester's live and pilot verification."""
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL lock_timeout='5s'")
            cur.execute("SELECT pg_advisory_xact_lock(290926, 1)")
            cur.execute(
                """
                SELECT telegram_user_id FROM intelligence_leads
                WHERE LOWER(LTRIM(COALESCE(telegram_username,''),'@'))=%s
                UNION
                SELECT telegram_user_id FROM referrals
                WHERE LOWER(LTRIM(COALESCE(telegram_username,''),'@'))=%s
                """,
                (TARGET_USERNAME, TARGET_USERNAME),
            )
            candidate_ids = [row["telegram_user_id"] for row in cur.fetchall()]
            cur.execute("SELECT telegram_user_id FROM btx_crm_test_identity WHERE slot=1")
            pinned = cur.fetchone()
            if pinned is None:
                raise RuntimeError("Betroxy verification reset refused: pinned tester is missing")
            uid = _validated_uid(candidate_ids, pinned["telegram_user_id"])
            if int(store.test_uid) != uid:
                raise RuntimeError("Betroxy verification reset refused: CRM identity does not match")

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS btx_verification_resets (
                    reset_key TEXT PRIMARY KEY,
                    telegram_user_id BIGINT NOT NULL,
                    live_rows INTEGER NOT NULL,
                    pilot_rows INTEGER NOT NULL,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                "SELECT telegram_user_id,live_rows,pilot_rows FROM btx_verification_resets WHERE reset_key=%s",
                (RESET_KEY,),
            )
            existing = cur.fetchone()
            if existing is not None:
                if int(existing["telegram_user_id"]) != uid:
                    raise RuntimeError("Betroxy verification reset marker points to another account")
                conn.commit()
                LOGGER.warning(
                    "BTX_VERIFICATION_RESET already_applied=on uid=%s live_rows=%s pilot_rows=%s",
                    uid, existing["live_rows"], existing["pilot_rows"],
                )
                return False

            cur.execute(
                "DELETE FROM v110_mobile_verifications WHERE telegram_user_id=%s RETURNING telegram_user_id",
                (uid,),
            )
            live_rows = len(cur.fetchall())
            cur.execute(
                "DELETE FROM btx_crm_verification_tests WHERE telegram_user_id=%s RETURNING telegram_user_id",
                (uid,),
            )
            pilot_rows = len(cur.fetchall())
            cur.execute(
                "SELECT COUNT(*) AS n FROM v110_mobile_verifications WHERE telegram_user_id=%s",
                (uid,),
            )
            if int(cur.fetchone()["n"]) != 0:
                raise RuntimeError("Betroxy live verification still exists after reset")
            cur.execute(
                "SELECT COUNT(*) AS n FROM btx_crm_verification_tests WHERE telegram_user_id=%s",
                (uid,),
            )
            if int(cur.fetchone()["n"]) != 0:
                raise RuntimeError("Betroxy pilot verification still exists after reset")
            cur.execute(
                """
                INSERT INTO btx_verification_resets(reset_key,telegram_user_id,live_rows,pilot_rows)
                VALUES (%s,%s,%s,%s)
                """,
                (RESET_KEY, uid, live_rows, pilot_rows),
            )
            conn.commit()
    LOGGER.warning(
        "BTX_VERIFICATION_RESET applied=on uid=%s live_rows=%s pilot_rows=%s "
        "saved_contact=preserved crm=preserved consent=preserved rewards=preserved",
        uid, live_rows, pilot_rows,
    )
    return True
