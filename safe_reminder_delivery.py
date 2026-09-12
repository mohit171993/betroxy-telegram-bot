"""Shared safe delivery layer for automated BETROXY Telegram messages.

This module deliberately affects automated/background sends that go through
v83._tg_send / v83._send_claimed. Interactive replies keep their existing
handlers, while reminders, quiz alerts, sports/promotions and reactivation
messages share one conservative queue.
"""
import os
import random
import re
import threading
import time

import bot

OFFICIAL_MIN_INTERVAL = max(1.0, float(os.getenv("BETROXY_DM_INTERVAL_SECONDS", "2.5")))
BUSINESS_MIN_INTERVAL = max(2.0, float(os.getenv("BETROXY_BUSINESS_DM_INTERVAL_SECONDS", "8.0")))
MAX_ATTEMPTS = max(2, min(5, int(os.getenv("BETROXY_DM_MAX_ATTEMPTS", "4"))))
STALE_SENDING_MINUTES = max(5, int(os.getenv("BETROXY_DM_STALE_MINUTES", "15")))

_rate_lock = threading.Lock()
_last_attempt_at = 0.0
_installed_v83 = None
_original_tg_send = None


def _retry_after_seconds(data):
    if not isinstance(data, dict):
        return None
    params = data.get("parameters") or {}
    try:
        if params.get("retry_after") is not None:
            return max(1, int(params.get("retry_after")))
    except Exception:
        pass
    desc = str(data.get("description") or "")
    m = re.search(r"retry(?:\s+in|\s+after)?\s+(\d+)", desc, flags=re.I)
    return int(m.group(1)) if m else None


def _error_code(data):
    if not isinstance(data, dict):
        return None
    try:
        return int(data.get("error_code")) if data.get("error_code") is not None else None
    except Exception:
        return None


def _description(data):
    return str((data or {}).get("description") or "") if isinstance(data, dict) else ""


def _permanent_unreachable(data):
    code = _error_code(data)
    desc = _description(data).lower()
    return code == 403 or "blocked" in desc or "forbidden" in desc or "chat not found" in desc


def _transient_failure(data):
    code = _error_code(data)
    desc = _description(data).lower()
    if code == 429 or _retry_after_seconds(data):
        return True
    if code is not None and 500 <= code <= 599:
        return True
    transient_words = (
        "timed out", "timeout", "temporarily", "connection", "network",
        "remote end closed", "bad gateway", "service unavailable",
    )
    return any(word in desc for word in transient_words)


def _paced_send(chat_id, text, keyboard=None, business_connection_id=None):
    """Serialize automated sends, respect RetryAfter, and retry temporary errors."""
    global _last_attempt_at
    if _original_tg_send is None:
        raise RuntimeError("safe_reminder_delivery is not installed")

    business = bool(business_connection_id)
    interval = BUSINESS_MIN_INTERVAL if business else OFFICIAL_MIN_INTERVAL
    attempts_used = 0

    # One lock for every automated route. A Telegram RetryAfter pauses the whole
    # automation queue instead of letting other worker threads continue flooding.
    with _rate_lock:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            attempts_used = attempt
            wait = (_last_attempt_at + interval + random.uniform(0.15, 0.65)) - time.monotonic()
            if wait > 0:
                time.sleep(wait)

            ok, data = _original_tg_send(
                chat_id,
                text,
                keyboard,
                business_connection_id=business_connection_id,
            )
            _last_attempt_at = time.monotonic()
            if ok:
                return True, data, attempts_used - 1

            retry_after = _retry_after_seconds(data)
            if retry_after is not None:
                if attempt < MAX_ATTEMPTS:
                    pause = retry_after + 2 + random.uniform(0.25, 1.0)
                    bot.logger.warning(
                        "SAFE_DM_RATE_LIMIT route=%s retry_after=%ss attempt=%s/%s",
                        "business" if business else "officialbot",
                        retry_after,
                        attempt,
                        MAX_ATTEMPTS,
                    )
                    time.sleep(pause)
                    continue
                return False, data, attempts_used - 1

            if _transient_failure(data) and attempt < MAX_ATTEMPTS:
                pause = min(45.0, 4.0 * (2 ** (attempt - 1))) + random.uniform(0.25, 1.25)
                bot.logger.warning(
                    "SAFE_DM_TRANSIENT_RETRY route=%s wait=%.1fs attempt=%s/%s error=%s",
                    "business" if business else "officialbot",
                    pause,
                    attempt,
                    MAX_ATTEMPTS,
                    _description(data)[:180],
                )
                time.sleep(pause)
                continue

            return False, data, attempts_used - 1

    return False, {"description": "safe delivery exhausted"}, attempts_used - 1


def _claim_or_recover(v83, user_id, action, key, channel="officialbot", detail=""):
    uid = int(user_id)
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id,status,
                       (created_at < NOW()-(%s * INTERVAL '1 minute')) AS stale
                FROM engagement_log
                WHERE telegram_user_id=%s AND message_key=%s
                ORDER BY id DESC
                LIMIT 1
                FOR UPDATE
                """,
                (int(STALE_SENDING_MINUTES), uid, str(key)),
            )
            row = cur.fetchone()
            if row:
                status = str(row.get("status") or "")
                if status == "sent":
                    conn.commit()
                    return None, "already_sent"
                if status == "sending" and not bool(row.get("stale")):
                    conn.commit()
                    return None, "in_progress"
                cur.execute(
                    """
                    UPDATE engagement_log
                    SET status='sending', channel=%s, action_type=%s,
                        campaign_key=%s, detail=%s
                    WHERE id=%s
                    RETURNING id
                    """,
                    (str(channel), str(action), str(key), ("retrying failed delivery; " + str(detail))[:2000], int(row["id"])),
                )
                claimed = cur.fetchone()
                conn.commit()
                return int(claimed["id"]), "recovered"

            cur.execute(
                """
                INSERT INTO engagement_log(
                    telegram_user_id,channel,action_type,campaign_key,message_key,status,detail
                )
                VALUES (%s,%s,%s,%s,%s,'sending',%s)
                ON CONFLICT(telegram_user_id,message_key) WHERE message_key IS NOT NULL DO NOTHING
                RETURNING id
                """,
                (uid, str(channel), str(action), str(key), str(key), str(detail)[:2000]),
            )
            claimed = cur.fetchone()
        conn.commit()
    return (int(claimed["id"]), "new") if claimed else (None, "in_progress")


def _mark_unreachable(user_id):
    try:
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE intelligence_leads
                    SET opt_out=TRUE,lifecycle_stage='unreachable',last_event='telegram_unreachable'
                    WHERE telegram_user_id=%s
                    """,
                    (int(user_id),),
                )
                cur.execute(
                    "UPDATE engagement_subscriptions SET master_enabled=FALSE WHERE telegram_user_id=%s",
                    (int(user_id),),
                )
            conn.commit()
    except Exception:
        bot.logger.exception("SAFE_DM_MARK_UNREACHABLE_FAILED uid=%s", user_id)


def send_claimed_result(
    user_id,
    action,
    key,
    text,
    keyboard=None,
    *,
    chat_id=None,
    business_connection_id=None,
    channel=None,
    detail="",
):
    """Send one deduplicated automated message and return structured outcome."""
    v83 = _installed_v83
    if v83 is None:
        raise RuntimeError("safe_reminder_delivery is not installed")

    route = channel or ("business" if business_connection_id else "officialbot")
    job_id, claim_state = _claim_or_recover(v83, user_id, action, key, route, detail)
    if not job_id:
        return {
            "sent": False,
            "status": claim_state,
            "retried": 0,
            "permanent": False,
        }

    target_chat = int(chat_id if chat_id is not None else user_id)
    ok, data, retried = _paced_send(
        target_chat,
        text,
        keyboard,
        business_connection_id=business_connection_id,
    )
    v83._finish_job(job_id, ok, data)

    if ok:
        try:
            with bot.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE intelligence_leads SET last_contact_at=NOW(), last_event=%s WHERE telegram_user_id=%s",
                        (f"auto_{action}", int(user_id)),
                    )
                conn.commit()
        except Exception:
            bot.logger.exception("SAFE_DM_CONTACT_STATE_FAILED uid=%s", user_id)
        return {
            "sent": True,
            "status": "sent",
            "retried": int(retried),
            "permanent": False,
        }

    permanent = _permanent_unreachable(data)
    if permanent:
        _mark_unreachable(user_id)
    return {
        "sent": False,
        "status": "failed",
        "retried": int(retried),
        "permanent": bool(permanent),
        "error": _description(data)[:300],
    }


def _safe_send_claimed(user_id, action, key, text, keyboard, business_connection_id=None):
    result = send_claimed_result(
        user_id,
        action,
        key,
        text,
        keyboard,
        business_connection_id=business_connection_id,
    )
    return bool(result.get("sent"))


def install(v83):
    """Install once before any production automation worker starts."""
    global _installed_v83, _original_tg_send
    if getattr(v83, "_safe_reminder_delivery_installed", False):
        _installed_v83 = v83
        return

    _installed_v83 = v83
    _original_tg_send = v83._tg_send
    v83._tg_send = lambda chat_id, text, keyboard=None, business_connection_id=None: _paced_send(
        chat_id,
        text,
        keyboard,
        business_connection_id=business_connection_id,
    )[:2]
    v83._send_claimed = _safe_send_claimed
    v83._safe_delivery_send_claimed_result = send_claimed_result
    v83._safe_reminder_delivery_installed = True

    bot.logger.warning(
        "SAFE_REMINDER_DELIVERY active=on official_interval=%.1fs business_interval=%.1fs max_attempts=%s global_queue=on retry_after=on failed_reclaim=on",
        OFFICIAL_MIN_INTERVAL,
        BUSINESS_MIN_INTERVAL,
        MAX_ATTEMPTS,
    )
