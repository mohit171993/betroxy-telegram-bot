"""BETROXY Quiz V2.1 engagement upgrade.

This overlay intentionally leaves the proven quiz mechanics unchanged:
- 7 questions
- 30 seconds per question
- ₹1,000 prize pool split ₹500 / ₹300 / ₹200
- accuracy -> hard-question accuracy -> speed ranking
- manual reward approval

It improves only presentation and measurement:
1) clearer completion screen with provisional/final rank language,
2) visible daily theme + direct @betroxyupdates CTA,
3) one admin-only daily funnel report after 21:10 IST.
"""
from __future__ import annotations

import html
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import bot
import daily_quiz_question_bank as bank
import daily_quiz_schedule as schedule
import v113_text_quiz_ux as quiz

v110 = quiz.v110

IST = timezone(timedelta(hours=5, minutes=30))
CHANNEL_HANDLE = "@betroxyupdates"
CHANNEL_URL = "https://t.me/betroxyupdates"
REPORT_HOUR = 21
REPORT_MINUTE = 10
REPORT_TABLE = "quiz_v21_admin_reports"

_installed = False
_original_result_text = None
_original_result_rows = None


def _ist_now():
    return datetime.now(timezone.utc).astimezone(IST)


def _ensure_schema():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {REPORT_TABLE} (
                    report_date DATE PRIMARY KEY,
                    campaign_id BIGINT,
                    sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
        conn.commit()


def _campaign_for_day(day):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM v110_quiz_campaigns
                WHERE campaign_date=%s AND test_mode=FALSE
                ORDER BY id DESC
                LIMIT 1
                """,
                (day,),
            )
            return cur.fetchone()


def _report_already_sent(day):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT 1 AS x FROM {REPORT_TABLE} WHERE report_date=%s", (day,))
            return bool(cur.fetchone())


def _mark_report_sent(day, campaign_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {REPORT_TABLE}(report_date,campaign_id)
                VALUES (%s,%s)
                ON CONFLICT(report_date) DO NOTHING
                """,
                (day, int(campaign_id)),
            )
        conn.commit()


def _analytics_snapshot(campaign_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) AS started,
                    COUNT(*) FILTER (WHERE completed_at IS NOT NULL) AS completed,
                    COUNT(*) FILTER (WHERE completed_at IS NOT NULL AND correct_count=7) AS perfect,
                    AVG(EXTRACT(EPOCH FROM (completed_at-started_at)))
                        FILTER (WHERE completed_at IS NOT NULL) AS avg_completion_seconds,
                    AVG(correct_count::numeric)
                        FILTER (WHERE completed_at IS NOT NULL) AS avg_score
                FROM v110_quiz_entries
                WHERE campaign_id=%s
                """,
                (int(campaign_id),),
            )
            totals = cur.fetchone() or {}

            cur.execute(
                """
                WITH incomplete AS (
                    SELECT
                        e.id,
                        COALESCE(MAX(q.seq),0) AS last_answered_seq
                    FROM v110_quiz_entries e
                    LEFT JOIN v110_quiz_answers a ON a.entry_id=e.id
                    LEFT JOIN v110_quiz_questions q ON q.id=a.question_id
                    WHERE e.campaign_id=%s
                      AND e.completed_at IS NULL
                    GROUP BY e.id
                )
                SELECT
                    LEAST(last_answered_seq + 1, 7) AS next_question,
                    COUNT(*) AS users
                FROM incomplete
                GROUP BY LEAST(last_answered_seq + 1, 7)
                ORDER BY users DESC, next_question ASC
                LIMIT 1
                """,
                (int(campaign_id),),
            )
            drop = cur.fetchone()

            cur.execute(
                """
                SELECT COUNT(*) AS answered
                FROM v110_quiz_answers a
                JOIN v110_quiz_entries e ON e.id=a.entry_id
                WHERE e.campaign_id=%s
                """,
                (int(campaign_id),),
            )
            answered = int((cur.fetchone() or {}).get("answered") or 0)

    started = int(totals.get("started") or 0)
    completed = int(totals.get("completed") or 0)
    perfect = int(totals.get("perfect") or 0)
    avg_seconds_raw = totals.get("avg_completion_seconds")
    avg_seconds = float(avg_seconds_raw) if avg_seconds_raw is not None else 0.0
    avg_score_raw = totals.get("avg_score")
    avg_score = float(avg_score_raw) if avg_score_raw is not None else 0.0
    return {
        "started": started,
        "completed": completed,
        "incomplete": max(0, started - completed),
        "perfect": perfect,
        "top3": min(3, completed),
        "answered": answered,
        "avg_completion_seconds": avg_seconds,
        "avg_score": avg_score,
        "drop_question": int(drop.get("next_question") or 0) if drop else 0,
        "drop_users": int(drop.get("users") or 0) if drop else 0,
    }


def _format_duration(seconds):
    seconds = max(0, int(round(float(seconds or 0))))
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}m {seconds:02d}s" if minutes else f"{seconds}s"


def _analytics_text(campaign, stats):
    started = stats["started"]
    completed = stats["completed"]
    completion_pct = (100.0 * completed / started) if started else 0.0
    theme = bank.theme_for_date(campaign["campaign_date"])
    if stats["drop_question"]:
        drop_text = f"Before Q{stats['drop_question']}: <b>{stats['drop_users']}</b> user(s)"
    else:
        drop_text = "No incomplete entries"

    return (
        "📊 <b>BETROXY DAILY QUIZ — PERFORMANCE REPORT</b>\n\n"
        f"Date: <b>{campaign['campaign_date']}</b>\n"
        f"Theme: <b>{html.escape(theme)}</b>\n\n"
        f"▶️ Started: <b>{started}</b>\n"
        f"✅ Completed: <b>{completed}</b> ({completion_pct:.1f}%)\n"
        f"🚪 Incomplete: <b>{stats['incomplete']}</b>\n"
        f"💯 Perfect 7/7: <b>{stats['perfect']}</b>\n"
        f"🏆 Top-3 positions filled: <b>{stats['top3']}/3</b>\n"
        f"🎯 Average score: <b>{stats['avg_score']:.2f}/7</b>\n"
        f"⏱ Average completion time: <b>{_format_duration(stats['avg_completion_seconds'])}</b>\n"
        f"📝 Total answers recorded: <b>{stats['answered']}</b>\n"
        f"📉 Largest drop-off: {drop_text}\n\n"
        "Use this report to improve themes/questions from actual user behaviour. "
        "Core rules remain unchanged: 7 questions, 30 seconds, ₹1,000 pool."
    )


def _send_daily_analytics(day):
    if _report_already_sent(day):
        return True
    campaign = _campaign_for_day(day)
    if not campaign:
        return False
    stats = _analytics_snapshot(campaign["id"])
    rows = [
        [{"text": "🏆 Review Leaderboard", "callback_data": f"v110_leaderboard:{int(campaign['id'])}"}],
        [{"text": "🎁 Review Daily Quiz Rewards", "callback_data": "dq_rewards_today"}],
    ]
    ok, data = quiz._tg_send_text(int(bot.ADMIN_ID), _analytics_text(campaign, stats), rows)
    if ok:
        _mark_report_sent(day, campaign["id"])
    bot.logger.warning(
        "QUIZ_V21_ANALYTICS_REPORT sent=%s date=%s campaign=%s started=%s completed=%s perfect=%s drop_q=%s drop_users=%s",
        ok, day, campaign.get("id"), stats["started"], stats["completed"], stats["perfect"],
        stats["drop_question"], stats["drop_users"],
    )
    if not ok:
        bot.logger.warning("QUIZ_V21_ANALYTICS_REPORT_DETAIL %s", data)
    return bool(ok)


def _analytics_worker():
    time.sleep(30)
    while True:
        try:
            local = _ist_now()
            due = (local.hour, local.minute) >= (REPORT_HOUR, REPORT_MINUTE)
            if due:
                _send_daily_analytics(local.date())
        except Exception:
            bot.logger.exception("QUIZ_V21_ANALYTICS_WORKER_FAILED")
        time.sleep(60)


def _install_result_experience():
    global _original_result_text, _original_result_rows
    main_mod = sys.modules.get("__main__")
    if main_mod is None:
        return False
    current_text = getattr(main_mod, "_result_text", None)
    current_rows = getattr(main_mod, "_result_rows", None)
    if not callable(current_text) or not callable(current_rows):
        bot.logger.warning("QUIZ_V21_RESULT_PATCH skipped reason=production_result_helpers_not_ready")
        return False

    _original_result_text = current_text
    _original_result_rows = current_rows

    def _v21_result_text(entry, rank, already=False):
        campaign = None
        try:
            campaign = v110._campaign(int(entry.get("campaign_id") or 0))
        except Exception:
            campaign = None
        announced = False
        if campaign:
            try:
                announced = bool(schedule._result_already_sent(int(campaign["id"])))
            except Exception:
                announced = False

        heading = "✅ <b>Today's challenge is already complete.</b>" if already else "🎉 <b>Challenge complete!</b>"
        score = int(entry.get("correct_count") or 0)
        rank_label = "Final rank" if announced else "Current provisional rank"
        lines = [heading, "", f"🎯 Score: <b>{score}/7</b>", f"🏆 {rank_label}: <b>#{int(rank)}</b>"]

        if campaign:
            theme = bank.theme_for_date(campaign["campaign_date"])
            lines += [f"🎨 Today's theme: <b>{html.escape(theme)}</b>"]

        if announced:
            lines += ["", "✅ <b>Final results have been announced.</b>"]
        else:
            lines += [
                "",
                "⏳ Your position can still move while other players finish.",
                "Final winners are announced at <b>21:05 IST</b>.",
            ]

        lines += [
            "",
            "Ranking: accuracy → hard-question accuracy → total answer time.",
            f"📢 Join <b>{CHANNEL_HANDLE}</b> for final winners and tomorrow's quiz update.",
        ]
        return "\n".join(lines)

    def _v21_result_rows(campaign):
        return [
            [{"text": "📢 JOIN BETROXY UPDATES", "url": CHANNEL_URL}],
            [{"text": "🏆 LIVE LEADERBOARD", "callback_data": f"v110_leaderboard:{int(campaign['id'])}"}],
            [{"text": "🚀 EXPLORE BETROXY", "url": v110.OPEN_APP_URL}],
        ]

    setattr(main_mod, "_result_text", _v21_result_text)
    setattr(main_mod, "_result_rows", _v21_result_rows)
    bot.logger.warning(
        "QUIZ_V21_RESULT_EXPERIENCE active=on provisional_rank=on final_rank_after_announcement=on "
        "theme_visible=on channel_cta=@betroxyupdates tomorrow_return_prompt=existing_v2"
    )
    return True


def install():
    global _installed
    if _installed:
        return
    _ensure_schema()
    patched = _install_result_experience()
    threading.Thread(target=_analytics_worker, name="betroxy-quiz-v21-analytics", daemon=True).start()
    _installed = True
    bot.logger.warning(
        "QUIZ_V21_UPGRADE active=on result_ux=%s daily_themes=preserved+visible analytics=21:10_IST "
        "admin_only_analytics=on customer_extra_dm=off core_rules_unchanged=7q/30s/₹1000/500-300-200/manual_approval",
        "on" if patched else "pending",
    )
