"""Authoritative IST clock for the BETROXY Daily Quiz production schedule.

The legacy schedule module was originally written around Dubai time and later
mutated by the alert worker.  This installer makes the core close/result clock
itself authoritative in India Standard Time so every consumer (campaign close,
result announcement, admin reward gate and worker logging) uses the same clock.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone

import bot

IST_OFFSET_HOURS = 5.5
IST = timezone(timedelta(hours=5, minutes=30))

_installed = False


def _coerce_date(value):
    if value is None:
        return _ist_now().date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _ist_now():
    return datetime.now(timezone.utc).astimezone(IST)


def _day_bounds_ist(local_date=None):
    d = _coerce_date(local_date)
    close_local = datetime(d.year, d.month, d.day, 21, 0, 0, tzinfo=IST)
    result_local = datetime(d.year, d.month, d.day, 21, 5, 0, tzinfo=IST)
    return close_local.astimezone(timezone.utc), result_local.astimezone(timezone.utc)


def install(schedule, v110=None, v83=None):
    global _installed
    if _installed:
        return schedule.schedule_worker

    v110 = v110 or schedule.v110
    v83 = v83 or getattr(v110, "v83", None)

    # Keep the legacy attribute for compatibility, but make its value IST.
    schedule.DUBAI_OFFSET = IST_OFFSET_HOURS
    schedule._dubai_now = _ist_now
    schedule._day_bounds = _day_bounds_ist
    v110.TZ_OFFSET = IST_OFFSET_HOURS
    if v83 is not None:
        v83.TZ_OFFSET = IST_OFFSET_HOURS

    def schedule_worker_ist():
        bot.logger.warning(
            "QUIZ_SCHEDULE_WORKER start enabled=%s close=21:00 result=21:05 "
            "tz=Asia/Kolkata question_seconds=30 result_channel=%s auto_rewards=%s exclusions=%s",
            schedule.SCHEDULE_ENABLED,
            schedule.RESULT_CHANNEL_ENABLED,
            schedule.AUTO_REWARDS_ENABLED,
            sorted(schedule.PRIZE_EXCLUDED_USERNAMES),
        )
        while True:
            try:
                if schedule.SCHEDULE_ENABLED:
                    schedule._today_campaign_windowed(test_mode=False)
                    schedule._announce_if_due()
            except Exception:
                bot.logger.exception("QUIZ_SCHEDULE_WORKER_FAILED")
            time.sleep(30)

    schedule.schedule_worker = schedule_worker_ist
    _installed = True
    bot.logger.warning(
        "QUIZ_TIMEZONE_AUTHORITY active=on timezone=Asia/Kolkata offset=UTC+05:30 "
        "close=21:00_IST result=21:05_IST legacy_dubai_clock=off"
    )
    return schedule_worker_ist
