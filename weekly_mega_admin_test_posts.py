"""One-time admin-only preview of every Weekly Mega Quiz channel post.

This is additive test tooling only. It does not publish to @betroxyupdates,
does not touch Daily Quiz code/banners/schedules, does not write to the real
mega_quiz_deliveries table, and cannot affect payout state.
"""
from __future__ import annotations

import time

import bot

TEST_BATCH_KEY = "weekly_mega_channel_preview_2026_09_13_v1"


def _ensure_schema():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS mega_quiz_admin_test_posts (
                    batch_key TEXT NOT NULL,
                    slot_key TEXT NOT NULL,
                    sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY(batch_key, slot_key)
                )
            """)
        conn.commit()


def _already_sent(slot_key):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM mega_quiz_admin_test_posts WHERE batch_key=%s AND slot_key=%s",
                (TEST_BATCH_KEY, str(slot_key)),
            )
            return bool(cur.fetchone())


def _mark_sent(slot_key):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO mega_quiz_admin_test_posts(batch_key,slot_key)
                VALUES (%s,%s)
                ON CONFLICT(batch_key,slot_key) DO NOTHING
                """,
                (TEST_BATCH_KEY, str(slot_key)),
            )
        conn.commit()


def _send_text(weekly, admin_id, slot_key, label, text):
    if _already_sent(slot_key):
        return True
    body = f"🧪 <b>TEST PREVIEW — {label}</b>\n\n" + text
    rows = [[{"text": "🔥 Open Sunday Mega Quiz", "url": weekly.BOT_DEEPLINK}]]
    ok, detail = weekly.quiz._tg_send_text(int(admin_id), body, rows)
    if ok:
        _mark_sent(slot_key)
    bot.logger.warning(
        "MEGA_ADMIN_TEST_POST slot=%s mode=text sent=%s detail=%s",
        slot_key, ok, "ok" if ok else detail,
    )
    return bool(ok)


def _send_image(weekly, media, admin_id, slot_key, label, asset_key, caption):
    if _already_sent(slot_key):
        return True
    file_id = media._approved_file_id(asset_key)
    if not file_id:
        bot.logger.warning("MEGA_ADMIN_TEST_POST slot=%s missing_asset=%s", slot_key, asset_key)
        return False
    body = f"🧪 <b>TEST PREVIEW — {label}</b>\n\n" + caption
    rows = [[{"text": "🔥 Open Sunday Mega Quiz", "url": weekly.BOT_DEEPLINK}]]
    ok, detail = media._api_send_photo(int(admin_id), str(file_id), body, rows)
    if ok:
        _mark_sent(slot_key)
    bot.logger.warning(
        "MEGA_ADMIN_TEST_POST slot=%s mode=image asset=%s sent=%s detail=%s",
        slot_key, asset_key, ok, "ok" if ok else detail,
    )
    return bool(ok)


def _sample_result_text():
    return (
        "🔥 <b>BETROXY SUNDAY MEGA QUIZ — FINAL RESULTS</b>\n\n"
        "<i>Sample winner names shown only for this private test preview.</i>\n\n"
        "🥇 <b>Winner 1</b> — 10/10 — <b>₹2,500</b>\n"
        "🥈 <b>Winner 2</b> — 9/10 — <b>₹1,500</b>\n"
        "🥉 <b>Winner 3</b> — 8/10 — <b>₹1,000</b>\n\n"
        "Final ranking: accuracy → hard-question accuracy → total answer time.\n\n"
        "🎁 Total prize pool: <b>₹5,000 in Amazon Pay Gift Vouchers</b>"
    )


def send_all_once(weekly, media, schedule):
    """Send all seven scheduled post previews privately to the bot admin."""
    _ensure_schema()
    admin_id = int(bot.ADMIN_ID)
    campaign = weekly._ensure_campaign()

    jobs = [
        ("wed", lambda: _send_text(
            weekly, admin_id, "wed", "Wednesday 7:30 PM IST",
            schedule._midweek_text("midweek_teaser", campaign),
        )),
        ("fri", lambda: _send_text(
            weekly, admin_id, "fri", "Friday 7:30 PM IST",
            schedule._midweek_text("friday_reminder", campaign),
        )),
        ("sat", lambda: _send_image(
            weekly, media, admin_id, "sat", "Saturday 7:30 PM IST",
            "mega_preview", weekly._promo_text("preview", campaign),
        )),
        ("sun_open", lambda: _send_image(
            weekly, media, admin_id, "sun_open", "Sunday 10:05 AM IST",
            "mega_open", weekly._promo_text("open", campaign),
        )),
        ("sun_afternoon", lambda: _send_image(
            weekly, media, admin_id, "sun_afternoon", "Sunday 4:05 PM IST",
            "mega_afternoon", weekly._promo_text("reminder", campaign),
        )),
        ("sun_last", lambda: _send_image(
            weekly, media, admin_id, "sun_last", "Sunday 7:05 PM IST",
            "mega_last_chance", weekly._promo_text("last_call", campaign),
        )),
        ("sun_result", lambda: _send_image(
            weekly, media, admin_id, "sun_result", "Sunday 9:10 PM IST",
            "mega_result", _sample_result_text(),
        )),
    ]

    sent = 0
    for _, action in jobs:
        try:
            if action():
                sent += 1
        except Exception:
            bot.logger.exception("MEGA_ADMIN_TEST_POST_FAILED")
        time.sleep(1.5)

    bot.logger.warning(
        "MEGA_ADMIN_TEST_POSTS_COMPLETE sent_or_already=%s total=7 target=admin_only "
        "public_channel=off real_delivery_dedupe=untouched daily_quiz=untouched",
        sent,
    )
    return sent
