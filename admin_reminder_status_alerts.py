"""Frequent, low-volume admin status alerts for BETROXY reminder delivery.

This module does not change customer delivery speed. It only increases visibility
for the admin while preserving the account-safety-first queue policy.
"""
from __future__ import annotations

import threading
import time

import bot
import channel_subscription_cta
import channel_membership_tracker

HEARTBEAT_SECONDS = 15 * 60
PROGRESS_EVERY_USERS = 5
_initial_delay_seconds = 90
_installed = False
_quiz_alerts = None


def _delivery_counts(message_key):
    """Return current persisted delivery state for one target quiz key."""
    counts = {
        "official_sent": 0,
        "business_sent": 0,
        "failed": 0,
        "sending": 0,
    }
    try:
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT channel,status,COUNT(*) AS n
                    FROM engagement_log
                    WHERE message_key=%s
                    GROUP BY channel,status
                    """,
                    (str(message_key),),
                )
                rows = cur.fetchall()
        for row in rows:
            channel = str(row.get("channel") or "")
            status = str(row.get("status") or "")
            n = int(row.get("n") or 0)
            if status == "sent" and channel == "business":
                counts["business_sent"] += n
            elif status == "sent":
                counts["official_sent"] += n
            elif status == "failed":
                counts["failed"] += n
            elif status == "sending":
                counts["sending"] += n
    except Exception:
        bot.logger.exception("ADMIN_REMINDER_STATUS_COUNTS_FAILED key=%s", message_key)
    return counts


def _channel_conversion_text():
    """Use persisted audit data only; never call Telegram from the 15m heartbeat."""
    try:
        snap = channel_membership_tracker.snapshot()
        if not snap:
            return ""
        all_s = snap["all"]
        return (
            "\n\n📢 <b>Updates Channel Conversion</b>\n"
            f"Joined @betroxyupdates: <b>{all_s['members']}/{all_s['total']}</b>\n"
            f"Confirmed not joined: <b>{all_s['nonmembers']}</b> • "
            f"Unknown: <b>{all_s['unknown']}</b>"
        )
    except Exception:
        bot.logger.exception("ADMIN_REMINDER_CHANNEL_SNAPSHOT_FAILED")
        return ""


def _send_snapshot(target_day, source):
    state = _quiz_alerts._queue_items(target_day)
    items = list(state.get("items") or [])
    message_key = str(state.get("message_key") or "")
    official_pending = sum(1 for item in items if item.get("route") == "officialbot")
    business_pending = sum(1 for item in items if item.get("route") == "business")
    persisted = _delivery_counts(message_key)

    # No heartbeat spam after the queue has fully cleared. The normal final report
    # remains authoritative for completion.
    if not items and not persisted["sending"] and not persisted["failed"]:
        return False

    local = _quiz_alerts._local_now()
    text = (
        "🔄 <b>BETROXY Reminder Live Status</b>\n\n"
        f"Time: <b>{local.strftime('%H:%M')} IST</b>\n"
        f"Target quiz date: <b>{target_day.isoformat()}</b>\n"
        f"Queue source: <b>{source}</b>\n\n"
        f"✅ OfficialBot delivered: <b>{persisted['official_sent']}</b>\n"
        f"✅ Business DM delivered: <b>{persisted['business_sent']}</b>\n"
        f"⏳ OfficialBot pending: <b>{official_pending}</b>\n"
        f"⏳ Business DM pending: <b>{business_pending}</b>\n"
        f"🔁 Currently sending/recovering: <b>{persisted['sending']}</b>\n"
        f"⚠️ Failed records awaiting recovery/check: <b>{persisted['failed']}</b>\n"
        f"📦 Total remaining now: <b>{len(items)}</b>"
        f"{_channel_conversion_text()}\n\n"
        "🛡 Account-safety pacing is unchanged: Bot ≥60s, Business ≥10m, "
        "Telegram RetryAfter is always respected."
    )
    return bool(_quiz_alerts._admin_notice(text))


def _heartbeat_worker():
    time.sleep(_initial_delay_seconds)
    while True:
        try:
            if _quiz_alerts.schedule.SCHEDULE_ENABLED:
                local = _quiz_alerts._local_now()
                target_day, source = _quiz_alerts._target_day_for_queue(local)
                if target_day is not None:
                    _send_snapshot(target_day, source)
        except Exception:
            bot.logger.exception("ADMIN_REMINDER_STATUS_HEARTBEAT_FAILED")
        time.sleep(HEARTBEAT_SECONDS)


def install(quiz_alerts):
    global _installed, _quiz_alerts
    if _installed:
        return
    _quiz_alerts = quiz_alerts

    # Add a one-tap @betroxyupdates subscription CTA to both OfficialBot and the
    # weekly Business reminder. This is conversion-only and changes no pacing,
    # eligibility, cooldown or dedupe policy.
    channel_subscription_cta.install(quiz_alerts)

    # Keep every Updates menu destination aligned with the production channel.
    # clean_customer_menu resolves bot.UPDATES_URL at callback time, so this late
    # authority update also fixes the existing Updates & Promotions channel button.
    bot.UPDATES_URL = channel_subscription_cta.CHANNEL_URL

    # Slowly measure which known Bot/Business users have actually joined the
    # channel. This is a once-daily getChatMember audit and sends no customer DM.
    channel_membership_tracker.install(quiz_alerts._admin_notice)

    # Existing queue progress messages now arrive after every five processed users
    # instead of every twenty. This changes reporting only, never customer pacing.
    quiz_alerts.PROGRESS_EVERY = PROGRESS_EVERY_USERS

    threading.Thread(
        target=_heartbeat_worker,
        name="betroxy-admin-reminder-status",
        daemon=True,
    ).start()
    _installed = True
    bot.logger.warning(
        "ADMIN_REMINDER_STATUS_ALERTS active=on heartbeat=15m progress_every=5 "
        "channel_subscription_cta=on channel_conversion_tracking=on "
        "updates_channel=@betroxyupdates customer_pacing_unchanged=on account_priority=on"
    )
