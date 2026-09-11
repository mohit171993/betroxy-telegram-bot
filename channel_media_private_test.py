"""Admin-only private end-to-end test and manual posting for BETROXY channel media.

/test_channel_media sends all four production channel creatives privately to
ADMIN_ID using the exact approved Telegram file_ids. It never posts to the
public channel and never marks a public delivery as sent.

/post_10am_now posts the locked 10 AM creative to the configured public channel
through the same fixed-media production path and does not auto-delete it.
"""
from __future__ import annotations

import asyncio
import html

import bot
import channel_media_manager as media
import daily_quiz_schedule as schedule

_previous_post_init = None
_installed = False
AUTO_REPOST_KEY = "manual_10am_repost_20260911_after_channel_rename_v1"


def _play_rows():
    return bot.InlineKeyboardMarkup([[
        bot.InlineKeyboardButton(
            "🏆 Play Today's Quiz",
            url=f"https://t.me/{bot.BOT_USERNAME}?start=dailyquiz",
        )
    ]])


def _play_rows_raw():
    return [[{
        "text": "🏆 Play Today's Quiz",
        "url": f"https://t.me/{bot.BOT_USERNAME}?start=dailyquiz",
    }]]


def _caption_open():
    return (
        "🏆 <b>Today's BETROXY Daily Quiz is OPEN</b>\n\n"
        "Play anytime today until <b>9:00 PM IST</b>.\n\n"
        "🎁 <b>₹1,000 Amazon Pay Gift Voucher prize pool</b>\n"
        "🥇 1st — ₹500\n"
        "🥈 2nd — ₹300\n"
        "🥉 3rd — ₹200\n\n"
        "7 questions • 30 seconds each • one attempt today\n"
        "💯 Free to participate — no deposit or wager required."
    )


def _caption_afternoon():
    return (
        "🏆 <b>Can You Reach Today's Top 3?</b>\n\n"
        "The BETROXY Daily Quiz leaderboard is still moving — and you haven't completed today's challenge yet.\n\n"
        "🎁 <b>₹1,000 Amazon Pay Gift Voucher prize pool</b>\n"
        "🥇 1st — ₹500\n"
        "🥈 2nd — ₹300\n"
        "🥉 3rd — ₹200\n\n"
        "7 questions • 30 seconds each\n"
        "🎯 Accuracy comes first; hard-question accuracy and speed break ties\n"
        "💯 Free to participate — no deposit or wager required\n\n"
        "⏰ Entries close at <b>9:00 PM IST</b>.\n"
        "🏆 Play now and put your score on the leaderboard."
    )


def _caption_last_chance():
    return (
        "⏰ <b>Only 2 Hours Left — Final Call</b>\n\n"
        "You still haven't completed today's BETROXY Daily Quiz. Entries close at <b>9:00 PM IST</b>.\n\n"
        "🎁 <b>₹1,000 Amazon Pay Gift Voucher prize pool</b>\n"
        "🥇 1st — ₹500\n"
        "🥈 2nd — ₹300\n"
        "🥉 3rd — ₹200\n\n"
        "7 questions • 30 seconds each • one attempt today\n"
        "💯 Free to participate — no deposit or wager required\n\n"
        "🔥 This is your last reminder for today's challenge.\n"
        "🏆 Play now before the leaderboard closes."
    )


def _caption_result():
    try:
        campaign = schedule._today_campaign_windowed(test_mode=False)
        rows = schedule._final_rows(campaign["id"])
        return schedule._winner_text(rows)
    except Exception:
        bot.logger.exception("CHANNEL_MEDIA_PRIVATE_TEST_RESULT_BUILD_FAILED")
        return (
            "🏆 <b>BETROXY DAILY CHALLENGE — FINAL RESULTS</b>\n\n"
            "No eligible completed entries today.\n\n"
            "Final prize ranking: accuracy → hard-question accuracy → total answer time."
        )


def _ensure_repost_schema():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """CREATE TABLE IF NOT EXISTS channel_manual_reposts (
                    repost_key TEXT PRIMARY KEY,
                    channel_chat TEXT NOT NULL,
                    message_id BIGINT,
                    sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )"""
            )
        conn.commit()


def _repost_done(key=AUTO_REPOST_KEY):
    _ensure_repost_schema()
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT message_id FROM channel_manual_reposts WHERE repost_key=%s", (key,))
            row = cur.fetchone()
    return bool(row and row.get("message_id"))


def _save_repost(message_id, key=AUTO_REPOST_KEY):
    _ensure_repost_schema()
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO channel_manual_reposts(repost_key,channel_chat,message_id)
                   VALUES (%s,%s,%s)
                   ON CONFLICT(repost_key) DO UPDATE SET
                     channel_chat=EXCLUDED.channel_chat,
                     message_id=EXCLUDED.message_id,
                     sent_at=NOW()""",
                (key, str(schedule.v110.CHANNEL_CHAT), int(message_id)),
            )
        conn.commit()


def _send_public_10am():
    if media._approved_count() < 4:
        return False, {"description": "locked media is not 4/4"}
    if not media._approved_file_id("quiz_open"):
        return False, {"description": "quiz_open is not approved/locked"}
    return schedule._send_text(
        schedule.v110.CHANNEL_CHAT,
        _caption_open(),
        _play_rows_raw(),
    )


async def _send_slot(context, chat_id, asset_key, caption, with_play_button=True):
    file_id = media._approved_file_id(asset_key)
    if not file_id:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"❌ {media.ASSETS[asset_key]} is not approved/locked.",
        )
        bot.logger.error("CHANNEL_MEDIA_PRIVATE_TEST asset=%s sent=false reason=not_locked", asset_key)
        return False
    try:
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=file_id,
            caption=caption,
            parse_mode=bot.ParseMode.HTML,
            reply_markup=_play_rows() if with_play_button else None,
        )
        bot.logger.warning(
            "CHANNEL_MEDIA_PRIVATE_TEST asset=%s sent=true source=approved_telegram_file_id public=false",
            asset_key,
        )
        return True
    except Exception:
        bot.logger.exception("CHANNEL_MEDIA_PRIVATE_TEST asset=%s sent=false", asset_key)
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"❌ Private test failed for {html.escape(media.ASSETS[asset_key])}.",
            parse_mode=bot.ParseMode.HTML,
        )
        return False


async def test_channel_media(update, context):
    user = getattr(update, "effective_user", None)
    message = getattr(update, "effective_message", None)
    if not user or int(user.id) != int(bot.ADMIN_ID) or not message:
        return

    count = media._approved_count()
    await message.reply_text(
        "🧪 <b>BETROXY CHANNEL MEDIA — PRIVATE TEST</b>\n\n"
        f"Locked media: <b>{count}/4</b>\n"
        f"Nothing in this test is posted to {html.escape(str(schedule.v110.CHANNEL_CHAT))}.\n"
        "Sending the four production slots now…",
        parse_mode=bot.ParseMode.HTML,
    )

    tests = [
        ("quiz_open", _caption_open(), True),
        ("quiz_afternoon", _caption_afternoon(), True),
        ("quiz_last_chance", _caption_last_chance(), True),
        ("quiz_result", _caption_result(), False),
    ]
    passed = 0
    for asset_key, caption, with_button in tests:
        if await _send_slot(context, user.id, asset_key, caption, with_button):
            passed += 1

    await context.bot.send_message(
        chat_id=user.id,
        text=(
            "✅ <b>PRIVATE CHANNEL MEDIA TEST COMPLETE</b>\n\n"
            f"Passed: <b>{passed}/4</b>\n"
            "Public channel posts sent: <b>0</b>\n\n"
            "If all four images/captions above look correct, the locked-media path is ready."
        ),
        parse_mode=bot.ParseMode.HTML,
    )
    bot.logger.warning("CHANNEL_MEDIA_PRIVATE_TEST_COMPLETE passed=%s/4 public=false", passed)


async def post_10am_now(update, context):
    user = getattr(update, "effective_user", None)
    message = getattr(update, "effective_message", None)
    if not user or int(user.id) != int(bot.ADMIN_ID) or not message:
        return

    ok, data = await asyncio.to_thread(_send_public_10am)
    payload = data if isinstance(data, dict) else {}
    mid = ((payload.get("result") or {}).get("message_id")) if payload else None
    if ok and mid:
        await message.reply_text(
            f"✅ <b>10 AM post published</b>\n\nChannel: <b>{html.escape(str(schedule.v110.CHANNEL_CHAT))}</b>\nMessage ID: <b>{mid}</b>\nAuto-delete: <b>OFF</b>",
            parse_mode=bot.ParseMode.HTML,
        )
        bot.logger.warning(
            "CHANNEL_MANUAL_10AM_POST sent=true channel=%s message_id=%s source=approved_file_id auto_delete=off",
            schedule.v110.CHANNEL_CHAT, mid,
        )
    else:
        detail = str(payload.get("description") or payload or "send failed")
        await message.reply_text(
            "❌ <b>10 AM channel post failed</b>\n\n" + html.escape(detail),
            parse_mode=bot.ParseMode.HTML,
        )
        bot.logger.error("CHANNEL_MANUAL_10AM_POST sent=false detail=%s", detail)


async def _one_time_repost(application):
    await asyncio.sleep(5)
    try:
        if _repost_done():
            bot.logger.warning("CHANNEL_MANUAL_10AM_REPOST skipped=already_sent key=%s", AUTO_REPOST_KEY)
            return
        ok, data = await asyncio.to_thread(_send_public_10am)
        payload = data if isinstance(data, dict) else {}
        mid = ((payload.get("result") or {}).get("message_id")) if payload else None
        if ok and mid:
            _save_repost(mid)
            bot.logger.warning(
                "CHANNEL_MANUAL_10AM_REPOST sent=true channel=%s message_id=%s source=approved_file_id auto_delete=off",
                schedule.v110.CHANNEL_CHAT, mid,
            )
            await application.bot.send_message(
                chat_id=bot.ADMIN_ID,
                text=(
                    "✅ <b>10 AM channel post restored</b>\n\n"
                    f"Posted again to <b>{html.escape(str(schedule.v110.CHANNEL_CHAT))}</b>.\n"
                    f"Message ID: <b>{mid}</b>\n"
                    "This post will <b>not</b> auto-delete."
                ),
                parse_mode=bot.ParseMode.HTML,
            )
        else:
            detail = str(payload.get("description") or payload or "send failed")
            bot.logger.error("CHANNEL_MANUAL_10AM_REPOST sent=false detail=%s", detail)
    except Exception:
        bot.logger.exception("CHANNEL_MANUAL_10AM_REPOST_FAILED")


def install():
    global _previous_post_init, _installed
    if _installed:
        return
    _previous_post_init = bot.post_init

    async def private_test_post_init(application):
        if _previous_post_init:
            await _previous_post_init(application)
        application.add_handler(bot.CommandHandler("test_channel_media", test_channel_media), group=-110)
        application.add_handler(bot.CommandHandler("post_10am_now", post_10am_now), group=-111)
        application.create_task(_one_time_repost(application))
        bot.logger.warning("CHANNEL_MEDIA_PRIVATE_TEST_HANDLER active=on command=/test_channel_media admin_only=true public=false")
        bot.logger.warning("CHANNEL_MANUAL_10AM_HANDLER active=on command=/post_10am_now admin_only=true auto_delete=off")

    bot.post_init = private_test_post_init
    _installed = True
