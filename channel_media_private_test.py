"""Admin-only private end-to-end test for locked BETROXY channel media.

/test_channel_media sends all four production channel creatives privately to
ADMIN_ID using the exact approved Telegram file_ids. It never posts to the
public channel and never marks a public delivery as sent.
"""
from __future__ import annotations

import html

import bot
import channel_media_manager as media
import daily_quiz_schedule as schedule

_previous_post_init = None
_installed = False


def _play_rows():
    return bot.InlineKeyboardMarkup([[
        bot.InlineKeyboardButton(
            "🏆 Play Today's Quiz",
            url=f"https://t.me/{bot.BOT_USERNAME}?start=dailyquiz",
        )
    ]])


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
        "Nothing in this test is posted to @betroxycasino.\n"
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


def install():
    global _previous_post_init, _installed
    if _installed:
        return
    _previous_post_init = bot.post_init

    async def private_test_post_init(application):
        if _previous_post_init:
            await _previous_post_init(application)
        application.add_handler(bot.CommandHandler("test_channel_media", test_channel_media), group=-110)
        bot.logger.warning("CHANNEL_MEDIA_PRIVATE_TEST_HANDLER active=on command=/test_channel_media admin_only=true public=false")

    bot.post_init = private_test_post_init
    _installed = True
