"""BETROXY admin-alert parity policy.

Goal: match the useful alert pattern used by the IBETIN/FANTZO admin flows:
1) one immediate alert for a genuinely new verified lead;
2) one consolidated automation/growth report every two hours.

Everything else remains available inside the existing admin/CRM screens but does
not interrupt the admin as a background popup. Customer delivery, quiz rules,
rewards, schedules and Business auto-replies are unchanged.
"""
from __future__ import annotations

import html
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

import requests

log = logging.getLogger(__name__)
REPORT_INTERVAL_SECONDS = 2 * 60 * 60
_prepared = False
_store = None
_bot = None


def _ensure_schema():
    with _bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS btx_admin_alert_log (
                    event_key TEXT PRIMARY KEY,
                    alert_type TEXT NOT NULL,
                    telegram_user_id BIGINT,
                    sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
        conn.commit()


def _send_admin(text, keyboard=None):
    payload = {
        "chat_id": int(_bot.ADMIN_ID),
        "text": str(text),
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{_bot.BOT_TOKEN}/sendMessage",
            json=payload,
            timeout=20,
        )
        data = r.json() if r.content else {}
        ok = bool(r.ok and data.get("ok"))
        if not ok:
            log.warning(
                "BTX_REFERENCE_ADMIN_ALERT_SEND_FAILED type=http status=%s detail=%s",
                r.status_code,
                str(data.get("description") or "")[:220],
            )
        return ok
    except Exception as exc:
        log.warning(
            "BTX_REFERENCE_ADMIN_ALERT_SEND_FAILED type=%s",
            type(exc).__name__,
        )
        return False


def _mark_once(event_key, alert_type, uid=None):
    with _bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO btx_admin_alert_log(event_key,alert_type,telegram_user_id)
                VALUES (%s,%s,%s)
                ON CONFLICT(event_key) DO NOTHING
                RETURNING event_key
                """,
                (str(event_key), str(alert_type), int(uid) if uid else None),
            )
            row = cur.fetchone()
        conn.commit()
    return bool(row)


def _unmark(event_key):
    try:
        with _bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM btx_admin_alert_log WHERE event_key=%s",
                    (str(event_key),),
                )
            conn.commit()
    except Exception:
        log.exception("BTX_REFERENCE_ALERT_UNMARK_FAILED key=%s", event_key)


def _identity(uid):
    username = ""
    name = ""
    campaign = ""
    with _bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT to_regclass('public.intelligence_leads') AS intel,
                       to_regclass('public.referrals') AS refs
                """
            )
            tables = cur.fetchone() or {}
            if tables.get("intel"):
                cur.execute(
                    """
                    SELECT telegram_username,first_name,last_name,source
                    FROM intelligence_leads
                    WHERE telegram_user_id=%s
                    LIMIT 1
                    """,
                    (int(uid),),
                )
                row = cur.fetchone() or {}
                username = str(row.get("telegram_username") or "")
                name = " ".join(
                    str(x)
                    for x in (row.get("first_name"), row.get("last_name"))
                    if x
                )
                campaign = str(row.get("source") or "")
            if tables.get("refs"):
                cur.execute(
                    """
                    SELECT telegram_username,first_name,last_name,start_payload
                    FROM referrals
                    WHERE telegram_user_id=%s
                    LIMIT 1
                    """,
                    (int(uid),),
                )
                row = cur.fetchone() or {}
                username = username or str(row.get("telegram_username") or "")
                if not name:
                    name = " ".join(
                        str(x)
                        for x in (row.get("first_name"), row.get("last_name"))
                        if x
                    )
                campaign = str(row.get("start_payload") or campaign or "")
    return username.lstrip("@"), name, campaign


def _new_verified_alert(uid, mobile):
    event_key = f"verified:{int(uid)}"
    if not _mark_once(event_key, "new_verified_lead", uid):
        return
    username, name, campaign = _identity(uid)
    text = (
        "🔥 <b>NEW VERIFIED BETROXY LEAD</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"Mobile: <code>{html.escape(str(mobile))}</code>\n"
        f"Telegram: {'@' + html.escape(username) if username else '—'}\n"
        f"Name: {html.escape(name or '—')}\n"
        f"Campaign/source: <code>{html.escape(campaign or 'direct')}</code>\n"
        "Verification source: <code>telegram_contact</code>\n"
        f"User ID: <code>{int(uid)}</code>\n\n"
        "Lead status: <b>NEW</b>"
    )
    keyboard = [[
        {
            "text": "📊 Open Team CRM",
            "callback_data": "btxcrm:home",
        }
    ]]
    if not _send_admin(text, keyboard):
        _unmark(event_key)
    else:
        log.warning(
            "BTX_REFERENCE_NEW_VERIFIED_ALERT sent=on uid=%s",
            int(uid),
        )


def _install_verification_alert():
    import quiz_mobile_verification_overlay as verifier

    original = verifier._save_verification
    if getattr(original, "_btx_reference_alert_wrapped", False):
        return

    def wrapped(uid, mobile):
        existed = False
        try:
            with _bot.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT 1
                        FROM v110_mobile_verifications
                        WHERE telegram_user_id=%s
                        LIMIT 1
                        """,
                        (int(uid),),
                    )
                    existed = bool(cur.fetchone())
        except Exception:
            # Fail closed for notification only; verification itself must proceed.
            log.exception(
                "BTX_REFERENCE_VERIFY_PRECHECK_FAILED uid=%s notification_skipped=on",
                uid,
            )
            existed = True

        result = original(uid, mobile)
        if not existed and result:
            try:
                _new_verified_alert(int(uid), result)
            except Exception:
                log.exception(
                    "BTX_REFERENCE_NEW_VERIFIED_ALERT_FAILED uid=%s",
                    uid,
                )
        return result

    wrapped._btx_reference_alert_wrapped = True
    wrapped._btx_reference_alert_original = original
    verifier._save_verification = wrapped


def _install_quiet_reminder_admin_policy():
    import admin_reminder_status_alerts as status
    import quiz_v21_upgrade

    if getattr(status, "_btx_reference_quiet_install", False):
        return

    def silent_notice(text):
        log.info(
            "BTX_ADMIN_BACKGROUND_NOTICE_SUPPRESSED category=reminder_or_channel length=%s",
            len(str(text or "")),
        )
        return False

    def quiet_install(quiz_alerts):
        if status._installed:
            return
        status._quiz_alerts = quiz_alerts

        # Preserve the V2.1 customer result presentation without its separate
        # 21:10 admin analytics popup; those metrics are covered by the 2h report.
        if not quiz_v21_upgrade._installed:
            quiz_v21_upgrade._ensure_schema()
            patched = quiz_v21_upgrade._install_result_experience()
            quiz_v21_upgrade._installed = True
            _bot.logger.warning(
                "QUIZ_V21_UPGRADE active=on result_ux=%s admin_analytics=off "
                "reason=reference_admin_alert_parity core_rules_unchanged=on",
                "on" if patched else "pending",
            )

        status.channel_subscription_cta.install(quiz_alerts)
        _bot.UPDATES_URL = status.channel_subscription_cta.CHANNEL_URL

        # Keep membership measurement but do not push its daily admin card.
        status.channel_membership_tracker.install(silent_notice)

        # Suppress start/progress/final queue cards and the 15-minute heartbeat.
        # Customer queue pacing and delivery continue exactly as before.
        quiz_alerts._admin_notice = silent_notice
        quiz_alerts.PROGRESS_EVERY = 10**9

        # Do not install the 21:20/21:35 repeated payout nags. The original
        # interactive payout approval card at result time remains intact.
        status._installed = True
        _bot.logger.warning(
            "ADMIN_REMINDER_STATUS_ALERTS active=quiet "
            "heartbeat=off progress_popups=off queue_start_final_popups=off "
            "channel_membership_popup=off payout_repeat_alerts=off "
            "customer_pacing_unchanged=on"
        )

    status.install = quiet_install
    status._btx_reference_quiet_install = True


def _install_quiet_business_policy():
    import v85_silent_business_inbox as v85

    async def silent_attention(context, enquiry, intent, inbound_preview, reason, reopened=False):
        log.info(
            "BTX_BUSINESS_ATTENTION_POPUP_SUPPRESSED enquiry=%s reopened=%s reason=%s",
            enquiry.get("id"),
            bool(reopened),
            str(reason or "")[:120],
        )
        return None

    async def silent_new(context, enquiry, intent, inbound_preview, auto_replied):
        log.info(
            "BTX_BUSINESS_NEW_POPUP_SUPPRESSED enquiry=%s",
            enquiry.get("id"),
        )
        return None

    v85._send_attention_alert = silent_attention
    v85._send_new_lead_alert = silent_new
    v85._maybe_send_business_digest = lambda: None


def _install_weekly_reward_alert_policy():
    """Keep only the first action-required Mega payout card (slot 0)."""
    import weekly_mega_quiz as weekly

    original = weekly._send_admin_reward_alert
    if getattr(original, "_btx_reference_alert_wrapped", False):
        return

    def first_only(campaign, rows, slot):
        if int(slot or 0) > 0:
            log.info(
                "BTX_MEGA_PAYOUT_REPEAT_SUPPRESSED campaign=%s slot=%s",
                campaign.get("id"), slot,
            )
            return False
        return original(campaign, rows, slot)

    first_only._btx_reference_alert_wrapped = True
    first_only._btx_reference_alert_original = original
    weekly._send_admin_reward_alert = first_only


def _install_async_admin_message_filter():
    """Suppress a small set of legacy unsolicited admin popups additively.

    The filter is installed on the Telegram bot class only after the application
    exists. It does not block customer messages, admin-command replies, payout
    approval cards, or the two reference-style alerts produced by this module.
    """
    old_post_init = _bot.post_init

    async def post_init_with_filter(app):
        cls = app.bot.__class__
        current = cls.send_message
        if getattr(current, "_btx_admin_alert_filter", False) is not True:
            original = current
            blocked_prefixes = (
                # Receipt/status/report popups: state remains visible in admin screens.
                "✅ <b>WINNER CONFIRMED VOUCHER RECEIPT</b>",
                "🔄 <b>BETROXY Reminder Live Status</b>",
                "📬 <b>BETROXY BUSINESS INBOX • DAILY SUMMARY</b>",
                "🔴 <b>BUSINESS CHAT NEEDS ATTENTION</b>",
                "🟠 <b>RESOLVED CUSTOMER RETURNED</b>",
                "🟢 <b>NEW BUSINESS LEAD</b>",
                "📊 <b>BETROXY DAILY QUIZ — PERFORMANCE REPORT</b>",

                # Legacy V49 Business handlers can still be registered underneath
                # the newer silent inbox. Suppress their unsolicited admin copies.
                "🔌 <b>Telegram Business connection updated</b>",
                "🔔 <b>NEW TELEGRAM BUSINESS ENQUIRY</b>",

                # Backstop any legacy reminder worker that was armed before the
                # quiet installer. Customer delivery itself is not affected.
                "🛡 <b>BETROXY Daily Reminder Queue Started</b>",
                "📊 <b>BETROXY Reminder Queue Progress</b>",
                "✅ <b>BETROXY Daily Reminder Queue Report</b>",

                # Repeat payout nags stay quiet. The original result-time approval
                # cards remain untouched: DAILY QUIZ REWARDS — FINAL and the first
                # ACTION REQUIRED — MEGA QUIZ PAYOUT card still reach the admin.
                "🚨🚨 <b>ACTION REQUIRED — DAILY QUIZ PAYOUT</b> 🚨🚨",
                "⏰ <b>PAYOUT REMINDER —",
                "⏰ <b>REMINDER — MEGA QUIZ PAYOUT STILL PENDING</b>",
            )

            async def filtered_send_message(self, chat_id, text, *args, **kwargs):
                try:
                    is_admin = int(chat_id) == int(_bot.ADMIN_ID)
                except Exception:
                    is_admin = False
                value = str(text or "")
                if is_admin and value.startswith(blocked_prefixes):
                    log.info(
                        "BTX_LEGACY_ADMIN_POPUP_SUPPRESSED prefix=%s",
                        value.split("\n", 1)[0][:120],
                    )
                    return None
                return await original(self, chat_id, text, *args, **kwargs)

            filtered_send_message._btx_admin_alert_filter = True
            filtered_send_message._btx_admin_alert_original = original
            cls.send_message = filtered_send_message

        return await old_post_init(app)

    _bot.post_init = post_init_with_filter


def _report_stats():
    people = _store.snapshot()
    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(hours=24)
    total = len(people)
    mobile = sum(bool(p.get("phone")) for p in people.values())
    verified = sum(bool(p.get("verified")) for p in people.values())
    active_24h = sum(
        bool(p.get("last_seen") and p["last_seen"] >= day_ago)
        for p in people.values()
    )
    new_unworked = sum(p.get("status") == "new" for p in people.values())
    unassigned = sum(p.get("assigned_to") is None for p in people.values())
    interested = sum(p.get("status") == "interested" for p in people.values())
    converted = sum(p.get("status") == "converted" for p in people.values())

    extra = {
        "new_verified_24h": 0,
        "reminders_sent_24h": 0,
        "reminder_failed_24h": 0,
        "business_attention": 0,
        "business_unread": 0,
        "quiz_started_today": 0,
        "quiz_completed_today": 0,
    }
    with _bot.get_db() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    """
                    SELECT COUNT(*) AS n
                    FROM v110_mobile_verifications
                    WHERE verified_at >= NOW()-INTERVAL '24 hours'
                    """
                )
                extra["new_verified_24h"] = int((cur.fetchone() or {}).get("n") or 0)
            except Exception:
                pass
            try:
                cur.execute(
                    """
                    SELECT
                        COUNT(*) FILTER (WHERE status='sent') AS sent,
                        COUNT(*) FILTER (WHERE status='failed') AS failed
                    FROM engagement_log
                    WHERE created_at >= NOW()-INTERVAL '24 hours'
                    """
                )
                row = cur.fetchone() or {}
                extra["reminders_sent_24h"] = int(row.get("sent") or 0)
                extra["reminder_failed_24h"] = int(row.get("failed") or 0)
            except Exception:
                pass
            try:
                cur.execute(
                    """
                    SELECT
                        COUNT(*) FILTER (
                            WHERE status='open' AND admin_priority='attention'
                        ) AS attention,
                        COALESCE(SUM(silent_unread_count) FILTER (
                            WHERE status='open'
                        ),0) AS unread
                    FROM telegram_business_enquiries
                    """
                )
                row = cur.fetchone() or {}
                extra["business_attention"] = int(row.get("attention") or 0)
                extra["business_unread"] = int(row.get("unread") or 0)
            except Exception:
                pass
            try:
                cur.execute(
                    """
                    SELECT
                        COUNT(*) AS started,
                        COUNT(*) FILTER (WHERE e.completed_at IS NOT NULL) AS completed
                    FROM v110_quiz_entries e
                    JOIN v110_quiz_campaigns c ON c.id=e.campaign_id
                    WHERE c.campaign_date=(NOW() AT TIME ZONE 'Asia/Kolkata')::date
                      AND c.test_mode=FALSE
                    """
                )
                row = cur.fetchone() or {}
                extra["quiz_started_today"] = int(row.get("started") or 0)
                extra["quiz_completed_today"] = int(row.get("completed") or 0)
            except Exception:
                pass

    return {
        "total": total,
        "mobile": mobile,
        "verified": verified,
        "not_verified": max(0, total - verified),
        "active_24h": active_24h,
        "new_unworked": new_unworked,
        "unassigned": unassigned,
        "interested": interested,
        "converted": converted,
        **extra,
    }


def _send_two_hour_report():
    s = _report_stats()
    text = (
        "📊 <b>BETROXY AUTOMATION & GROWTH REPORT</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "<b>LEADS</b>\n"
        f"👥 All-time leads: <b>{s['total']}</b>\n"
        f"📱 Saved mobile: <b>{s['mobile']}</b>\n"
        f"✅ Verified: <b>{s['verified']}</b> · "
        f"⚠️ Not verified: <b>{s['not_verified']}</b>\n"
        f"🔥 New verified (24h): <b>{s['new_verified_24h']}</b>\n"
        f"🆕 New/Unworked: <b>{s['new_unworked']}</b> · "
        f"👤 Unassigned: <b>{s['unassigned']}</b>\n"
        f"⭐ Interested: <b>{s['interested']}</b> · "
        f"✅ Converted: <b>{s['converted']}</b>\n\n"
        "<b>ACTIVITY · 24H</b>\n"
        f"👥 Active users: <b>{s['active_24h']}</b>\n"
        f"📨 Automated deliveries sent: <b>{s['reminders_sent_24h']}</b>\n"
        f"⚠️ Delivery failures: <b>{s['reminder_failed_24h']}</b>\n\n"
        "<b>BUSINESS INBOX</b>\n"
        f"🔴 Needs attention: <b>{s['business_attention']}</b>\n"
        f"📥 Unread: <b>{s['business_unread']}</b>\n\n"
        "<b>DAILY QUIZ TODAY</b>\n"
        f"▶️ Started: <b>{s['quiz_started_today']}</b> · "
        f"✅ Completed: <b>{s['quiz_completed_today']}</b>\n\n"
        "🔄 Automatic report: every 2 hours"
    )
    return _send_admin(
        text,
        [[{"text": "📊 Open Team CRM", "callback_data": "btxcrm:home"}]],
    )


def _report_worker():
    # Match the reference bots: no extra startup message, first report after 2h.
    time.sleep(REPORT_INTERVAL_SECONDS)
    while True:
        try:
            _send_two_hour_report()
        except Exception:
            log.exception("BTX_REFERENCE_TWO_HOUR_REPORT_FAILED")
        time.sleep(REPORT_INTERVAL_SECONDS)


def prepare(production, store):
    global _prepared, _store, _bot
    if _prepared:
        return
    _bot = production.bot
    _store = store
    _ensure_schema()
    _install_verification_alert()
    _install_quiet_reminder_admin_policy()
    _install_quiet_business_policy()
    _install_weekly_reward_alert_policy()
    _install_async_admin_message_filter()

    # Disable startup-triggered Weekly Mega preview cards without affecting any
    # real channel post, uploader or schedule.
    try:
        import weekly_mega_admin_test_posts as previews

        def quiet_preview(*args, **kwargs):
            log.info("BTX_MEGA_ADMIN_PREVIEW_SUPPRESSED startup_preview=off")
            return 0

        previews.send_all_once = quiet_preview
    except Exception:
        log.exception("BTX_MEGA_PREVIEW_SUPPRESSION_FAILED")

    threading.Thread(
        target=_report_worker,
        name="betroxy-reference-admin-report",
        daemon=True,
    ).start()

    _prepared = True
    _bot.logger.warning(
        "BTX_ADMIN_ALERT_PARITY active=on reference=ibetin+fantzo "
        "new_verified_lead=immediate two_hour_summary=on "
        "15m_heartbeat=off queue_progress_popups=off "
        "business_popups=off daily_business_digest=off "
        "payout_repeat_nags=off receipt_confirmation_popup=off "
        "quiz_performance_popup=off legacy_business_enquiry_popups=off "
        "payout_repeat_fallback_filter=on initial_action_required_payout_card=preserved "
        "customer_delivery_unchanged=on"
    )
