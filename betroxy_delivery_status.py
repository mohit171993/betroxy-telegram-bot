"""On-demand, admin-only status for Betroxy delivery routes.

This sends no customer messages and starts no background polling or reminders.
"""

import asyncio
import inspect
import logging
import threading
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from telegram.ext import CommandHandler

import bot


LOGGER = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
CHANNEL_KINDS = (
    "quiz_alert_open", "quiz_alert_afternoon", "quiz_alert_last_chance", "final_result",
)
_installed = False


def _db_snapshot(day):
    import daily_quiz_alerts
    snapshot = {
        "last_post": None, "dm_counts": {}, "business_auto_reply": None,
        "membership_audit": None,
    }
    key = f"daily_quiz_open:{day.isoformat()}"
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout='3s'")
            cur.execute(
                """
                SELECT delivery_type,sent_at FROM v110_delivery_log
                WHERE target=%s AND delivery_type=ANY(%s)
                ORDER BY sent_at DESC LIMIT 1
                """,
                (daily_quiz_alerts.v110.CHANNEL_CHAT, list(CHANNEL_KINDS)),
            )
            snapshot["last_post"] = cur.fetchone()
            cur.execute(
                """
                SELECT channel,status,COUNT(*) AS n FROM engagement_log
                WHERE message_key=%s GROUP BY channel,status
                """,
                (key,),
            )
            for row in cur.fetchall():
                snapshot["dm_counts"][(str(row["channel"]), str(row["status"]))] = int(row["n"])
            cur.execute("SELECT auto_ack_enabled FROM telegram_business_settings WHERE id=1")
            row = cur.fetchone()
            snapshot["business_auto_reply"] = bool(row["auto_ack_enabled"]) if row else None
            cur.execute(
                """
                SELECT completed_at,checked,errors FROM betroxy_channel_membership_audits
                WHERE completed_at IS NOT NULL ORDER BY id DESC LIMIT 1
                """
            )
            snapshot["membership_audit"] = cur.fetchone()
    return snapshot


def _stamp(value):
    if value is None:
        return "none recorded"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(IST).strftime("%d %b %H:%M IST")


def _worker_alive(name):
    return any(t.name == name and t.is_alive() for t in threading.enumerate())


async def _channel_permission(context):
    import daily_quiz_alerts
    try:
        me = await asyncio.wait_for(context.bot.get_me(), timeout=5)
        member = await asyncio.wait_for(
            context.bot.get_chat_member(daily_quiz_alerts.v110.CHANNEL_CHAT, me.id),
            timeout=5,
        )
        status = str(member.status)
        return status == "creator" or (
            status == "administrator" and bool(getattr(member, "can_post_messages", False))
        )
    except Exception:
        LOGGER.exception("BTX_STATUS_CHANNEL_PERMISSION_UNAVAILABLE")
        return None


def _render(snapshot, day, permission):
    import business_dm_reply_fix
    import daily_quiz_alerts
    import daily_quiz_schedule
    import v49_telegram_business_inbox_bootstrap as business_inbox
    channel_worker = _worker_alive("betroxy-public-channel-schedule")
    result_worker = _worker_alive("betroxy-daily-quiz-schedule")
    dm_worker = _worker_alive("betroxy-daily-quiz-alerts")
    business_handler = (
        business_inbox._business_message_update
        is business_dm_reply_fix.business_message_update_with_menu_reply
    )
    generic_off = (
        daily_quiz_alerts.v83._worker_loop
        is daily_quiz_alerts._disabled_engagement_worker
    )
    permission_text = "YES" if permission is True else "NO" if permission is False else "unavailable"
    last = snapshot.get("last_post") if snapshot else None
    last_text = (
        f"{last['delivery_type']} · {_stamp(last['sent_at'])}"
        if last else "none recorded" if snapshot else "unavailable"
    )
    counts = snapshot.get("dm_counts", {}) if snapshot else {}
    count_text = (
        f"OfficialBot {counts.get(('officialbot', 'sent'), 0)} · "
        f"Business {counts.get(('business', 'sent'), 0)} · "
        f"failed {sum(n for (route, state), n in counts.items() if state == 'failed')}"
        if snapshot else "unavailable"
    )
    auto = snapshot.get("business_auto_reply") if snapshot else None
    auto_text = "ON" if auto is True else "OFF" if auto is False else "unavailable"
    audit = snapshot.get("membership_audit") if snapshot else None
    audit_text = (
        f"{_stamp(audit['completed_at'])} · errors {audit['errors']}/{audit['checked']}"
        if audit else "none recorded" if snapshot else "unavailable"
    )
    return (
        "<b>BETROXY delivery status</b>\n"
        f"Channel: <code>{daily_quiz_alerts.v110.CHANNEL_CHAT}</code> · "
        f"worker {'ON' if channel_worker and daily_quiz_schedule.SCHEDULE_ENABLED else 'OFF'} · "
        f"can post {permission_text}\n"
        f"Last confirmed channel post: {last_text}\n"
        f"Last channel membership audit: {audit_text}\n"
        f"Final result: {'ON' if result_worker and daily_quiz_schedule.RESULT_CHANNEL_ENABLED else 'OFF'}\n"
        f"Daily Quiz DM queue: {'ON' if dm_worker and daily_quiz_schedule.SCHEDULE_ENABLED else 'OFF'}\n"
        f"{day.isoformat()} DM delivery: {count_text}\n"
        f"Business DM handler: {'ON' if business_handler else 'OFF'} · smart reply {auto_text}\n"
        f"Other proactive reminders: {'OFF' if generic_off else 'check runtime'}"
    )


async def command(update, context):
    user = update.effective_user
    if user is None or not bot.is_admin(user.id):
        return
    if update.effective_chat is None or update.effective_chat.type != "private":
        return
    day = datetime.now(IST).date()
    try:
        snapshot = await asyncio.to_thread(_db_snapshot, day)
    except Exception:
        LOGGER.exception("BTX_STATUS_DATABASE_UNAVAILABLE")
        snapshot = None
    permission = await _channel_permission(context)
    await update.effective_message.reply_text(
        _render(snapshot, day, permission),
        parse_mode=bot.ParseMode.HTML,
        disable_web_page_preview=True,
    )


def install():
    global _installed
    if _installed:
        return
    previous_post_init = bot.post_init

    async def status_post_init(application):
        result = previous_post_init(application)
        if inspect.isawaitable(result):
            await result
        application.add_handler(CommandHandler("betroxy_status", command))
        LOGGER.warning("BTX_DELIVERY_STATUS ready=on admin_command=/betroxy_status background_alerts=off")

    bot.post_init = status_post_init
    _installed = True
