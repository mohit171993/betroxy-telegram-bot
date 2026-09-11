"""Staged daily quiz schedule for BETROXY.

Policy:
- one daily attempt per Telegram user (existing DB unique constraint)
- participation any time until 21:00 Asia/Dubai
- 30 seconds per question
- visible countdown refreshes at 20s, 10s and 5s remaining
- live leaderboard is provisional
- final results at 21:05 Asia/Dubai
- winners: #1 ₹500, #2 ₹300, #3 ₹200
- configured test accounts can participate but are excluded from prize ranking
- GiftPort operator GPAPGV, auto reward issuance separately gated
"""
import html
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import requests

import bot
import v113_text_quiz_ux as quiz

v110 = quiz.v110
v97 = v110.v97

DUBAI_OFFSET = 4
QUESTION_SECONDS = 30
CLOSE_HOUR = 21
RESULT_MINUTE = 5
SCHEDULE_ENABLED = os.getenv("QUIZ_SCHEDULE_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
AUTO_REWARDS_ENABLED = os.getenv("QUIZ_AUTO_REWARDS_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
RESULT_CHANNEL_ENABLED = os.getenv("QUIZ_RESULT_CHANNEL_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
REWARD_OPERATOR = os.getenv("QUIZ_REWARD_OPERATOR", "GPAPGV").strip() or "GPAPGV"

# Test/admin accounts may still play and appear on the provisional leaderboard,
# but they never consume a paid prize position in the final ranking.
_raw_excluded_usernames = os.getenv("QUIZ_PRIZE_EXCLUDED_USERNAMES", "mohit_97saxena")
PRIZE_EXCLUDED_USERNAMES = {
    x.strip().lstrip("@").lower()
    for x in _raw_excluded_usernames.split(",")
    if x.strip()
}
_raw_excluded_ids = os.getenv("QUIZ_PRIZE_EXCLUDED_USER_IDS", "")
PRIZE_EXCLUDED_USER_IDS = {
    int(x.strip()) for x in _raw_excluded_ids.split(",")
    if x.strip().isdigit()
}

# Make all answer validation use 30 seconds.
v110.QUESTION_SECONDS = QUESTION_SECONDS
quiz.v110.QUESTION_SECONDS = QUESTION_SECONDS


def _dubai_now():
    return datetime.now(timezone.utc) + timedelta(hours=DUBAI_OFFSET)


def _day_bounds(local_date=None):
    now = _dubai_now()
    d = local_date or now.date()
    close_local = datetime(d.year, d.month, d.day, CLOSE_HOUR, 0, 0, tzinfo=timezone.utc)
    result_local = datetime(d.year, d.month, d.day, CLOSE_HOUR, RESULT_MINUTE, 0, tzinfo=timezone.utc)
    return close_local - timedelta(hours=DUBAI_OFFSET), result_local - timedelta(hours=DUBAI_OFFSET)


def _question_text(question, seq, remaining=QUESTION_SECONDS):
    return (
        f"🏆 <b>BETROXY DAILY CHALLENGE</b>\n\n"
        f"<b>Question {seq}/7</b>  •  {html.escape(str(question['sport']))}\n"
        f"{quiz._difficulty_label(question)}\n\n"
        f"❓ <b>{html.escape(str(question['question']))}</b>\n\n"
        f"⏱ <b>{remaining} seconds remaining</b>\n"
        "Tap one answer below."
    )


def _edit_question(uid, message_id, question, seq, remaining):
    payload = {
        "chat_id": str(uid),
        "message_id": str(message_id),
        "text": _question_text(question, seq, remaining),
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": json.dumps({"inline_keyboard": v110._question_buttons(uid, question)}, separators=(",", ":")),
    }
    try:
        requests.post(f"{v110.TG_API}/editMessageText", data=payload, timeout=20)
    except Exception:
        bot.logger.exception("QUIZ_COUNTDOWN_EDIT_FAILED uid=%s qid=%s remaining=%s", uid, question.get("id"), remaining)


def _countdown_worker(uid, message_id, question, seq):
    started = time.monotonic()
    for elapsed, remaining in ((10, 20), (20, 10), (25, 5)):
        delay = elapsed - (time.monotonic() - started)
        if delay > 0:
            time.sleep(delay)
        try:
            session = v110._session(uid)
            if int(session.get("current_question_id") or 0) != int(question["id"]):
                return
            _edit_question(uid, message_id, question, seq, remaining)
        except Exception:
            bot.logger.exception("QUIZ_COUNTDOWN_WORKER_FAILED uid=%s", uid)
            return


async def _send_question_30s(uid, campaign, entry, question):
    seq = int(question["seq"])
    ok, data = quiz._tg_send_text(uid, _question_text(question, seq, QUESTION_SECONDS), v110._question_buttons(uid, question))
    if ok:
        result = (data.get("result") or {}) if isinstance(data, dict) else {}
        message_id = result.get("message_id")
        v110._set_session(
            uid,
            campaign_id=campaign["id"],
            entry_id=entry["id"],
            current_question_id=question["id"],
            question_sent_at=datetime.now(timezone.utc),
            flow_state="in_quiz",
        )
        if message_id:
            threading.Thread(
                target=_countdown_worker,
                args=(int(uid), int(message_id), question, seq),
                name=f"quiz-countdown-{uid}-{question['id']}",
                daemon=True,
            ).start()
        bot.logger.warning("QUIZ_30S_QUESTION_SENT uid=%s campaign=%s seq=%s", uid, campaign["id"], seq)
    return ok


quiz._send_question_text = _send_question_30s
v110._send_question_to_user = _send_question_30s

_original_today_campaign = v110._today_campaign


def _today_campaign_windowed(test_mode=False):
    campaign = _original_today_campaign(test_mode=test_mode)
    if test_mode:
        return campaign
    close_utc, _ = _day_bounds(campaign.get("campaign_date"))
    now_utc = datetime.now(timezone.utc)
    desired_status = "closed" if now_utc >= close_utc else "open"
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE v110_quiz_campaigns SET closes_at=%s, status=%s WHERE id=%s RETURNING *",
                (close_utc, desired_status, int(campaign["id"])),
            )
            campaign = cur.fetchone()
        conn.commit()
    return campaign


v110._today_campaign = _today_campaign_windowed


def _send_text(chat_id, text, rows=None):
    payload = {"chat_id": str(chat_id), "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if rows:
        payload["reply_markup"] = json.dumps({"inline_keyboard": rows}, separators=(",", ":"))
    r = requests.post(f"{v110.TG_API}/sendMessage", data=payload, timeout=25)
    data = r.json() if r.content else {}
    return bool(r.ok and data.get("ok")), data


def _prize_eligible(row):
    try:
        uid = int(row.get("telegram_user_id") or 0)
    except Exception:
        uid = 0
    username = str(row.get("telegram_username") or "").strip().lstrip("@").lower()
    if uid and uid in PRIZE_EXCLUDED_USER_IDS:
        return False
    if username and username in PRIZE_EXCLUDED_USERNAMES:
        return False
    return True


def _final_rows(campaign_id):
    # Pull extra rows so excluded test/admin accounts do not reduce the number
    # of actual prize winners. Provisional/live leaderboard behavior is unchanged.
    ranked = v110._leaderboard(campaign_id, limit=100)
    eligible = []
    excluded = []
    for row in ranked:
        if _prize_eligible(row):
            eligible.append(row)
            if len(eligible) >= 3:
                break
        else:
            excluded.append(row)
    if excluded:
        bot.logger.warning(
            "QUIZ_PRIZE_EXCLUSIONS campaign=%s excluded=%s",
            campaign_id,
            [(r.get("telegram_user_id"), r.get("telegram_username")) for r in excluded],
        )
    return eligible


def _result_key(campaign):
    return f"final_result:{campaign['campaign_date']}"


def _result_already_sent(campaign_id):
    return v110._delivery_exists(campaign_id, v110.CHANNEL_CHAT, "final_result")


def _winner_text(rows):
    medals = ["🥇", "🥈", "🥉"]
    prizes = [500, 300, 200]
    lines = ["🏆 <b>BETROXY DAILY CHALLENGE — FINAL RESULTS</b>", ""]
    if not rows:
        lines.append("No eligible completed entries today.")
    else:
        for i, row in enumerate(rows[:3]):
            name = row.get("telegram_username") or f"Player {str(row.get('telegram_user_id'))[-4:]}"
            lines.append(f"{medals[i]} <b>{html.escape(str(name))}</b> — {int(row.get('correct_count') or 0)}/7 — ₹{prizes[i]}")
    lines += ["", "Final prize ranking: accuracy → hard-question accuracy → total answer time."]
    return "\n".join(lines)


def _ensure_award(campaign, row, rank, amount):
    period_key = f"daily_quiz:{campaign['campaign_date']}"
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO reward_awards(
                    telegram_user_id,reward_type,period_key,rank,points,amount,currency,brand_code,provider,status
                ) VALUES (%s,'daily_quiz',%s,%s,%s,%s,'INR',%s,'giftport','queued')
                ON CONFLICT(reward_type,period_key,rank) DO NOTHING""",
                (int(row["telegram_user_id"]), period_key, int(rank), int(row.get("points") or 0), int(amount), REWARD_OPERATOR),
            )
            cur.execute(
                "SELECT * FROM reward_awards WHERE reward_type='daily_quiz' AND period_key=%s AND rank=%s LIMIT 1",
                (period_key, int(rank)),
            )
            award = cur.fetchone()
        conn.commit()
    return award


def _issue_winner_rewards(campaign, rows):
    for rank, amount in ((1, 500), (2, 300), (3, 200)):
        if len(rows) < rank:
            continue
        award = _ensure_award(campaign, rows[rank - 1], rank, amount)
        if not award:
            continue
        if not AUTO_REWARDS_ENABLED:
            bot.logger.warning("QUIZ_REWARD_STAGED rank=%s amount=%s award=%s operator=%s", rank, amount, award.get("id"), REWARD_OPERATOR)
            continue
        status = str(award.get("status") or "")
        if status in {"delivered", "issued", "delivery_pending"} or int(award.get("issue_attempts") or 0) > 0:
            continue
        v97._issue_award(award)


def _announce_if_due():
    local = _dubai_now()
    campaign = _today_campaign_windowed(test_mode=False)
    close_utc, result_utc = _day_bounds(campaign.get("campaign_date"))
    now_utc = datetime.now(timezone.utc)
    if now_utc < result_utc:
        return False
    if _result_already_sent(campaign["id"]):
        return False
    rows = _final_rows(campaign["id"])
    _issue_winner_rewards(campaign, rows)
    if RESULT_CHANNEL_ENABLED:
        ok, data = _send_text(v110.CHANNEL_CHAT, _winner_text(rows))
        if ok:
            mid = ((data.get("result") or {}).get("message_id")) if isinstance(data, dict) else None
            v110._mark_delivery(campaign["id"], v110.CHANNEL_CHAT, "final_result", mid)
        bot.logger.warning("QUIZ_FINAL_RESULT sent=%s campaign=%s rows=%s local=%s", ok, campaign["id"], len(rows), local.isoformat())
        return ok
    bot.logger.warning("QUIZ_FINAL_RESULT_DRYRUN campaign=%s winners=%s result_channel=OFF auto_rewards=%s", campaign["id"], len(rows), AUTO_REWARDS_ENABLED)
    return False


def schedule_worker():
    bot.logger.warning(
        "QUIZ_SCHEDULE_WORKER start enabled=%s close=21:00 result=21:05 tz=Asia/Dubai question_seconds=30 result_channel=%s auto_rewards=%s exclusions=%s",
        SCHEDULE_ENABLED, RESULT_CHANNEL_ENABLED, AUTO_REWARDS_ENABLED,
        sorted(PRIZE_EXCLUDED_USERNAMES),
    )
    while True:
        try:
            if SCHEDULE_ENABLED:
                _today_campaign_windowed(test_mode=False)
                _announce_if_due()
        except Exception:
            bot.logger.exception("QUIZ_SCHEDULE_WORKER_FAILED")
        time.sleep(30)


bot.logger.warning(
    "QUIZ_SCHEDULE_STAGED live=%s all_day_until=21:00 result=21:05 question_seconds=30 countdown=20/10/5 operator=%s prize_exclusions=%s",
    SCHEDULE_ENABLED, REWARD_OPERATOR, sorted(PRIZE_EXCLUDED_USERNAMES),
)
