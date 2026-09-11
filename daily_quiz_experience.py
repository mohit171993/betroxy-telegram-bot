"""Fresh, fair and engaging BETROXY daily quiz experience.

Rules installed by this module:
- every public campaign is frozen once its 7 questions exist
- a new campaign receives 7 fresh questions: 2 easy, 3 medium, 2 hard
- exact question text used in the previous 30 public days is avoided
- weekday themes rotate to keep the challenge fresh
- India Standard Time is authoritative for the daily campaign date
- question messages include lightweight progress/motivation
- result text adds non-cash badges and participation streaks

The existing ranking, 30-second timer, prize amounts and reward approval flow are
left untouched.
"""

import hashlib
import html
import json
import random
from datetime import date, datetime, timedelta, timezone

import bot

IST_OFFSET_HOURS = 5.5

THEMES = {
    0: "Cricket Mania",
    1: "Football Fever",
    2: "Mixed Sports",
    3: "Rules & Records",
    4: "Friday Challenge",
    5: "India Sports Special",
    6: "Championship Mix",
}

# (difficulty, tags, sport, question, options, correct_index)
# Facts are deliberately evergreen/rules-based so the daily quiz does not depend
# on changing live sports results or records.
CURATED = [
    (1, ("cricket", "rules", "mixed"), "Cricket", "How many legal balls are there in a standard cricket over?", ["5", "6", "7", "8"], 1),
    (1, ("cricket", "rules"), "Cricket", "What does LBW stand for in cricket?", ["Leg Before Wicket", "Long Ball Wide", "Last Bat Wins", "Line Behind Wicket"], 0),
    (1, ("cricket", "mixed"), "Cricket", "How many runs is a boundary worth when the ball reaches the rope after touching the ground?", ["3", "4", "5", "6"], 1),
    (1, ("cricket", "mixed"), "Cricket", "How many runs is a clean six worth?", ["4", "5", "6", "7"], 2),
    (1, ("cricket", "rules"), "Cricket", "In a standard 50-over match, what is the scheduled maximum number of overs per side?", ["20", "40", "50", "60"], 2),
    (1, ("cricket", "rules"), "Cricket", "In a standard T20 innings, what is the scheduled maximum number of overs?", ["10", "20", "25", "50"], 1),
    (1, ("cricket", "india"), "Cricket", "A score of 100 runs by one batter is commonly called what?", ["A fifty", "A century", "A double", "A maiden"], 1),
    (1, ("cricket", "india"), "Cricket", "A score of 50 runs by one batter is commonly called what?", ["A half-century", "A century", "A hat-trick", "A powerplay"], 0),
    (2, ("cricket", "rules"), "Cricket", "How many wickets can normally fall before a cricket innings is all out?", ["8", "9", "10", "11"], 2),
    (2, ("cricket", "rules"), "Cricket", "Which player normally stands directly behind the stumps at the striker's end?", ["Bowler", "Wicketkeeper", "Mid-on", "Third umpire"], 1),
    (2, ("cricket", "mixed"), "Cricket", "If a batter hits two fours and one six, how many boundary runs is that?", ["12", "13", "14", "16"], 2),
    (2, ("cricket", "india"), "Cricket", "If 3 overs are completed with no extra deliveries, how many legal balls have been bowled?", ["12", "15", "18", "24"], 2),
    (2, ("cricket", "rules"), "Cricket", "Which result adds one run to the batting side and requires the ball to be bowled again?", ["A legal dot ball", "A no-ball", "A completed over", "A wicket between overs"], 1),
    (3, ("cricket", "challenge"), "Cricket", "A team needs 18 runs from 12 balls. What required run rate per over does that equal?", ["6", "7", "8", "9"], 3),
    (3, ("cricket", "challenge"), "Cricket", "A batter scores 4, 6, 2, 1 and 4. What is the total?", ["15", "16", "17", "18"], 2),

    (1, ("football", "rules", "mixed"), "Football", "How many players does one team normally start with on the football pitch?", ["9", "10", "11", "12"], 2),
    (1, ("football", "rules"), "Football", "Which card sends a football player off the field?", ["Blue", "Green", "Yellow", "Red"], 3),
    (1, ("football", "mixed"), "Football", "What is the standard regulation time of a football match before added time?", ["60 minutes", "80 minutes", "90 minutes", "100 minutes"], 2),
    (1, ("football", "mixed"), "Football", "A player who scores three goals in one match has completed what?", ["A clean sheet", "A hat-trick", "A brace", "A shutout"], 1),
    (1, ("football", "rules"), "Football", "How many points does a team normally receive for a win in league football?", ["1", "2", "3", "4"], 2),
    (1, ("football", "rules"), "Football", "How many points does a team normally receive for a draw in league football?", ["0", "1", "2", "3"], 1),
    (2, ("football", "rules"), "Football", "How far is the penalty spot from the goal line in football?", ["10 yards", "11 yards", "12 yards", "15 yards"], 2),
    (2, ("football", "rules"), "Football", "Two yellow cards to the same player in one match normally result in what?", ["A corner", "A red card", "A free substitution", "A penalty shootout"], 1),
    (2, ("football", "mixed"), "Football", "A team wins 2 matches and draws 1. Using 3 points for a win and 1 for a draw, how many points does it earn?", ["5", "6", "7", "8"], 2),
    (2, ("football", "mixed"), "Football", "A team scores 18 goals and concedes 12. What is its goal difference?", ["+4", "+5", "+6", "+7"], 2),
    (2, ("football", "rules"), "Football", "Can a goal be scored directly from a direct free kick?", ["Yes", "No", "Only after two touches", "Only in extra time"], 0),
    (3, ("football", "challenge"), "Football", "A team has 4 wins, 3 draws and 2 losses. With 3 points per win and 1 per draw, how many points does it have?", ["13", "14", "15", "16"], 2),
    (3, ("football", "challenge"), "Football", "A match is 1-0. The trailing team scores twice and the leading team scores once more. What is the final score?", ["1-2", "2-1", "2-2", "3-2"], 2),

    (1, ("mixed", "rules", "championship"), "Basketball", "How many players from one team are normally on the basketball court at a time?", ["4", "5", "6", "7"], 1),
    (1, ("mixed", "rules"), "Basketball", "How many points is a successful free throw worth?", ["1", "2", "3", "4"], 0),
    (2, ("mixed", "challenge"), "Basketball", "A player makes two 3-pointers and three 2-pointers. How many points is that?", ["10", "11", "12", "13"], 2),
    (1, ("mixed", "championship"), "Tennis", "In tennis scoring, what does 'love' mean?", ["Zero", "One point", "Advantage", "Match point"], 0),
    (2, ("mixed", "rules"), "Tennis", "Which sequence correctly follows the first three scoring steps in a standard tennis game?", ["10, 20, 30", "15, 30, 40", "15, 25, 40", "20, 30, 50"], 1),
    (1, ("mixed", "india", "championship"), "Badminton", "How many points normally win a badminton game before deuce rules apply?", ["15", "18", "21", "25"], 2),
    (2, ("mixed", "rules"), "Badminton", "A standard badminton match is normally best of how many games?", ["1", "3", "5", "7"], 1),
    (1, ("mixed", "rules"), "Volleyball", "How many players from one team are normally on court in indoor volleyball?", ["5", "6", "7", "8"], 1),
    (1, ("mixed", "india"), "Kabaddi", "How many players from one team are normally on court in kabaddi?", ["5", "6", "7", "8"], 2),
    (1, ("mixed", "india", "championship"), "Hockey", "How many players does a field hockey team normally have on the field, including the goalkeeper?", ["9", "10", "11", "12"], 2),
    (2, ("mixed", "rules"), "Hockey", "A standard international field hockey match is divided into how many quarters?", ["2", "3", "4", "5"], 2),
    (2, ("mixed", "rules"), "Table Tennis", "How many points normally win a table-tennis game if there is no 10-10 deuce?", ["9", "10", "11", "15"], 2),
    (1, ("mixed", "championship"), "Olympics", "How many rings are in the Olympic symbol?", ["4", "5", "6", "7"], 1),
    (1, ("mixed", "challenge"), "Athletics", "One lap of a standard outdoor athletics track is how long?", ["200 m", "300 m", "400 m", "500 m"], 2),
    (2, ("mixed", "challenge"), "Athletics", "Two full laps of a standard 400 m outdoor track equal what distance?", ["600 m", "700 m", "800 m", "1,000 m"], 2),
    (1, ("mixed", "rules"), "Chess", "How many squares are on a standard chessboard?", ["48", "56", "64", "72"], 2),
    (1, ("mixed", "championship"), "Golf", "In golf, is a lower or higher total score generally better?", ["Lower", "Higher", "Either is identical", "Only tied scores count"], 0),
]

TAG_FOR_WEEKDAY = {
    0: "cricket",
    1: "football",
    2: "mixed",
    3: "rules",
    4: "challenge",
    5: "india",
    6: "championship",
}


def _india_now():
    return datetime.now(timezone.utc) + timedelta(hours=IST_OFFSET_HOURS)


def theme_for_date(day):
    return THEMES[int(day.weekday())]


def _numeric_options(correct, rng, spread=2, floor=0):
    correct = int(correct)
    vals = {correct}
    offsets = [1, -1, spread, -spread, spread + 1, -(spread + 1), 3, -3]
    rng.shuffle(offsets)
    for off in offsets:
        value = max(floor, correct + off)
        if value != correct:
            vals.add(value)
        if len(vals) == 4:
            break
    while len(vals) < 4:
        vals.add(max(floor, correct + rng.randint(4, 12)))
    choices = list(vals)
    rng.shuffle(choices)
    return [str(v) for v in choices], choices.index(correct)


def _generated_question(difficulty, rng, salt, theme_tag):
    # Numeric/scenario generators create many fresh, objectively keyed questions.
    families = []
    if theme_tag in {"cricket", "india", "rules", "challenge", "championship", "mixed"}:
        families += ["cricket_sum", "cricket_chase", "cricket_balls", "cricket_boundaries"]
    if theme_tag in {"football", "rules", "challenge", "championship", "mixed"}:
        families += ["football_points", "football_score", "football_gd"]
    families += ["basketball_points", "track_distance", "kabaddi_score"]
    family = families[(salt + rng.randrange(len(families))) % len(families)]

    if family == "cricket_sum":
        a, b, c = rng.randint(1, 6), rng.randint(1, 6), rng.randint(1, 6)
        total = a + b + c
        opts, idx = _numeric_options(total, rng, 2)
        return (difficulty, "Cricket", f"A batter scores {a}, {b} and {c} runs from three scoring shots. What is the total?", opts, idx)
    if family == "cricket_chase":
        current = rng.randrange(80, 181, 5)
        needed = rng.randrange(15, 61, 5)
        target = current + needed
        opts, idx = _numeric_options(needed, rng, 5)
        return (difficulty, "Cricket", f"A team is on {current} and its target is {target}. How many more runs are needed to reach the target?", opts, idx)
    if family == "cricket_balls":
        overs = rng.randint(2, 12)
        balls = overs * 6
        opts, idx = _numeric_options(balls, rng, 6)
        return (difficulty, "Cricket", f"With six legal balls per over, how many legal balls are there in exactly {overs} complete overs?", opts, idx)
    if family == "cricket_boundaries":
        fours, sixes = rng.randint(1, 5), rng.randint(1, 4)
        total = fours * 4 + sixes * 6
        opts, idx = _numeric_options(total, rng, 4)
        return (difficulty, "Cricket", f"A batter hits {fours} fours and {sixes} sixes. How many runs came from those boundaries?", opts, idx)
    if family == "football_points":
        wins, draws = rng.randint(1, 6), rng.randint(0, 4)
        total = wins * 3 + draws
        opts, idx = _numeric_options(total, rng, 3)
        return (difficulty, "Football", f"A team has {wins} wins and {draws} draws. With 3 points per win and 1 per draw, how many points has it earned?", opts, idx)
    if family == "football_score":
        a, b = rng.randint(0, 3), rng.randint(0, 3)
        add_a, add_b = rng.randint(0, 2), rng.randint(0, 2)
        final_a, final_b = a + add_a, b + add_b
        correct = f"{final_a}-{final_b}"
        candidates = {correct}
        for da, db in [(1, 0), (0, 1), (-1, 0), (0, -1), (1, 1)]:
            candidates.add(f"{max(0, final_a+da)}-{max(0, final_b+db)}")
            if len(candidates) == 4:
                break
        choices = list(candidates)[:4]
        rng.shuffle(choices)
        return (difficulty, "Football", f"The score is {a}-{b}. The first team scores {add_a} more and the second team scores {add_b} more. What is the final score?", choices, choices.index(correct))
    if family == "football_gd":
        scored = rng.randint(12, 35)
        conceded = rng.randint(5, scored - 1)
        gd = scored - conceded
        opts, idx = _numeric_options(gd, rng, 2)
        return (difficulty, "Football", f"A team has scored {scored} goals and conceded {conceded}. What is its goal difference?", [f"+{x}" for x in opts], idx)
    if family == "basketball_points":
        twos, threes, frees = rng.randint(1, 5), rng.randint(1, 4), rng.randint(0, 5)
        total = twos * 2 + threes * 3 + frees
        opts, idx = _numeric_options(total, rng, 3)
        return (difficulty, "Basketball", f"A player makes {twos} two-pointers, {threes} three-pointers and {frees} free throws. How many points is that?", opts, idx)
    if family == "track_distance":
        laps = rng.randint(2, 8)
        total = laps * 400
        opts, idx = _numeric_options(total, rng, 400)
        return (difficulty, "Athletics", f"On a standard 400 m outdoor track, how far is {laps} complete laps?", [f"{x} m" for x in opts], idx)

    start = rng.randint(8, 25)
    scored = rng.randint(2, 9)
    total = start + scored
    opts, idx = _numeric_options(total, rng, 2)
    return (difficulty, "Kabaddi", f"A kabaddi team has {start} points and then scores {scored} more. What is its new total?", opts, idx)


def _recent_question_texts(v110, day, days=30):
    try:
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT DISTINCT q.question
                       FROM v110_quiz_questions q
                       JOIN v110_quiz_campaigns c ON c.id=q.campaign_id
                       WHERE c.test_mode=FALSE
                         AND c.campaign_date < %s
                         AND c.campaign_date >= %s""",
                    (day, day - timedelta(days=days)),
                )
                return {str(r["question"]) for r in cur.fetchall()}
    except Exception:
        bot.logger.exception("QUIZ_ROTATION_RECENT_LOOKUP_FAILED")
        return set()


def select_daily_questions(v110, day, test_mode=False):
    theme_tag = TAG_FOR_WEEKDAY[int(day.weekday())]
    used = set() if test_mode else _recent_question_texts(v110, day, 30)
    chosen_texts = set()
    seed = int(hashlib.sha256(f"betroxy:{day.isoformat()}:{theme_tag}".encode()).hexdigest()[:16], 16)
    rng = random.Random(seed)
    plan = [1, 1, 2, 2, 2, 3, 3]
    selected = []

    for seq, difficulty in enumerate(plan, start=1):
        candidates = [
            item for item in CURATED
            if int(item[0]) == difficulty
            and (theme_tag in item[1] or "mixed" in item[1])
            and item[3] not in used
            and item[3] not in chosen_texts
        ]
        # Use curated facts when available, but mix in generated scenarios so
        # successive weeks never feel like the same bank in a different order.
        use_generated = (seq + day.toordinal()) % 2 == 0 or not candidates
        picked = None
        if not use_generated:
            rng.shuffle(candidates)
            picked = candidates[0] if candidates else None
            if picked:
                picked = (picked[0], picked[2], picked[3], picked[4], picked[5])

        if picked is None:
            for attempt in range(80):
                probe_rng = random.Random(seed + seq * 1009 + attempt * 7919)
                probe = _generated_question(difficulty, probe_rng, seq + attempt, theme_tag)
                if probe[2] not in used and probe[2] not in chosen_texts:
                    picked = probe
                    break

        if picked is None:
            # Extremely defensive fallback. The date in the text makes it unique
            # while the arithmetic remains objectively keyed.
            n = day.toordinal() % 20 + seq
            total = n + 7
            opts, idx = _numeric_options(total, rng, 2)
            picked = (difficulty, "Sports Maths", f"Daily challenge {day.isoformat()}: a team has {n} points and adds 7. What is the total?", opts, idx)

        selected.append(picked)
        chosen_texts.add(picked[2])

    return selected, theme_for_date(day)


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
                    """SELECT DISTINCT c.campaign_date
                       FROM v110_quiz_entries e
                       JOIN v110_quiz_campaigns c ON c.id=e.campaign_id
                       WHERE e.telegram_user_id=%s
                         AND e.completed_at IS NOT NULL
                         AND c.test_mode=FALSE
                         AND c.campaign_date <= %s
                         AND c.campaign_date >= %s
                       ORDER BY c.campaign_date DESC""",
                    (uid, day, day - timedelta(days=60)),
                )
                dates = {r["campaign_date"] for r in cur.fetchall()}
        streak = 0
        cursor = day
        while cursor in dates:
            streak += 1
            cursor -= timedelta(days=1)
        return streak
    except Exception:
        bot.logger.exception("QUIZ_STREAK_FAILED")
        return 0


def install(v110, quiz, daily_schedule, production_globals):
    if getattr(v110, "_daily_rotation_installed", False):
        return

    # Make the campaign key itself follow India time, not the server/Dubai clock.
    v110.TZ_OFFSET = IST_OFFSET_HOURS
    v110._local_now = _india_now

    original_ensure = v110._ensure_campaign

    def _ensure_campaign_rotating(test_mode=False):
        key = v110._campaign_key(test_mode)
        # Freeze a campaign once questions exist. This prevents a deployment from
        # changing questions after players have started.
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM v110_quiz_campaigns WHERE campaign_key=%s LIMIT 1", (key,))
                existing = cur.fetchone()
                if existing:
                    cur.execute("SELECT COUNT(*) AS n FROM v110_quiz_questions WHERE campaign_id=%s", (int(existing["id"]),))
                    count = int((cur.fetchone() or {}).get("n") or 0)
                    if count >= 7:
                        return existing

        day = _india_now().date()
        questions, theme = select_daily_questions(v110, day, test_mode=test_mode)
        v110.QUESTIONS = questions
        campaign = original_ensure(test_mode=test_mode)
        try:
            with bot.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE v110_quiz_campaigns SET title=%s WHERE id=%s RETURNING *",
                        (f"BETROXY Daily Challenge — {theme}", int(campaign["id"])),
                    )
                    campaign = cur.fetchone() or campaign
                conn.commit()
        except Exception:
            bot.logger.exception("QUIZ_THEME_TITLE_UPDATE_FAILED")
        bot.logger.warning(
            "QUIZ_ROTATION_CREATED campaign=%s date=%s theme=%s mix=2easy/3medium/2hard no_repeat_days=30",
            campaign.get("id"), day, theme,
        )
        return campaign

    v110._ensure_campaign = _ensure_campaign_rotating
    v110._today_campaign = _ensure_campaign_rotating

    # daily_quiz_schedule wraps _today_campaign later during import, so replace
    # its original source reference too; the wrapper continues to enforce closing.
    if hasattr(daily_schedule, "_original_today_campaign"):
        daily_schedule._original_today_campaign = _ensure_campaign_rotating

    original_question_text = daily_schedule._question_text

    def _engaging_question_text(question, seq, remaining=daily_schedule.QUESTION_SECONDS):
        base = original_question_text(question, seq, remaining)
        progress = ""
        if seq == 1:
            progress = f"\n\n🎯 <b>Today's theme: {html.escape(theme_for_date(_india_now().date()))}</b>"
        elif seq == 5:
            progress = "\n\n🔥 <b>4/7 complete — the leaderboard deciders are coming.</b>"
        elif seq == 7:
            progress = "\n\n🏁 <b>Final question — finish strong!</b>"
        else:
            progress = f"\n\n⚡ <b>{seq-1}/7 complete — keep going.</b>"
        return base + progress

    daily_schedule._question_text = _engaging_question_text

    old_result_text = production_globals.get("_result_text")

    def _motivating_result_text(entry, rank, already=False):
        text = old_result_text(entry, rank, already) if callable(old_result_text) else ""
        score = int(entry.get("correct_count") or 0)
        hard = int(entry.get("hard_correct") or 0)
        answer_ms = int(entry.get("total_answer_ms") or 0)
        streak = _streak_for_entry(v110, entry)
        badges = []
        if score == 7:
            badges.append("🏅 Perfect 7/7")
        if hard >= 2:
            badges.append("🧠 Hard Question Ace")
        if score >= 5 and 0 < answer_ms <= 105000:
            badges.append("⚡ Speedster")
        if streak >= 2:
            badges.append(f"🔥 {streak}-Day Streak")
        if badges:
            text += "\n\n<b>Today's badges</b>\n" + " • ".join(badges)
        else:
            text += "\n\n🎯 Complete tomorrow's challenge to start building your streak."
        tomorrow = _india_now().date() + timedelta(days=1)
        text += f"\n\n📅 Tomorrow: <b>{html.escape(theme_for_date(tomorrow))}</b>"
        return text

    production_globals["_result_text"] = _motivating_result_text
    v110._daily_rotation_installed = True
    bot.logger.warning(
        "QUIZ_EXPERIENCE installed=on timezone=IST daily_rotation=on campaign_freeze=on no_repeat=30d difficulty_mix=2/3/2 progress=on badges=on streaks=on"
    )
