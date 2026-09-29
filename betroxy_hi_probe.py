"""At-most-once, user-requested neutral Bot API delivery check for one pinned chat."""

import asyncio
import inspect
import logging


LOGGER = logging.getLogger(__name__)
TARGET_UID = 1456774567
PROBE_KEY = "betroxy_mohit_97saxena_hi_2026_09_29"


def _claim(bot):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL lock_timeout='5s'")
            cur.execute("SELECT telegram_user_id FROM btx_crm_test_identity WHERE slot=1")
            row = cur.fetchone()
            if row is None or int(row["telegram_user_id"]) != TARGET_UID:
                raise RuntimeError("Pinned Betroxy user does not match requested hi target")
            cur.execute(
                """CREATE TABLE IF NOT EXISTS btx_one_time_probe_sends (
                    probe_key TEXT PRIMARY KEY,
                    telegram_user_id BIGINT NOT NULL,
                    attempted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    completed_at TIMESTAMPTZ,
                    state TEXT NOT NULL,
                    telegram_message_id BIGINT,
                    error_type TEXT
                )"""
            )
            cur.execute(
                """INSERT INTO btx_one_time_probe_sends(probe_key,telegram_user_id,state)
                   VALUES (%s,%s,'attempted') ON CONFLICT(probe_key) DO NOTHING
                   RETURNING probe_key""",
                (PROBE_KEY, TARGET_UID),
            )
            claimed = cur.fetchone() is not None
        conn.commit()
    return claimed


def _record(bot, state, message_id=None, error_type=None):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE btx_one_time_probe_sends SET completed_at=NOW(), state=%s,
                   telegram_message_id=%s,error_type=%s WHERE probe_key=%s
                   AND telegram_user_id=%s""",
                (state, message_id, error_type, PROBE_KEY, TARGET_UID),
            )
        conn.commit()


def install(bot):
    previous_post_init = bot.post_init

    async def probe_post_init(app):
        result = previous_post_init(app)
        if inspect.isawaitable(result):
            await result
        try:
            claimed = await asyncio.to_thread(_claim, bot)
        except Exception as exc:
            LOGGER.warning("BTX_HI_PROBE claim=failed uid=%s error_type=%s", TARGET_UID, type(exc).__name__)
            return
        if not claimed:
            LOGGER.warning("BTX_HI_PROBE duplicate_prevented=on uid=%s", TARGET_UID)
            return
        try:
            sent = await app.bot.send_message(chat_id=TARGET_UID, text="hi")
        except Exception as exc:
            error_type = type(exc).__name__
            LOGGER.warning("BTX_HI_PROBE sent=off uid=%s error_type=%s", TARGET_UID, error_type)
            try:
                await asyncio.to_thread(_record, bot, "failed", error_type=error_type)
            except Exception:
                LOGGER.exception("BTX_HI_PROBE result_record_failed uid=%s", TARGET_UID)
            return
        message_id = getattr(sent, "message_id", None)
        LOGGER.warning("BTX_HI_PROBE sent=on uid=%s message_id=%s", TARGET_UID, message_id)
        try:
            await asyncio.to_thread(_record, bot, "sent", message_id=message_id)
        except Exception:
            LOGGER.exception("BTX_HI_PROBE result_record_failed uid=%s", TARGET_UID)

    bot.post_init = probe_post_init
