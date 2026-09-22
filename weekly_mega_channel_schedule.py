"""Additive public-channel schedule for the BETROXY Sunday Mega Quiz.

This module replaces only the NEW weekly Mega Quiz worker. It does not modify
or patch the locked Daily Quiz code, Daily Quiz banners, Daily Quiz schedule,
private reminder eligibility, or the 12/09 Closing branch.

Final weekly channel cadence (IST):
- Wednesday 19:30: approved midweek teaser banner, text fallback
- Friday 19:30: approved 2-days-to-go banner, text fallback
- Saturday 19:30: approved Mega preview banner
- Sunday 10:05: approved Mega open banner
- Sunday 16:05: approved Mega afternoon banner
- Sunday 19:05: approved Mega last-chance banner
- Sunday 21:10: approved Mega results banner
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, time as dtime, timedelta, timezone

import bot

WEDNESDAY_TEASER_AT = dtime(19, 30)
FRIDAY_REMINDER_AT = dtime(19, 30)
SATURDAY_PREVIEW_AT = dtime(19, 30)
SUNDAY_OPEN_POST_AT = dtime(10, 5)
SUNDAY_AFTERNOON_POST_AT = dtime(16, 5)
SUNDAY_LAST_CALL_POST_AT = dtime(19, 5)
SUNDAY_CLOSE_AT = dtime(21, 0)
SUNDAY_RESULT_AT = dtime(21, 10)

_installed = False
_weekly = None
_media = None
_midweek = None


def _rows():
    return [[{"text": "🔥 Open Sunday Mega Quiz", "url": _weekly.BOT_DEEPLINK}]]


def _midweek_text(kind, campaign):
    day = campaign["campaign_date"].strftime("%d %b")
    if kind == "midweek_teaser":
        return (
            "🔥 <b>THIS SUNDAY: BETROXY MEGA QUIZ</b>\n\n"
            f"Sunday {day} brings our <b>₹5,000 Amazon Pay Gift Voucher</b> challenge.\n\n"
            "🥇 ₹2,500 • 🥈 ₹1,500 • 🥉 ₹1,000\n"
            "10 questions • 30 seconds each • free entry\n\n"
            "⏰ Opens Sunday at <b>10:00 AM IST</b>."
        )
    return (
        "🏆 <b>₹5,000 SUNDAY MEGA QUIZ — 2 DAYS TO GO</b>\n\n"
        "Win <b>Amazon Pay Gift Vouchers</b> this Sunday.\n\n"
        "🥇 ₹2,500 • 🥈 ₹1,500 • 🥉 ₹1,000\n"
        "10 questions • 30 seconds each • free entry\n\n"
        "📅 Sunday • <b>10:00 AM–9:00 PM IST</b>."
    )


def _text_channel_post(campaign, delivery_type, text):
    if _weekly._delivery_exists(campaign["id"], _weekly.CHANNEL, delivery_type):
        return True
    ok, detail = _weekly.quiz._tg_send_text(_weekly.CHANNEL, text, _rows())
    if ok:
        _weekly._mark_delivery(campaign["id"], _weekly.CHANNEL, delivery_type)
    bot.logger.warning(
        "MEGA_WEEKLY_BUILDUP_POST type=%s mode=text_only sent=%s campaign=%s detail=%s",
        delivery_type, ok, campaign["id"], detail if not ok else "ok",
    )
    return bool(ok)


def _midweek_channel_post(campaign, delivery_type, asset_key, text):
    if _midweek is not None:
        return _midweek.channel_post(campaign, delivery_type, asset_key, text, _rows())
    return _text_channel_post(campaign, delivery_type, text)


def _run_weekly_channel_cycle(local, campaign):
    day = campaign["campaign_date"]
    local_day = local.date()
    local_time = local.time().replace(tzinfo=None)

    # Wednesday: four days before Sunday. Approved image when available.
    if local_day == day - timedelta(days=4) and local_time >= WEDNESDAY_TEASER_AT:
        _midweek_channel_post(
            campaign,
            "midweek_teaser",
            "mega_wednesday",
            _midweek_text("midweek_teaser", campaign),
        )
        return

    # Friday: two days before Sunday. Approved image when available.
    if local_day == day - timedelta(days=2) and local_time >= FRIDAY_REMINDER_AT:
        _midweek_channel_post(
            campaign,
            "friday_reminder",
            "mega_friday",
            _midweek_text("friday_reminder", campaign),
        )
        return

    # Saturday: approved dedicated "Tomorrow" preview banner.
    if local_day == day - timedelta(days=1) and local_time >= SATURDAY_PREVIEW_AT:
        _weekly._channel_post(campaign, "preview", _weekly._promo_text("preview", campaign))
        return

    if local_day != day:
        return

    # Stagger Sunday Mega posts five minutes after the locked Daily Quiz channel
    # schedule so both campaigns remain visible and never land simultaneously.
    if SUNDAY_OPEN_POST_AT <= local_time < SUNDAY_AFTERNOON_POST_AT:
        _weekly._channel_post(campaign, "open", _weekly._promo_text("open", campaign))
    elif SUNDAY_AFTERNOON_POST_AT <= local_time < SUNDAY_LAST_CALL_POST_AT:
        _weekly._channel_post(campaign, "reminder", _weekly._promo_text("reminder", campaign))
    elif SUNDAY_LAST_CALL_POST_AT <= local_time < SUNDAY_CLOSE_AT:
        _weekly._channel_post(campaign, "last_call", _weekly._promo_text("last_call", campaign))


def worker():
    bot.logger.warning(
        "MEGA_CHANNEL_SCHEDULE_WORKER start wed=19:30 fri=19:30 sat=19:30 "
        "sun=10:05/16:05/19:05 result=21:10_IST "
        "wed_fri=banner_if_approved+text_fallback approved_mega_banners=sat+sun daily_quiz_untouched=on"
    )
    while True:
        try:
            local = _weekly._ist_now()
            campaign = _weekly._ensure_campaign()
            _weekly._refresh_status(campaign)

            _run_weekly_channel_cycle(local, campaign)

            # Existing media-aware result publisher remains authoritative. It
            # uses the approved Mega results banner and preserves manual payout.
            _weekly._announce_due_results()

            # Reference-style admin alert policy: keep the original result-time
            # payout approval card, but suppress the +15m/+30m repeat nags.
        except Exception:
            bot.logger.exception("MEGA_CHANNEL_SCHEDULE_WORKER_FAILED")
        time.sleep(30)


def install(weekly_module, media_module):
    global _installed, _weekly, _media, _midweek
    _weekly = weekly_module
    _media = media_module
    if _installed:
        return

    # Add two explicitly assigned midweek media slots. This is weekly-only and
    # leaves the existing 5 weekly banners and all Daily Quiz assets untouched.
    try:
        _midweek = __import__("weekly_mega_midweek_banners")
        _midweek.install(_weekly, _media)
    except Exception:
        _midweek = None
        bot.logger.exception("MEGA_MIDWEEK_BANNERS_INSTALL_FAILED")

    # production.py starts weekly_module.worker later; replacing this attribute is
    # isolated to the new weekly feature and leaves all Daily Quiz workers intact.
    _weekly.worker = worker
    _weekly._staggered_channel_schedule_installed = True
    _installed = True

    bot.logger.warning(
        "MEGA_CHANNEL_SCHEDULE active=on wed=19:30_banner_if_approved fri=19:30_banner_if_approved "
        "sat=19:30_preview_banner sun_open=10:05 sun_afternoon=16:05 "
        "sun_last=19:05 sun_result=21:10_IST midweek_text_fallback=on daily_schedule=unchanged "
        "daily_banners=unchanged private_dm=unchanged"
    )

    # Weekly-only recovery tool. Bulk Telegram albums preserve an order but do not
    # semantically identify which image says Preview/Open/Last Chance/Results.
    # This visual remap wizard lets the admin correct the five already-uploaded
    # images without re-uploading anything or touching the Daily Quiz.
    try:
        remap = __import__("weekly_mega_banner_reassign")
        remap.install(_media, __import__(__name__))
    except Exception:
        bot.logger.exception("MEGA_BANNER_REMAP_INSTALL_FAILED")

    # Startup preview cards are intentionally disabled by the reference-style
    # admin alert policy. Manual preview/upload commands remain available.
    bot.logger.warning(
        "MEGA_ADMIN_TEST_POSTS startup_auto_send=off manual_preview_tools=on "
        "real_channel_delivery_unchanged=on"
    )
