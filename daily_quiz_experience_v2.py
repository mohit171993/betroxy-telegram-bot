"""BETROXY Daily Quiz Experience V2.

Adds a 280-question rotating bank, IST weekday themes, 30-day repeat protection,
2/3/2 difficulty mix, answer reactions, Q4 progress, streaks and non-cash badges.
Prize amounts, ranking and manual reward approval are deliberately untouched.
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timedelta, timezone

import bot
import daily_quiz_question_bank as bank

IST_OFFSET_HOURS = 5.5


def _india_now():
    return datetime.now(timezone.utc) + timedelta(hours=IST_OFFSET_HOURS)


def _recent_question_texts(v110, campaign_date, days=30):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT q.question
                FROM v110_quiz_questions q
                JOIN v110_quiz_campaigns c ON c.id=q.campaign_id
                WHERE c.test_mode=FALSE
                  AND c.campaign_date < %s
                  AND c.campaign_date >= %s
                """,
                (campaign_date, campaign_date - timedelta(days=days)),
            )
            return {str(r["question"]) for r in cur.fetchall()}


def _streak_for_entry(v110, entry):
    try:
        campaign = v110._campaign(int(entry.get("campaign_id") or 0))
        if not campaign or campaign.get("test_mode"):
            return 0
        day = campaign["campaign_date"]
        uid = int(entry["telegram_user_id"])
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT c.campaign_date
                    FROM v110_quiz_entries e
                    JOIN v110_quiz_campaigns c ON c.id=e.campaign_id
                    WHERE e.telegram_user_id=%s
                      AND e.completed_at IS NOT NULL
                      AND c.test_mode=FALSE
                      AND c.campaign_date <= %s
                      AND c.campaign_date >= %s
                    ORDER BY c.campaign_date DESC
                    """,
                    (uid, day, day - timedelta(days=120)),
                )
                dates = {r["campaign_date"] for r in cur.fetchall()}
        streak = 0
        cursor = day
        while cursor in dates:
            streak += 1
            cursor -= timedelta(days=1)
        return streak
    except Exception:
        bot.logger.exception("QUIZ_V2_STREAK_FAILED")
        return 0


def _sport_correct(v110, entry_id):
    try:
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT q.sport, COUNT(*) FILTER (WHERE a.is_correct) AS correct
                    FROM v110_quiz_answers a
                    JOIN v110_quiz_questions q ON q.id=a.question_id
                    WHERE a.entry_id=%s
                    GROUP BY q.sport
                    """,
                    (int(entry_id),),
                )
                return {str(r["sport"]): int(r.get("correct") or 0) for r in cur.fetchall()}
    except Exception:
        bot.logger.exception("QUIZ_V2_SPORT_BADGE_FAILED")
        return {}


def install(v110, quiz, daily_schedule, production_globals):
    if getattr(v110, "_daily_rotation_v2_installed", False):
        return

    bank.validate_bank()
    v110.TZ_OFFSET = IST_OFFSET_HOURS
    v110._local_now = _india_now
    original_ensure = v110._ensure_campaign

    def _seed_campaign(campaign, test_mode=False):
        used = set() if test_mode else _recent_question_texts(v110, campaign["campaign_date"], 30)
        theme, selected = bank.select_daily_questions(campaign["campaign_date"], used)
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM v110_quiz_questions WHERE campaign_id=%s", (int(campaign["id"]),))
                for seq, item in enumerate(selected, start=1):
                    cur.execute(
                        """
                        INSERT INTO v110_quiz_questions(
                            campaign_id,seq,difficulty,sport,question,options_json,correct_option
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s)
                        """,
                        (
                            int(campaign["id"]), seq, int(item["difficulty"]), str(item["sport"]),
                            str(item["question"]), json.dumps(item["options"]), int(item["correct"]),
                        ),
                    )
                cur.execute(
                    "UPDATE v110_quiz_campaigns SET title=%s WHERE id=%s RETURNING *",
                    (f"BETROXY Daily Challenge — {theme}", int(campaign["id"])),
                )
                out = cur.fetchone() or campaign
            conn.commit()
        bot.logger.warning(
            "QUIZ_V2_ROTATION campaign=%s date=%s theme=%s bank=%s used30d=%s mix=2/3/2",
            out.get("id"), out.get("campaign_date"), theme, len(bank.QUESTION_BANK), len(used),
        )
        return out

    def _ensure_campaign_rotating(test_mode=False):
        key = v110._campaign_key(test_mode)
        existing = None
        qcount = 0
        entries = 0
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM v110_quiz_campaigns WHERE campaign_key=%s LIMIT 1", (key,))
                existing = cur.fetchone()
                if existing:
                    cur.execute("SELECT COUNT(*) AS n FROM v110_quiz_questions WHERE campaign_id=%s", (int(existing["id"]),))
                    qcount = int((cur.fetchone() or {}).get("n") or 0)
                    cur.execute("SELECT COUNT(*) AS n FROM v110_quiz_entries WHERE campaign_id=%s", (int(existing["id"]),))
                    entries = int((cur.fetchone() or {}).get("n") or 0)

        if existing and qcount >= 7:
            title = str(existing.get("title") or "")
            if "BETROXY Daily Challenge —" in title:
                return existing
            if entries > 0:
                bot.logger.warning(
                    "QUIZ_V2_ROTATION preserve_legacy campaign=%s entries=%s reason=fairness",
                    existing["id"], entries,
                )
                return existing
            # Legacy seven exist but nobody started: safe to upgrade today.
            return _seed_campaign(existing, test_mode=test_mode)

        campaign = original_ensure(test_mode=test_mode)
        # original_ensure inserts the old fixed questions; nobody has started a
        # brand-new campaign yet, so replace them atomically with today's set.
        return _seed_campaign(campaign, test_mode=test_mode)

    v110._ensure_campaign = _ensure_campaign_rotating
    v110._today_campaign = _ensure_campaign_rotating
    if hasattr(daily_schedule, "_original_today_campaign"):
        daily_schedule._original_today_campaign = _ensure_campaign_rotating

    original_question_text = daily_schedule._question_text

    def _engaging_question_text(question, seq, remaining=daily_schedule.QUESTION_SECONDS):
        base = original_question_text(question, seq, remaining)
        if seq == 1:
            extra = f"\n\n🎯 <b>Today's theme: {html.escape(bank.theme_for_date(_india_now().date()))}</b>"
        elif seq == 5:
            extra = "\n\n🔥 <b>4/7 complete — the leaderboard deciders are coming.</b>"
        elif seq == 7:
            extra = "\n\n🏁 <b>Final question — finish strong!</b>"
        else:
            extra = f"\n\n⚡ <b>{seq-1}/7 complete — keep going.</b>"
        return base + extra

    daily_schedule._question_text = _engaging_question_text

    # Upgrade completion text before production captures it in its text-only
    # finish/replay wrappers.
    old_result_text = production_globals.get("_result_text")

    def _motivating_result_text(entry, rank, already=False):
        text = old_result_text(entry, rank, already) if callable(old_result_text) else ""
        campaign = v110._campaign(int(entry.get("campaign_id") or 0))
        score = int(entry.get("correct_count") or 0)
        hard = int(entry.get("hard_correct") or 0)
        answer_ms = int(entry.get("total_answer_ms") or 0)
        streak = _streak_for_entry(v110, entry)
        badges = []
        if streak >= 5:
            badges.append("🔥 5-Day Streak")
        elif streak >= 2:
            badges.append("🔥 2-Day Streak")
        if score == 7:
            badges.append("💯 Perfect 7/7")
        if score >= 5 and 0 < answer_ms <= 90000:
            badges.append("⚡ Speedster")
        if hard >= 2:
            badges.append("🧠 Hard Question Ace")
        sports = _sport_correct(v110, entry["id"])
        if sum(v for k, v in sports.items() if "cricket" in k.lower()) >= 3:
            badges.append("🏏 Cricket Expert")
        if sum(v for k, v in sports.items() if "football" in k.lower()) >= 3:
            badges.append("⚽ Football Expert")

        if campaign and not campaign.get("test_mode"):
            text += f"\n\n🔥 Daily streak: <b>{streak} day{'s' if streak != 1 else ''}</b>"
        if badges:
            text += "\n🏅 " + " • ".join(html.escape(x) for x in badges)

        if campaign:
            top = v110._leaderboard(campaign["id"], 3)
            text += "\n\n🏆 <b>Today's Top 3</b>"
            if not top:
                text += "\nLeaderboard is still forming."
            else:
                medals = ["🥇", "🥈", "🥉"]
                for i, row in enumerate(top[:3]):
                    name = str(row.get("telegram_username") or f"Player {str(row.get('telegram_user_id') or '')[-4:]}")
                    text += f"\n{medals[i]} {html.escape(name)} — {int(row.get('correct_count') or 0)}/7"
        tomorrow = _india_now().date() + timedelta(days=1)
        text += f"\n\n📅 Tomorrow: <b>{html.escape(bank.theme_for_date(tomorrow))}</b>"
        return text

    production_globals["_result_text"] = _motivating_result_text

    # Intercept only quiz-answer callbacks. Everything else continues through
    # the existing proven menu/admin/rewards callback chain.
    previous_callback = bot.callback_handler

    async def _experience_callback(update, context):
        q = getattr(update, "callback_query", None)
        data = str(getattr(q, "data", "") or "") if q else ""
        if not q or not data.startswith("v110_answer:"):
            return await previous_callback(update, context)

        uid = int(q.from_user.id)
        parts = data.split(":")
        if len(parts) != 3:
            return
        question_id, selected = int(parts[1]), int(parts[2])
        session = v110._session(uid)
        if int(session.get("current_question_id") or 0) != question_id:
            await q.answer("That question is already closed.", show_alert=True)
            return
        entry = v110._entry_by_id(session.get("entry_id"))
        campaign = v110._campaign(session.get("campaign_id"))
        if not entry or not campaign:
            await q.answer("Session expired. Reopen today's quiz.", show_alert=True)
            return
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM v110_quiz_questions WHERE id=%s", (question_id,))
                question = cur.fetchone()
        if not question:
            return

        sent_at = session.get("question_sent_at")
        now = datetime.now(timezone.utc)
        if sent_at and not getattr(sent_at, "tzinfo", None):
            sent_at = sent_at.replace(tzinfo=timezone.utc)
        seconds = int(getattr(v110, "QUESTION_SECONDS", 30) or 30)
        elapsed_ms = int(max(0, min(999999, (now - sent_at).total_seconds() * 1000))) if sent_at else seconds * 1000
        on_time = elapsed_ms <= seconds * 1000
        is_correct = bool(on_time and selected == int(question["correct_option"]))
        difficulty = int(question["difficulty"])
        pts = ({1: 10, 2: 15, 3: 20}.get(difficulty, 10) if is_correct else 0)

        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO v110_quiz_answers(entry_id,question_id,selected_option,is_correct,difficulty,answer_ms,points)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(entry_id,question_id) DO NOTHING RETURNING id
                    """,
                    (entry["id"], question_id, selected, is_correct, difficulty, min(elapsed_ms, seconds*1000), pts),
                )
                inserted = cur.fetchone()
                if inserted:
                    cur.execute(
                        """
                        UPDATE v110_quiz_entries
                        SET correct_count=correct_count+%s,
                            hard_correct=hard_correct+%s,
                            points=points+%s,
                            total_answer_ms=total_answer_ms+%s
                        WHERE id=%s
                        """,
                        (1 if is_correct else 0, 1 if is_correct and difficulty == 3 else 0, pts, min(elapsed_ms, seconds*1000), entry["id"]),
                    )
            conn.commit()
        if not inserted:
            await q.answer("Already answered.", show_alert=True)
            return
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass

        seq = int(question.get("seq") or 0)
        remaining = max(0, 7 - seq)
        if not on_time:
            await q.answer("Time expired")
            reaction = f"⏱ <b>Time expired.</b> {remaining} question{'s' if remaining != 1 else ''} remain."
        elif is_correct:
            await q.answer("Correct! ✅")
            if difficulty == 3:
                reaction = f"💥 <b>Hard one correct! +{pts} points</b> — that can move the leaderboard."
            else:
                reaction = f"🔥 <b>Correct! +{pts} points</b> — you're still in the race."
            if remaining:
                reaction += f"\n{remaining} question{'s' if remaining != 1 else ''} remain."
        else:
            options = json.loads(question["options_json"])
            correct_text = options[int(question["correct_option"])]
            await q.answer("Keep going")
            reaction = f"🎯 <b>Close one.</b> Correct answer: <b>{html.escape(correct_text)}</b>"
            if remaining:
                reaction += f"\n{remaining} question{'s' if remaining != 1 else ''} remain — the leaderboard can still change."
        if seq == 4:
            reaction += "\n\n📊 <b>4/7 completed</b> • Keep going — the hard questions can change the leaderboard."
        await q.message.reply_text(reaction, parse_mode=bot.ParseMode.HTML)

        entry = v110._entry_by_id(entry["id"])
        nxt = v110._next_question(entry["id"])
        if nxt:
            await v110._send_question_to_user(uid, campaign, entry, nxt)
        else:
            await v110._finish_quiz(uid, campaign, entry)
        return

    bot.callback_handler = _experience_callback
    v110._daily_rotation_installed = True
    v110._daily_rotation_v2_installed = True
    v110._daily_question_bank_size = len(bank.QUESTION_BANK)
    bot.logger.warning(
        "QUIZ_EXPERIENCE_V2 active=on timezone=IST bank=%s no_repeat=30d themes=7 mix=2easy/3medium/2hard "
        "answer_reactions=on q4_progress=on top3_result=on badges=on streaks=on payouts_unchanged=on",
        len(bank.QUESTION_BANK),
    )
    return _experience_callback
