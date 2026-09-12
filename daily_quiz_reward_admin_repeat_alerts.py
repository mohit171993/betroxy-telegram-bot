"""Repeat Daily Quiz payout approval alerts to the primary admin only.

Safety/behavior:
- Customer messaging is untouched.
- The existing 21:05 IST reward-ready alert remains the first alert.
- If eligible payout is still awaiting approval, send one fresh reminder around
  21:20 IST and one final fresh reminder around 21:35 IST.
- Each reminder has its own durable delivery key, so restarts cannot duplicate it.
- Reminders stop automatically once all eligible awards have moved beyond the
  untouched queued/not-attempted state.
"""
from __future__ import annotations

from datetime import datetime, timezone
import threading
import time

import bot
import daily_quiz_schedule as schedule
import daily_quiz_admin_rewards as admin_rewards

FOLLOWUP_MINUTES = (15, 30)
INSTALL_WAIT_SECONDS = 1.0
INSTALL_MAX_WAIT_SECONDS = 300

_install_started = False
_installed = False
_original_announce_if_due = None
_original_alert_text = None


def _approval_pending(campaign, rows):
    """True while at least one eligible rank is still untouched and queued."""
    eligible = min(3, len(rows))
    if eligible <= 0:
        return False

    # Ensure the normal staged award records exist after finalisation.
    admin_rewards._stage_today(campaign, rows)

    for rank in range(1, eligible + 1):
        award = admin_rewards._award_for_rank(campaign, rank)
        if not award:
            return True
        status = str(award.get("status") or "queued")
        attempts = int(award.get("issue_attempts") or 0)
        if status == "queued" and attempts <= 0:
            return True
    return False


def _minutes_since_result(campaign):
    _, result_utc = schedule._day_bounds(campaign.get("campaign_date"))
    return (datetime.now(timezone.utc) - result_utc).total_seconds() / 60.0


def _followup_slot(campaign):
    """Return 15 or 30 for the currently due follow-up, otherwise None.

    We intentionally do not backfill both reminders after a long restart. From
    21:20-21:34 IST the 15-minute reminder is current; from 21:35 onward only the
    final 30-minute reminder is current.
    """
    elapsed = _minutes_since_result(campaign)
    if FOLLOWUP_MINUTES[0] <= elapsed < FOLLOWUP_MINUTES[1]:
        return FOLLOWUP_MINUTES[0]
    if elapsed >= FOLLOWUP_MINUTES[1]:
        return FOLLOWUP_MINUTES[1]
    return None


def _prominent_alert_text(campaign, rows):
    total = admin_rewards._eligible_total(rows)
    base = _original_alert_text(campaign, rows)
    if total <= 0:
        return base
    return (
        "🚨🚨 <b>ACTION REQUIRED — DAILY QUIZ PAYOUT</b> 🚨🚨\n\n"
        f"💰 <b>₹{total} is awaiting your approval.</b>\n"
        "Please tap the approval button below so today's winners can receive their prizes.\n\n"
        + base
    )


def _followup_text(campaign, rows, minutes):
    return (
        f"⏰ <b>PAYOUT REMINDER — {minutes} MINUTES PENDING</b>\n\n"
        + _prominent_alert_text(campaign, rows)
        + "\n\nThis reminder stops automatically after the payout is approved."
    )


def _send_followup_if_due(campaign, rows):
    ready, _ = admin_rewards._finalization_state(campaign)
    if not ready or not rows or not _approval_pending(campaign, rows):
        return False

    slot = _followup_slot(campaign)
    if slot is None:
        return False

    admin_id = int(bot.ADMIN_ID)
    delivery_type = f"daily_quiz_admin_reward_followup_{slot}m"
    if schedule.v110._delivery_exists(campaign["id"], str(admin_id), delivery_type):
        return False

    try:
        ok, data = schedule._send_text(
            admin_id,
            _followup_text(campaign, rows, slot),
            admin_rewards._admin_alert_rows(campaign, rows),
        )
    except Exception:
        bot.logger.exception(
            "DAILY_QUIZ_ADMIN_REPEAT_ALERT_FAILED campaign=%s slot=%sm",
            campaign.get("id"), slot,
        )
        return False

    if ok:
        payload = data if isinstance(data, dict) else {}
        mid = ((payload.get("result") or {}).get("message_id"))
        schedule.v110._mark_delivery(
            campaign["id"], str(admin_id), delivery_type, mid
        )

    bot.logger.warning(
        "DAILY_QUIZ_ADMIN_REPEAT_ALERT sent=%s campaign=%s slot=%sm winners=%s total=%s customer_dm=off",
        ok, campaign.get("id"), slot, min(3, len(rows)), admin_rewards._eligible_total(rows),
    )
    return bool(ok)


def _install_now():
    global _installed, _original_announce_if_due, _original_alert_text
    if _installed:
        return True
    if admin_rewards._original_announce_if_due is None:
        return False

    _original_alert_text = admin_rewards._admin_alert_text
    admin_rewards._admin_alert_text = _prominent_alert_text

    # At this point admin_rewards.install() has already wrapped the schedule's
    # result announcer. Preserve that full behavior and add only follow-up checks.
    _original_announce_if_due = schedule._announce_if_due

    def _announce_with_repeat_reminders():
        result = _original_announce_if_due()
        try:
            campaign, rows = admin_rewards._today_snapshot()
            _send_followup_if_due(campaign, rows)
        except Exception:
            bot.logger.exception("DAILY_QUIZ_ADMIN_REPEAT_CHECK_FAILED")
        return result

    schedule._announce_if_due = _announce_with_repeat_reminders
    _installed = True
    bot.logger.warning(
        "DAILY_QUIZ_ADMIN_REPEAT_ALERTS active=on initial=21:05 followups=21:20/21:35_IST "
        "stop_after_approval=on durable_dedupe=on customer_dm=off"
    )
    return True


def _late_installer():
    deadline = time.time() + INSTALL_MAX_WAIT_SECONDS
    while time.time() < deadline:
        if _install_now():
            return
        time.sleep(INSTALL_WAIT_SECONDS)
    bot.logger.error("DAILY_QUIZ_ADMIN_REPEAT_ALERTS_INSTALL_TIMEOUT")


def install_when_ready():
    """Install after daily_quiz_admin_rewards.install() without changing load order."""
    global _install_started
    if _install_started or _installed:
        return
    _install_started = True
    threading.Thread(
        target=_late_installer,
        name="betroxy-admin-payout-repeat-installer",
        daemon=True,
    ).start()
