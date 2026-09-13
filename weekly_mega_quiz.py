"""BETROXY Sunday Mega Quiz.

Separate from the proven daily quiz:
- every Sunday, 10:00-21:00 IST
- 10 questions, 30 seconds each
- ₹5,000 pool: ₹2,500 / ₹1,500 / ₹1,000
- ranking: accuracy -> hard-question accuracy -> total answer time
- free entry, one attempt
- public-channel promotion only; no proactive private-DM campaign
- results 21:10 IST
- manual admin payout approval
- GiftPort operator GP298 (PhonePe eGift); fixed-denomination split handled by
  reward_code_display_fix installed before this module.
"""
from __future__ import annotations

import hashlib
import html
import json
import random
import re
import sys
import threading
import time
from datetime import date, datetime, time as dtime, timedelta, timezone

import bot
import daily_quiz_question_bank as bank
import daily_quiz_schedule as daily_schedule
import v113_text_quiz_ux as quiz

v110 = quiz.v110
v97 = v110.v97

IST = timezone(timedelta(hours=5, minutes=30))
CHANNEL = "@betroxyupdates"
BOT_DEEPLINK = f"https://t.me/{bot.BOT_USERNAME}?start=megaquiz"
QUESTION_SECONDS = 30
QUESTION_COUNT = 10
PRIZES = (2500, 1500, 1000)
PRIZE_POOL = sum(PRIZES)
REWARD_OPERATOR = "GP298"
OPEN_AT = dtime(10, 0)
CLOSE_AT = dtime(21, 0)
RESULT_AT = dtime(21, 10)
PROMO_PREVIEW_AT = dtime(19, 30)  # Saturday
PROMO_REMINDER_AT = dtime(17, 0)  # Sunday
PROMO_LAST_CALL_AT = dtime(20, 30)

_installed = False
_previous_callback = None
_previous_chat_handler = None
_previous_start = None


def _ist_now():
    return datetime.now(timezone.utc).astimezone(IST)


def _utc_for(day: date, clock: dtime):
    return datetime.combine(day, clock, tzinfo=IST).astimezone(timezone.utc)


def _next_sunday(now=None):
    now = now or _ist_now()
    today = now.date()
    days = (6 - today.weekday()) % 7
    candidate = today + timedelta(days=days)
    if days == 0 and now.timetz().replace(tzinfo=None) >= RESULT_AT:
        candidate += timedelta(days=7)
    return candidate


def _ensure_schema():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS mega_quiz_campaigns (
                    id BIGSERIAL PRIMARY KEY,
                    campaign_date DATE UNIQUE NOT NULL,
                    title TEXT NOT NULL,
                    prize_pool INTEGER NOT NULL DEFAULT 5000,
                    opens_at TIMESTAMPTZ NOT NULL,
                    closes_at TIMESTAMPTZ NOT NULL,
                    result_at TIMESTAMPTZ NOT NULL,
                    status TEXT NOT NULL DEFAULT 'upcoming',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS mega_quiz_questions (
                    id BIGSERIAL PRIMARY KEY,
                    campaign_id BIGINT NOT NULL REFERENCES mega_quiz_campaigns(id) ON DELETE CASCADE,
                    seq INTEGER NOT NULL,
                    difficulty INTEGER NOT NULL,
                    sport TEXT NOT NULL,
                    question TEXT NOT NULL,
                    options_json TEXT NOT NULL,
                    correct_option INTEGER NOT NULL,
                    UNIQUE(campaign_id, seq)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS mega_quiz_entries (
                    id BIGSERIAL PRIMARY KEY,
                    campaign_id BIGINT NOT NULL REFERENCES mega_quiz_campaigns(id) ON DELETE CASCADE,
                    telegram_user_id BIGINT NOT NULL,
                    telegram_username TEXT,
                    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    completed_at TIMESTAMPTZ,
                    correct_count INTEGER NOT NULL DEFAULT 0,
                    hard_correct INTEGER NOT NULL DEFAULT 0,
                    total_answer_ms BIGINT NOT NULL DEFAULT 0,
                    UNIQUE(campaign_id, telegram_user_id)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS mega_quiz_answers (
                    id BIGSERIAL PRIMARY KEY,
                    entry_id BIGINT NOT NULL REFERENCES mega_quiz_entries(id) ON DELETE CASCADE,
                    question_id BIGINT NOT NULL REFERENCES mega_quiz_questions(id) ON DELETE CASCADE,
                    selected_option INTEGER NOT NULL,
                    is_correct BOOLEAN NOT NULL,
                    difficulty INTEGER NOT NULL,
                    answer_ms INTEGER NOT NULL,
                    answered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(entry_id, question_id)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS mega_quiz_sessions (
                    telegram_user_id BIGINT PRIMARY KEY,
                    campaign_id BIGINT,
                    entry_id BIGINT,
                    current_question_id BIGINT,
                    question_sent_at TIMESTAMPTZ,
                    flow_state TEXT,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS mega_quiz_deliveries (
                    id BIGSERIAL PRIMARY KEY,
                    campaign_id BIGINT NOT NULL REFERENCES mega_quiz_campaigns(id) ON DELETE CASCADE,
                    target TEXT NOT NULL,
                    delivery_type TEXT NOT NULL,
                    sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(campaign_id, target, delivery_type)
                )
            """)
        conn.commit()


def _session(uid):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM mega_quiz_sessions WHERE telegram_user_id=%s", (int(uid),))
            return cur.fetchone() or {}


def _set_session(uid, **fields):
    allowed = {"campaign_id", "entry_id", "current_question_id", "question_sent_at", "flow_state"}
    clean = {k: v for k, v in fields.items() if k in allowed}
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO mega_quiz_sessions(telegram_user_id) VALUES (%s) ON CONFLICT(telegram_user_id) DO NOTHING", (int(uid),))
            if clean:
                sets = [f"{k}=%s" for k in clean] + ["updated_at=NOW()"]
                cur.execute(
                    f"UPDATE mega_quiz_sessions SET {', '.join(sets)} WHERE telegram_user_id=%s",
                    tuple(clean.values()) + (int(uid),),
                )
        conn.commit()


def _campaign_by_id(campaign_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM mega_quiz_campaigns WHERE id=%s", (int(campaign_id),))
            return cur.fetchone()


def _campaign_for_date(day):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM mega_quiz_campaigns WHERE campaign_date=%s LIMIT 1", (day,))
            return cur.fetchone()


def _daily_question_texts(day):
    try:
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT q.question
                    FROM v110_quiz_questions q
                    JOIN v110_quiz_campaigns c ON c.id=q.campaign_id
                    WHERE c.campaign_date=%s AND c.test_mode=FALSE
                """, (day,))
                return {str(r["question"]) for r in cur.fetchall()}
    except Exception:
        return set()


def _recent_mega_question_texts(day, weeks=8):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT q.question
                FROM mega_quiz_questions q
                JOIN mega_quiz_campaigns c ON c.id=q.campaign_id
                WHERE c.campaign_date < %s AND c.campaign_date >= %s
            """, (day, day - timedelta(days=7 * weeks)))
            return {str(r["question"]) for r in cur.fetchall()}


def _select_questions(day):
    excluded = _daily_question_texts(day) | _recent_mega_question_texts(day)
    seed = int(hashlib.sha256(f"betroxy-mega:{day.isoformat()}".encode()).hexdigest()[:16], 16)
    rng = random.Random(seed)
    plan = {1: 2, 2: 4, 3: 4}
    selected = []
    for difficulty, count in plan.items():
        pool = [q for q in bank.QUESTION_BANK if int(q["difficulty"]) == difficulty and str(q["question"]) not in excluded]
        if len(pool) < count:
            pool = [q for q in bank.QUESTION_BANK if int(q["difficulty"]) == difficulty]
        chosen = rng.sample(pool, count)
        selected.extend(chosen)
    return selected


def _seed_questions(campaign):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM mega_quiz_questions WHERE campaign_id=%s", (int(campaign["id"]),))
            if int((cur.fetchone() or {}).get("n") or 0) >= QUESTION_COUNT:
                return
        conn.commit()
    chosen = _select_questions(campaign["campaign_date"])
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            for seq, item in enumerate(chosen, 1):
                cur.execute("""
                    INSERT INTO mega_quiz_questions(campaign_id,seq,difficulty,sport,question,options_json,correct_option)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(campaign_id,seq) DO NOTHING
                """, (
                    int(campaign["id"]), seq, int(item["difficulty"]), str(item["sport"]),
                    str(item["question"]), json.dumps(item["options"]), int(item["correct"]),
                ))
        conn.commit()
    bot.logger.warning("MEGA_QUIZ_QUESTIONS seeded campaign=%s date=%s mix=2easy/4medium/4hard count=10", campaign["id"], campaign["campaign_date"])


def _ensure_campaign(day=None):
    day = day or _next_sunday()
    campaign = _campaign_for_date(day)
    if not campaign:
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO mega_quiz_campaigns(campaign_date,title,prize_pool,opens_at,closes_at,result_at,status)
                    VALUES (%s,%s,%s,%s,%s,%s,'upcoming')
                    ON CONFLICT(campaign_date) DO UPDATE SET title=EXCLUDED.title
                    RETURNING *
                """, (
                    day, "BETROXY Sunday Mega Quiz", PRIZE_POOL,
                    _utc_for(day, OPEN_AT), _utc_for(day, CLOSE_AT), _utc_for(day, RESULT_AT),
                ))
                campaign = cur.fetchone()
            conn.commit()
    _seed_questions(campaign)
    return campaign


def _refresh_status(campaign):
    now = datetime.now(timezone.utc)
    if now < campaign["opens_at"]:
        status = "upcoming"
    elif now < campaign["closes_at"]:
        status = "open"
    else:
        status = "closed"
    if str(campaign.get("status") or "") != status:
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE mega_quiz_campaigns SET status=%s WHERE id=%s RETURNING *", (status, int(campaign["id"])))
                campaign = cur.fetchone()
            conn.commit()
    return campaign


def _entry(campaign_id, uid, username=None):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO mega_quiz_entries(campaign_id,telegram_user_id,telegram_username)
                VALUES (%s,%s,%s)
                ON CONFLICT(campaign_id,telegram_user_id) DO UPDATE SET
                    telegram_username=COALESCE(EXCLUDED.telegram_username,mega_quiz_entries.telegram_username)
                RETURNING *
            """, (int(campaign_id), int(uid), username))
            row = cur.fetchone()
        conn.commit()
    return row


def _entry_by_id(entry_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM mega_quiz_entries WHERE id=%s", (int(entry_id),))
            return cur.fetchone()


def _next_question(entry_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT q.*
                FROM mega_quiz_questions q
                JOIN mega_quiz_entries e ON e.campaign_id=q.campaign_id
                WHERE e.id=%s
                  AND NOT EXISTS(
                      SELECT 1 FROM mega_quiz_answers a
                      WHERE a.entry_id=e.id AND a.question_id=q.id
                  )
                ORDER BY q.seq
                LIMIT 1
            """, (int(entry_id),))
            return cur.fetchone()


def _leaderboard(campaign_id, limit=10):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT * FROM mega_quiz_entries
                WHERE campaign_id=%s AND completed_at IS NOT NULL
                ORDER BY correct_count DESC, hard_correct DESC, total_answer_ms ASC, completed_at ASC
                LIMIT %s
            """, (int(campaign_id), int(limit)))
            return cur.fetchall()


def _rank(campaign_id, entry_id):
    rows = _leaderboard(campaign_id, 10000)
    for i, row in enumerate(rows, 1):
        if int(row["id"]) == int(entry_id):
            return i
    return max(1, len(rows) + 1)


def _final_rows(campaign_id):
    out = []
    for row in _leaderboard(campaign_id, 100):
        if daily_schedule._prize_eligible(row):
            out.append(row)
            if len(out) >= 3:
                break
    return out


def _buttons(uid, question):
    options = json.loads(question["options_json"])
    order = list(range(len(options)))
    seed = int(hashlib.sha256(f"mega:{uid}:{question['id']}".encode()).hexdigest()[:8], 16)
    random.Random(seed).shuffle(order)
    return [[{"text": options[idx], "callback_data": f"mega_answer:{question['id']}:{idx}"}] for idx in order]


def _difficulty_label(question):
    return {1: "🟢 EASY", 2: "🟡 MEDIUM", 3: "🔴 HARD"}.get(int(question.get("difficulty") or 1), "QUIZ")


def _question_text(question):
    seq = int(question["seq"])
    return (
        "🔥 <b>BETROXY SUNDAY MEGA QUIZ</b>\n\n"
        f"<b>Question {seq}/{QUESTION_COUNT}</b> • {html.escape(str(question['sport']))}\n"
        f"{_difficulty_label(question)}\n\n"
        f"❓ <b>{html.escape(str(question['question']))}</b>\n\n"
        f"⏱ <b>{QUESTION_SECONDS} seconds</b>\n"
        "Tap one answer below."
    )


async def _send_question(uid, campaign, entry, question):
    ok, data = quiz._tg_send_text(uid, _question_text(question), _buttons(uid, question))
    if ok:
        _set_session(
            uid,
            campaign_id=campaign["id"],
            entry_id=entry["id"],
            current_question_id=question["id"],
            question_sent_at=datetime.now(timezone.utc),
            flow_state="in_quiz",
        )
        bot.logger.warning("MEGA_QUIZ_QUESTION_SENT uid=%s campaign=%s seq=%s", uid, campaign["id"], question["seq"])
    else:
        bot.logger.warning("MEGA_QUIZ_QUESTION_FAILED uid=%s campaign=%s detail=%s", uid, campaign["id"], data)
    return ok


def _result_is_final(campaign_id):
    return _delivery_exists(campaign_id, CHANNEL, "final_result")


def _completion_text(campaign, entry, rank, already=False):
    final = _result_is_final(campaign["id"])
    rank_label = "Final rank" if final else "Current provisional rank"
    heading = "✅ <b>Mega Quiz already completed</b>" if already else "🔥 <b>MEGA QUIZ COMPLETE!</b>"
    return (
        f"{heading}\n\n"
        f"Score: <b>{int(entry.get('correct_count') or 0)}/{QUESTION_COUNT}</b>\n"
        f"{rank_label}: <b>#{rank}</b>\n\n"
        "🏆 <b>₹5,000 Prize Pool</b>\n"
        "🥇 ₹2,500 • 🥈 ₹1,500 • 🥉 ₹1,000\n\n"
        "Ranking: accuracy → hard-question accuracy → total answer time.\n"
        "📢 Final results: <b>21:10 IST</b>."
    )


def _completion_rows(campaign):
    return [
        [{"text": "🏆 Mega Leaderboard", "callback_data": f"mega_leaderboard:{campaign['id']}"}],
        [{"text": "📢 Join BETROXY Updates", "url": "https://t.me/betroxyupdates"}],
        [{"text": "🚀 Open BETROXY", "url": v110.OPEN_APP_URL}],
    ]


async def _finish(uid, campaign, entry):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE mega_quiz_entries SET completed_at=COALESCE(completed_at,NOW()) WHERE id=%s RETURNING *", (int(entry["id"]),))
            final = cur.fetchone()
        conn.commit()
    rank = _rank(campaign["id"], final["id"])
    quiz._tg_send_text(uid, _completion_text(campaign, final, rank), _completion_rows(campaign))
    _set_session(uid, flow_state="complete", current_question_id=None, question_sent_at=None)
    bot.logger.warning("MEGA_QUIZ_COMPLETE uid=%s campaign=%s score=%s rank=%s", uid, campaign["id"], final["correct_count"], rank)


async def _start_quiz(uid, username, campaign):
    campaign = _refresh_status(campaign)
    if campaign["status"] != "open":
        return False
    entry = _entry(campaign["id"], uid, username)
    if entry.get("completed_at"):
        rank = _rank(campaign["id"], entry["id"])
        quiz._tg_send_text(uid, _completion_text(campaign, entry, rank, already=True), _completion_rows(campaign))
        return True
    q = _next_question(entry["id"])
    if q:
        return await _send_question(uid, campaign, entry, q)
    return False


def _home_text(campaign):
    campaign = _refresh_status(campaign)
    day_label = campaign["campaign_date"].strftime("%d %b %Y")
    if campaign["status"] == "open":
        status = "🟢 <b>OPEN NOW</b> — closes 21:00 IST"
    elif campaign["status"] == "closed":
        status = "🔒 <b>Closed</b> — final results at 21:10 IST"
    else:
        status = f"⏳ Opens <b>Sunday {day_label} at 10:00 IST</b>"
    return (
        "🔥 <b>BETROXY SUNDAY MEGA QUIZ</b>\n\n"
        f"{status}\n\n"
        "🎁 <b>₹5,000 Prize Pool</b>\n"
        "🥇 1st — ₹2,500\n"
        "🥈 2nd — ₹1,500\n"
        "🥉 3rd — ₹1,000\n\n"
        "10 questions • 30 seconds each • one attempt\n"
        "Accuracy first; hard-question accuracy and speed break ties.\n"
        "💯 Free entry — no deposit or wager required."
    )


def _home_rows(campaign):
    campaign = _refresh_status(campaign)
    rows = []
    if campaign["status"] == "open":
        rows.append([{"text": "🔥 PLAY ₹5,000 MEGA QUIZ", "callback_data": f"mega_join:{campaign['id']}"}])
    rows += [
        [{"text": "📢 BETROXY Updates", "url": "https://t.me/betroxyupdates"}],
        [{"text": "🚀 Open BETROXY", "url": v110.OPEN_APP_URL}],
    ]
    return rows


async def _show_home_message(msg, uid):
    campaign = _ensure_campaign()
    await msg.reply_text(
        _home_text(campaign),
        parse_mode=bot.ParseMode.HTML,
        reply_markup=bot.InlineKeyboardMarkup([
            [bot.InlineKeyboardButton(b["text"], callback_data=b.get("callback_data"), url=b.get("url")) for b in row]
            for row in _home_rows(campaign)
        ]),
    )


def _consent_markup(campaign_id):
    return bot.InlineKeyboardMarkup([
        [bot.InlineKeyboardButton("✅ AGREE & START MEGA QUIZ", callback_data=f"mega_consent:{campaign_id}")],
        [bot.InlineKeyboardButton("Not now", callback_data="home")],
    ])


async def _registration_prompt(msg, uid, campaign_id):
    _set_session(uid, campaign_id=int(campaign_id), flow_state="register", entry_id=None, current_question_id=None, question_sent_at=None)
    await msg.reply_text(
        "📱 <b>Mega Quiz Registration</b>\n\nEnter your <b>10-digit Indian mobile number</b>.\nExample: <code>9876543210</code>\n\nWe add +91 automatically.",
        parse_mode=bot.ParseMode.HTML,
    )


async def _consent_prompt(msg, uid, campaign_id):
    await msg.reply_text(
        "✅ <b>Final step</b>\n\nBy continuing, you agree to participate in the free BETROXY Mega Quiz and to receive BETROXY quiz/reward updates. You can change notification preferences later.\n\nRanking is skill-based: accuracy first, then hard-question accuracy, then speed.",
        parse_mode=bot.ParseMode.HTML,
        reply_markup=_consent_markup(campaign_id),
    )


def _leaderboard_text(campaign):
    rows = _leaderboard(campaign["id"], 10)
    lines = [
        "🔥 <b>BETROXY SUNDAY MEGA QUIZ — LEADERBOARD</b>",
        "",
        "₹5,000 pool • Top 3 win",
        "Accuracy → hard questions → speed",
        "",
    ]
    if not rows:
        lines.append("No completed entries yet.")
    else:
        medals = ["🥇", "🥈", "🥉"]
        for i, row in enumerate(rows, 1):
            icon = medals[i - 1] if i <= 3 else f"#{i}"
            name = str(row.get("telegram_username") or f"Player {str(row.get('telegram_user_id') or '')[-4:]}")
            lines.append(f"{icon} <b>{html.escape(name)}</b> — {int(row.get('correct_count') or 0)}/{QUESTION_COUNT}")
    if not _result_is_final(campaign["id"]):
        lines += ["", "⏳ Rankings are provisional until 21:10 IST."]
    return "\n".join(lines)


def _delivery_exists(campaign_id, target, delivery_type):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 AS x FROM mega_quiz_deliveries WHERE campaign_id=%s AND target=%s AND delivery_type=%s", (int(campaign_id), str(target), str(delivery_type)))
            return bool(cur.fetchone())


def _mark_delivery(campaign_id, target, delivery_type):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO mega_quiz_deliveries(campaign_id,target,delivery_type)
                VALUES (%s,%s,%s)
                ON CONFLICT(campaign_id,target,delivery_type) DO NOTHING
            """, (int(campaign_id), str(target), str(delivery_type)))
        conn.commit()


def _channel_post(campaign, delivery_type, text):
    if _delivery_exists(campaign["id"], CHANNEL, delivery_type):
        return True
    rows = [[{"text": "🔥 Open Mega Quiz", "url": BOT_DEEPLINK}]]
    ok, data = quiz._tg_send_text(CHANNEL, text, rows)
    if ok:
        _mark_delivery(campaign["id"], CHANNEL, delivery_type)
    bot.logger.warning("MEGA_QUIZ_CHANNEL_POST type=%s sent=%s campaign=%s", delivery_type, ok, campaign["id"])
    return bool(ok)


def _promo_text(kind, campaign):
    day = campaign["campaign_date"].strftime("%d %b")
    common = "\n\n🎁 <b>₹5,000 Prize Pool</b>\n🥇 ₹2,500 • 🥈 ₹1,500 • 🥉 ₹1,000\n10 questions • 30 seconds each • free entry"
    if kind == "preview":
        return f"🔥 <b>TOMORROW: BETROXY SUNDAY MEGA QUIZ</b>\n\nStarts Sunday {day} at <b>10:00 IST</b>.{common}"
    if kind == "open":
        return f"🔥 <b>₹5,000 SUNDAY MEGA QUIZ IS OPEN!</b>\n\nPlay anytime before <b>21:00 IST</b>.{common}"
    if kind == "reminder":
        return f"🏆 <b>SUNDAY MEGA QUIZ — ₹5,000 UP FOR GRABS</b>\n\nLeaderboard is live. Complete your 10 questions before 21:00 IST.{common}"
    return f"⏰ <b>FINAL 30 MINUTES — ₹5,000 MEGA QUIZ</b>\n\nCloses at <b>21:00 IST</b>. One attempt only.{common}"


def _period_key(campaign):
    return f"weekly_mega:{campaign['campaign_date']}"


def _award_for_rank(campaign, rank):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM reward_awards WHERE reward_type='weekly_mega_quiz' AND period_key=%s AND rank=%s LIMIT 1", (_period_key(campaign), int(rank)))
            return cur.fetchone()


def _stage_awards(campaign, rows):
    for rank, amount in enumerate(PRIZES, 1):
        if len(rows) < rank:
            continue
        row = rows[rank - 1]
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO reward_awards(
                        telegram_user_id,reward_type,period_key,rank,points,amount,currency,brand_code,provider,status
                    ) VALUES (%s,'weekly_mega_quiz',%s,%s,%s,%s,'INR',%s,'giftport','queued')
                    ON CONFLICT(reward_type,period_key,rank) DO NOTHING
                """, (
                    int(row["telegram_user_id"]), _period_key(campaign), rank,
                    int(row.get("correct_count") or 0), int(amount), REWARD_OPERATOR,
                ))
            conn.commit()


def _rewards_pending_approval(campaign):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) AS n
                FROM reward_awards
                WHERE reward_type='weekly_mega_quiz' AND period_key=%s
                  AND COALESCE(issue_attempts,0)=0
                  AND COALESCE(status,'queued') NOT IN ('issued','delivered','delivery_pending','issuing')
            """, (_period_key(campaign),))
            return int((cur.fetchone() or {}).get("n") or 0) > 0


def _result_text(campaign, rows):
    lines = ["🔥 <b>BETROXY SUNDAY MEGA QUIZ — FINAL RESULTS</b>", ""]
    if not rows:
        lines += ["No eligible finishers qualified for the prize positions this week."]
    else:
        medals = ["🥇", "🥈", "🥉"]
        for i, row in enumerate(rows[:3]):
            name = str(row.get("telegram_username") or f"Player {str(row.get('telegram_user_id') or '')[-4:]}")
            lines.append(f"{medals[i]} <b>{html.escape(name)}</b> — {int(row.get('correct_count') or 0)}/{QUESTION_COUNT} — <b>₹{PRIZES[i]:,}</b>")
        lines += ["", "Final ranking: accuracy → hard-question accuracy → total answer time."]
    lines += ["", "🎁 Total weekly prize pool: <b>₹5,000</b>"]
    return "\n".join(lines)


def _admin_reward_text(campaign, rows, reminder_no=0):
    lead = "🚨 <b>ACTION REQUIRED — MEGA QUIZ PAYOUT</b>" if reminder_no == 0 else "⏰ <b>REMINDER — MEGA QUIZ PAYOUT STILL PENDING</b>"
    lines = [lead, "", f"Date: <b>{campaign['campaign_date']}</b>", f"Eligible payout: <b>₹{sum(PRIZES[:len(rows)]):,}</b>", ""]
    medals = ["🥇", "🥈", "🥉"]
    for i, row in enumerate(rows[:3]):
        name = str(row.get("telegram_username") or f"Player {str(row.get('telegram_user_id') or '')[-4:]}")
        lines.append(f"{medals[i]} {html.escape(name)} — ₹{PRIZES[i]:,}")
    lines += ["", "GiftPort route: <b>PhonePe eGift (GP298)</b>", "Payout remains manual until you approve below."]
    return "\n".join(lines)


def _send_admin_reward_alert(campaign, rows, slot):
    if not rows or not _rewards_pending_approval(campaign):
        return False
    dtype = f"admin_reward_{slot}"
    if _delivery_exists(campaign["id"], str(bot.ADMIN_ID), dtype):
        return True
    ok, _ = quiz._tg_send_text(
        int(bot.ADMIN_ID),
        _admin_reward_text(campaign, rows, reminder_no=slot),
        [[{"text": f"✅ Approve & Issue ₹{sum(PRIZES[:len(rows)]):,}", "callback_data": f"mega_rewards_approve:{campaign['id']}"}],
         [{"text": "🏆 Review Mega Leaderboard", "callback_data": f"mega_leaderboard:{campaign['id']}"}]],
    )
    if ok:
        _mark_delivery(campaign["id"], str(bot.ADMIN_ID), dtype)
    bot.logger.warning("MEGA_QUIZ_ADMIN_REWARD_ALERT slot=%s sent=%s campaign=%s", slot, ok, campaign["id"])
    return bool(ok)


def _announce_due_results():
    now = datetime.now(timezone.utc)
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM mega_quiz_campaigns WHERE result_at<=%s ORDER BY campaign_date DESC LIMIT 4", (now,))
            campaigns = cur.fetchall()
    for campaign in campaigns:
        if _delivery_exists(campaign["id"], CHANNEL, "final_result"):
            continue
        rows = _final_rows(campaign["id"])
        _stage_awards(campaign, rows)
        ok, _ = quiz._tg_send_text(CHANNEL, _result_text(campaign, rows), [[{"text": "🔥 Next Mega Quiz", "url": BOT_DEEPLINK}]])
        if ok:
            _mark_delivery(campaign["id"], CHANNEL, "final_result")
            _send_admin_reward_alert(campaign, rows, 0)
        bot.logger.warning("MEGA_QUIZ_FINAL_RESULT sent=%s campaign=%s winners=%s", ok, campaign["id"], len(rows))


def _approval_summary(campaign, results):
    lines = ["✅ <b>MEGA QUIZ PAYOUT APPROVAL PROCESSED</b>", ""]
    for rank, amount, status in results:
        lines.append(f"#{rank} ₹{amount:,}: <b>{html.escape(status.replace('_',' ').title())}</b>")
    lines += ["", "Duplicate/idempotency protection remains active."]
    return "\n".join(lines)


def _issue_mega_awards(campaign):
    results = []
    for rank, amount in enumerate(PRIZES, 1):
        award = _award_for_rank(campaign, rank)
        if not award:
            continue
        status = str(award.get("status") or "queued")
        attempts = int(award.get("issue_attempts") or 0)
        if status in {"issued", "delivered", "delivery_pending", "issuing"} or attempts > 0:
            results.append((rank, amount, status))
            continue
        try:
            v97._issue_award(award)
        except Exception:
            bot.logger.exception("MEGA_QUIZ_REWARD_ISSUE_FAILED campaign=%s rank=%s", campaign["id"], rank)
        fresh = _award_for_rank(campaign, rank) or award
        results.append((rank, amount, str(fresh.get("status") or status)))
    return results


def _giftport_capability():
    try:
        row = v97._catalogue_row(REWARD_OPERATOR) or {}
        denoms = {int(float(x)) for x in v97._parse_denominations(row.get("denominations")) if float(x).is_integer()}
        return str(row.get("brand_name") or "PhonePe eGift voucher"), denoms
    except Exception:
        return "PhonePe eGift voucher", set()


def worker():
    bot.logger.warning("MEGA_QUIZ_WORKER start sunday=10:00-21:00_IST result=21:10 prize_pool=5000 public_channel_only_promos=on private_campaign_dm=off")
    while True:
        try:
            local = _ist_now()
            campaign = _ensure_campaign()
            day = campaign["campaign_date"]
            _refresh_status(campaign)

            if local.date() == day - timedelta(days=1) and local.time() >= PROMO_PREVIEW_AT:
                _channel_post(campaign, "preview", _promo_text("preview", campaign))

            if local.date() == day:
                t = local.time()
                if OPEN_AT <= t < PROMO_REMINDER_AT:
                    _channel_post(campaign, "open", _promo_text("open", campaign))
                elif PROMO_REMINDER_AT <= t < PROMO_LAST_CALL_AT:
                    _channel_post(campaign, "reminder", _promo_text("reminder", campaign))
                elif PROMO_LAST_CALL_AT <= t < CLOSE_AT:
                    _channel_post(campaign, "last_call", _promo_text("last_call", campaign))

            _announce_due_results()

            # Fresh admin-only payout reminders at +15m and +30m, then stop after approval.
            with bot.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM mega_quiz_campaigns WHERE result_at<=NOW() ORDER BY campaign_date DESC LIMIT 1")
                    latest = cur.fetchone()
            if latest and _delivery_exists(latest["id"], CHANNEL, "final_result"):
                rows = _final_rows(latest["id"])
                elapsed = (datetime.now(timezone.utc) - latest["result_at"]).total_seconds()
                if elapsed >= 15 * 60:
                    _send_admin_reward_alert(latest, rows, 1)
                if elapsed >= 30 * 60:
                    _send_admin_reward_alert(latest, rows, 2)
        except Exception:
            bot.logger.exception("MEGA_QUIZ_WORKER_FAILED")
        time.sleep(30)


def install():
    global _installed, _previous_callback, _previous_chat_handler, _previous_start
    if _installed:
        return
    _ensure_schema()
    campaign = _ensure_campaign()

    _previous_callback = bot.callback_handler
    _previous_chat_handler = bot.chat_handler
    _previous_start = bot.start

    async def _start_wrapper(update, context):
        payload = str(context.args[0]).strip().lower() if getattr(context, "args", None) else ""
        if payload in {"megaquiz", "mega_quiz", "sundaymega"}:
            await _show_home_message(update.effective_message, update.effective_user.id)
            return
        return await _previous_start(update, context)

    async def _chat_wrapper(update, context):
        user = getattr(update, "effective_user", None)
        msg = getattr(update, "effective_message", None)
        if user and msg:
            state = _session(user.id)
            if str(state.get("flow_state") or "") == "register":
                raw = str(getattr(msg, "text", "") or "").strip()
                digits = re.sub(r"\D+", "", raw)
                if len(digits) == 12 and digits.startswith("91"):
                    digits = digits[2:]
                if not re.fullmatch(r"[6-9]\d{9}", digits):
                    await msg.reply_text("❌ Please enter a valid 10-digit Indian mobile number, for example <code>9876543210</code>.", parse_mode=bot.ParseMode.HTML)
                    return
                saved = v110.v105._save_indian_mobile(user.id, "+91" + digits, "mega_quiz_registration")
                if not saved:
                    await msg.reply_text("❌ Could not save that number. Please try again.")
                    return
                campaign = _campaign_by_id(state.get("campaign_id"))
                if not campaign:
                    campaign = _ensure_campaign()
                _set_session(user.id, flow_state="awaiting_consent")
                if not v110._has_consent(user.id):
                    await _consent_prompt(msg, user.id, campaign["id"])
                else:
                    await _start_quiz(user.id, user.username, campaign)
                return
        return await _previous_chat_handler(update, context)

    async def _callback_wrapper(update, context):
        q = getattr(update, "callback_query", None)
        data = str(getattr(q, "data", "") or "") if q else ""
        if not q or not data.startswith("mega_"):
            return await _previous_callback(update, context)

        uid = int(q.from_user.id)
        if data.startswith("mega_join:"):
            await q.answer()
            campaign = _campaign_by_id(int(data.split(":", 1)[1]))
            if not campaign:
                await q.message.reply_text("Mega Quiz campaign not found.")
                return
            campaign = _refresh_status(campaign)
            if campaign["status"] != "open":
                await q.message.reply_text(_home_text(campaign), parse_mode=bot.ParseMode.HTML)
                return
            if not v110._mobile(uid):
                await _registration_prompt(q.message, uid, campaign["id"])
                return
            if not v110._has_consent(uid):
                await _consent_prompt(q.message, uid, campaign["id"])
                return
            await _start_quiz(uid, q.from_user.username, campaign)
            return

        if data.startswith("mega_consent:"):
            campaign = _campaign_by_id(int(data.split(":", 1)[1]))
            await q.answer("Registration complete")
            if not campaign:
                return
            if not v110._mobile(uid):
                await _registration_prompt(q.message, uid, campaign["id"])
                return
            v110._record_consent(uid, "mega_quiz_registration")
            await _start_quiz(uid, q.from_user.username, campaign)
            return

        if data.startswith("mega_answer:"):
            parts = data.split(":")
            if len(parts) != 3:
                return
            question_id, selected = int(parts[1]), int(parts[2])
            state = _session(uid)
            if int(state.get("current_question_id") or 0) != question_id:
                await q.answer("That question is already closed.", show_alert=True)
                return
            entry = _entry_by_id(state.get("entry_id"))
            campaign = _campaign_by_id(state.get("campaign_id"))
            if not entry or not campaign:
                await q.answer("Session expired. Reopen the Mega Quiz.", show_alert=True)
                return
            with bot.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM mega_quiz_questions WHERE id=%s", (question_id,))
                    question = cur.fetchone()
            if not question:
                return
            sent_at = state.get("question_sent_at")
            if sent_at and not getattr(sent_at, "tzinfo", None):
                sent_at = sent_at.replace(tzinfo=timezone.utc)
            elapsed_ms = int(max(0, (datetime.now(timezone.utc) - sent_at).total_seconds() * 1000)) if sent_at else QUESTION_SECONDS * 1000
            on_time = elapsed_ms <= QUESTION_SECONDS * 1000
            is_correct = bool(on_time and selected == int(question["correct_option"]))
            difficulty = int(question["difficulty"])
            with bot.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO mega_quiz_answers(entry_id,question_id,selected_option,is_correct,difficulty,answer_ms)
                        VALUES (%s,%s,%s,%s,%s,%s)
                        ON CONFLICT(entry_id,question_id) DO NOTHING
                        RETURNING id
                    """, (entry["id"], question_id, selected, is_correct, difficulty, min(elapsed_ms, QUESTION_SECONDS * 1000)))
                    inserted = cur.fetchone()
                    if inserted:
                        cur.execute("""
                            UPDATE mega_quiz_entries
                            SET correct_count=correct_count+%s,
                                hard_correct=hard_correct+%s,
                                total_answer_ms=total_answer_ms+%s
                            WHERE id=%s
                        """, (1 if is_correct else 0, 1 if is_correct and difficulty == 3 else 0, min(elapsed_ms, QUESTION_SECONDS * 1000), entry["id"]))
                conn.commit()
            if not inserted:
                await q.answer("Already answered.", show_alert=True)
                return
            try:
                await q.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
            fresh = _entry_by_id(entry["id"])
            seq = int(question["seq"])
            if not on_time:
                await q.answer("Time expired")
                reaction = "⏱ <b>Time expired.</b>"
            elif is_correct:
                await q.answer("Correct! ✅")
                reaction = "🔥 <b>Correct!</b>"
            else:
                options = json.loads(question["options_json"])
                correct_text = options[int(question["correct_option"])]
                await q.answer("Keep going")
                reaction = f"🎯 <b>Correct answer:</b> {html.escape(correct_text)}"
            reaction += f"\nScore so far: <b>{int(fresh.get('correct_count') or 0)}/{seq}</b>"
            await q.message.reply_text(reaction, parse_mode=bot.ParseMode.HTML)
            nxt = _next_question(fresh["id"])
            if nxt:
                await _send_question(uid, campaign, fresh, nxt)
            else:
                await _finish(uid, campaign, fresh)
            return

        if data.startswith("mega_leaderboard:"):
            await q.answer()
            campaign = _campaign_by_id(int(data.split(":", 1)[1]))
            if not campaign:
                return
            await q.message.reply_text(
                _leaderboard_text(campaign),
                parse_mode=bot.ParseMode.HTML,
                reply_markup=bot.InlineKeyboardMarkup([[bot.InlineKeyboardButton("📢 BETROXY Updates", url="https://t.me/betroxyupdates")]]),
            )
            return

        if data.startswith("mega_rewards_approve:"):
            if not bot.is_admin(uid):
                await q.answer("Admin only", show_alert=True)
                return
            campaign = _campaign_by_id(int(data.split(":", 1)[1]))
            if not campaign:
                await q.answer("Campaign not found", show_alert=True)
                return
            await q.answer("Processing Mega Quiz rewards…")
            results = _issue_mega_awards(campaign)
            await q.message.reply_text(_approval_summary(campaign, results), parse_mode=bot.ParseMode.HTML)
            bot.logger.warning("MEGA_QUIZ_REWARD_APPROVAL admin=%s campaign=%s results=%s", uid, campaign["id"], results)
            return

        return await _previous_callback(update, context)

    bot.start = _start_wrapper
    bot.chat_handler = _chat_wrapper
    bot.callback_handler = _callback_wrapper

    # Add a Mega Quiz CTA to the existing daily completion keyboard without changing daily mechanics.
    main_mod = sys.modules.get("__main__")
    if main_mod is not None and callable(getattr(main_mod, "_result_rows", None)):
        original_rows = main_mod._result_rows

        def _rows_with_mega(daily_campaign):
            rows = [list(r) for r in original_rows(daily_campaign)]
            rows.insert(max(0, len(rows) - 1), [{"text": "🔥 ₹5,000 Sunday Mega Quiz", "url": BOT_DEEPLINK}])
            return rows

        main_mod._result_rows = _rows_with_mega

    brand, denoms = _giftport_capability()
    _installed = True
    bot.logger.warning(
        "MEGA_QUIZ_ACTIVE sunday=10:00-21:00_IST result=21:10 questions=10 timer=30s prize_pool=5000 split=2500/1500/1000 "
        "manual_approval=on public_channel_promos=on private_campaign_dm=off reward_operator=%s giftport_brand=%s giftport_5000=%s "
        "first_upcoming=%s deep_link=%s",
        REWARD_OPERATOR, brand, 5000 in denoms, campaign["campaign_date"], BOT_DEEPLINK,
    )
    bot.logger.warning(
        "MEGA_QUIZ_GIFTPORT_PLAN operator=%s denominations=%s payout_plan=2500:1000+1000+500,1500:1000+500,1000:1000",
        REWARD_OPERATOR, sorted(denoms),
    )
