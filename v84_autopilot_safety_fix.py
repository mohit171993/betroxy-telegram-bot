import threading
import time

import bot
import v83_autopilot_engagement as v83

# V84 - safety hardening for V83 Autopilot.
# 1) New OfficialBot users are marked reachable AFTER the normal /start flow creates their lead row.
# 2) Ignored_count increments only once per actually ignored outbound message, never once per worker scan.
# 3) Referral reachability is refreshed every worker cycle.

v63 = v83.v63
_previous_start = bot.start
_old_sync_business_leads = v83._sync_business_leads


def _ensure_v84_schema():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE intelligence_leads ADD COLUMN IF NOT EXISTS last_ignored_contact_at TIMESTAMPTZ")
        conn.commit()


async def v84_start(update, context):
    result = await _previous_start(update, context)
    user = update.effective_user
    if user and int(user.id) != int(bot.ADMIN_ID):
        try:
            with bot.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE intelligence_leads
                        SET reachable_bot=TRUE, last_seen_at=NOW(), ignored_count=0,
                            lifecycle_stage=CASE WHEN opt_out THEN lifecycle_stage ELSE 'engaged' END,
                            suppressed_at=CASE WHEN opt_out THEN suppressed_at ELSE NULL END
                        WHERE telegram_user_id=%s
                        """,
                        (int(user.id),),
                    )
                conn.commit()
        except Exception:
            bot.logger.exception("V84_REACHABILITY_UPDATE_FAILED")
    return result


def _v84_sync_business_and_reachability():
    _old_sync_business_leads()
    try:
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE intelligence_leads l
                    SET reachable_bot=TRUE
                    WHERE EXISTS (
                        SELECT 1 FROM referrals r
                        WHERE r.telegram_user_id=l.telegram_user_id
                    )
                    """
                )
            conn.commit()
    except Exception:
        bot.logger.exception("V84_REFERRAL_REACHABILITY_SYNC_FAILED")


def _safe_prepare_for_contact(lead, max_weekly):
    uid = int(lead["telegram_user_id"])
    if lead.get("opt_out") or not lead.get("reachable_bot"):
        return False
    if v83._weekly_sent(uid) >= int(max_weekly):
        return False

    last_contact = lead.get("last_contact_at")
    last_seen = lead.get("last_seen_at")
    last_eval = lead.get("last_ignored_contact_at")
    ignored = int(lead.get("ignored_count") or 0)

    # Count an ignored message once only: the outbound contact must be newer than
    # both the user's last interaction and the last outbound already evaluated.
    if last_contact and last_seen and last_contact > last_seen and (not last_eval or last_contact > last_eval):
        ignored += 1
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                if ignored >= 3:
                    cur.execute(
                        """
                        UPDATE intelligence_leads
                        SET ignored_count=%s, last_ignored_contact_at=%s,
                            lifecycle_stage='suppressed', suppressed_at=NOW(),
                            last_event='auto_suppressed'
                        WHERE telegram_user_id=%s
                        """,
                        (ignored, last_contact, uid),
                    )
                    cur.execute("UPDATE engagement_subscriptions SET master_enabled=FALSE WHERE telegram_user_id=%s", (uid,))
                else:
                    cur.execute(
                        "UPDATE intelligence_leads SET ignored_count=%s,last_ignored_contact_at=%s WHERE telegram_user_id=%s",
                        (ignored, last_contact, uid),
                    )
            conn.commit()
        if ignored >= 3:
            return False
    return True


try:
    _ensure_v84_schema()
    bot.logger.warning("V84_AUTOPILOT_SAFETY_SCHEMA ready=on")
except Exception:
    bot.logger.exception("V84_AUTOPILOT_SAFETY_SCHEMA_FAILED")

v83._sync_business_leads = _v84_sync_business_and_reachability
v83._prepare_for_contact = _safe_prepare_for_contact
bot.start = v84_start

bot.logger.warning(
    "V84_AUTOPILOT_SAFETY active=on new_user_reachability=post_start "
    "ignore_count=once_per_outbound referral_reachability_sync=on"
)

if __name__ == "__main__":
    v63.apply_signup_cta()
    v63.enable_business_smart_auto_reply()
    threading.Thread(target=v83._worker_loop, name="betroxy-engagement-worker", daemon=True).start()
    bot.logger.warning("V84 polling handover delay=12s")
    time.sleep(12)
    bot.main()
