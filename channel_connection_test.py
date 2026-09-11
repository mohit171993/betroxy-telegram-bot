"""One-time BETROXY public channel connection test.

Runs through the real production schedule._send_text path so the fixed-media
wrapper, approved Telegram file_id, button, and channel posting permission are
verified together. The temporary test post is deleted after 30 seconds.
"""
import json
import threading
import time

import bot
import daily_quiz_alerts as alerts

schedule = alerts.schedule
v110 = alerts.v110
manager = alerts.channel_media_manager
TEST_KEY = "betroxy_channel_connection_test_v1_20260911"


def _ensure_schema():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """CREATE TABLE IF NOT EXISTS channel_connection_tests (
                    test_key TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    message_id BIGINT,
                    detail TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    completed_at TIMESTAMPTZ
                )"""
            )
        conn.commit()


def _already_passed():
    _ensure_schema()
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM channel_connection_tests WHERE test_key=%s", (TEST_KEY,))
            row = cur.fetchone() or {}
    return str(row.get("status") or "") == "passed"


def _save(status, message_id=None, detail=None):
    _ensure_schema()
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO channel_connection_tests(test_key,status,message_id,detail,completed_at)
                   VALUES (%s,%s,%s,%s,NOW())
                   ON CONFLICT(test_key) DO UPDATE SET
                     status=EXCLUDED.status,
                     message_id=EXCLUDED.message_id,
                     detail=EXCLUDED.detail,
                     completed_at=NOW()""",
                (TEST_KEY, status, message_id, detail),
            )
        conn.commit()


def _admin_notice(text):
    try:
        manager._api(
            "sendMessage",
            data={"chat_id": str(bot.ADMIN_ID), "text": text, "parse_mode": "HTML"},
        )
    except Exception:
        bot.logger.exception("CHANNEL_CONNECTION_TEST_ADMIN_NOTICE_FAILED")


def _run():
    time.sleep(8)
    try:
        if _already_passed():
            bot.logger.warning("CHANNEL_CONNECTION_TEST skipped=already_passed key=%s", TEST_KEY)
            return

        approved = manager._approved_count()
        permission = manager._check_channel_permission()
        if approved < 4 or not permission:
            detail = f"approved={approved}/4 can_post={permission}"
            _save("failed", detail=detail)
            bot.logger.error("CHANNEL_CONNECTION_TEST failed %s", detail)
            _admin_notice(
                "❌ <b>BETROXY channel connection test failed</b>\n\n"
                f"Locked images: <b>{approved}/4</b>\n"
                f"Channel posting permission: <b>{'YES' if permission else 'NO'}</b>"
            )
            return

        test_text = (
            "🧪 <b>BETROXY CHANNEL CONNECTION TEST</b>\n\n"
            "🏆 <b>Today's BETROXY Daily Quiz is OPEN</b>\n\n"
            "This temporary post verifies the real scheduled channel path, "
            "the locked 10 AM creative and the quiz button.\n\n"
            "✅ Bot → channel connection is working."
        )
        rows = [[{
            "text": "🏆 Play Today's Quiz",
            "url": f"https://t.me/{bot.BOT_USERNAME}?start=dailyquiz",
        }]]
        ok, data = schedule._send_text(v110.CHANNEL_CHAT, test_text, rows)
        payload = data if isinstance(data, dict) else {}
        result = payload.get("result") or {}
        mid = result.get("message_id")
        if not ok or not mid:
            detail = str(payload.get("description") or payload or "send failed")
            _save("failed", detail=detail)
            bot.logger.error("CHANNEL_CONNECTION_TEST send_failed detail=%s", detail)
            _admin_notice("❌ <b>BETROXY channel test post failed.</b>\n\n" + detail)
            return

        _save("passed", message_id=int(mid), detail="production_path_send_ok")
        bot.logger.warning(
            "CHANNEL_CONNECTION_TEST passed channel=%s message_id=%s approved=4/4 production_path=on",
            v110.CHANNEL_CHAT, mid,
        )
        _admin_notice(
            "✅ <b>BETROXY channel connection confirmed</b>\n\n"
            "The locked 10 AM image was posted through the real production schedule path. "
            "The temporary test post will be removed automatically."
        )

        time.sleep(30)
        deleted, delete_payload = manager._api(
            "deleteMessage",
            data={"chat_id": str(v110.CHANNEL_CHAT), "message_id": str(mid)},
        )
        bot.logger.warning(
            "CHANNEL_CONNECTION_TEST cleanup_deleted=%s message_id=%s detail=%s",
            deleted,
            mid,
            "ok" if deleted else (delete_payload or {}).get("description"),
        )
    except Exception as exc:
        _save("failed", detail=str(exc))
        bot.logger.exception("CHANNEL_CONNECTION_TEST_FAILED")
        _admin_notice("❌ <b>BETROXY channel connection test crashed.</b> Check Railway logs.")


def start():
    threading.Thread(target=_run, name="betroxy-channel-connection-test", daemon=True).start()
    bot.logger.warning("CHANNEL_CONNECTION_TEST armed=on one_time=on auto_delete=30s production_path=on")
