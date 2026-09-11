import hashlib
import html
import io
import json
import os
import random
import re
import threading
import time
from datetime import datetime, timedelta, timezone

import requests
from PIL import Image, ImageDraw, ImageFont

import bot
import v107_mobile_prompt_state_fix as v107

v105 = v107.v105
v104 = v107.v104
v101 = v107.v101
v97 = v107.v97
v96 = v107.v96
v93 = v107.v93
v89 = v107.v89
v88 = v107.v88
v85 = v107.v85
v83 = v107.v83

TEST_USERNAME = "mohit_97saxena"
CHANNEL_CHAT = os.getenv("V110_CHANNEL", "@betroxycasino").strip() or "@betroxycasino"
PUBLIC_ENABLED = os.getenv("V110_PUBLIC_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
QUESTION_SECONDS = max(8, min(30, int(os.getenv("V110_QUESTION_SECONDS", "15"))))
TZ_OFFSET = int(os.getenv("ENGAGEMENT_TZ_OFFSET", "4"))
OPEN_APP_URL = v83.OPEN_APP_URL
TG_API = v83.TG_API

_old_callback_handler = bot.callback_handler
_old_chat_handler = bot.chat_handler

QUESTIONS = [
    (1, "Cricket", "How many legal balls are there in a standard cricket over?", ["5", "6", "7", "8"], 1),
    (1, "Football", "How many players does one team normally start with on the football pitch?", ["9", "10", "11", "12"], 2),
    (2, "Cricket", "What does LBW stand for in cricket?", ["Leg Before Wicket", "Long Ball Wide", "Last Bat Wins", "Line Behind Wicket"], 0),
    (2, "Football", "Which card sends a football player off the field?", ["Blue", "Green", "Yellow", "Red"], 3),
    (2, "Cricket", "In a 50-over cricket match, what is the maximum scheduled overs per side?", ["20", "40", "50", "60"], 2),
    (3, "Cricket", "A batter scores 4, 6 and 2 from three balls. What is the total?", ["10", "11", "12", "13"], 2),
    (3, "Football", "If a match is 2-1 and the trailing team scores once, what is the new score?", ["2-0", "2-2", "3-1", "3-2"], 1),
]


def _local_now():
    return datetime.now(timezone.utc) + timedelta(hours=TZ_OFFSET)


def _font(size, bold=False):
    candidates = [
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def _wrap(draw, text, font, max_width):
    words = str(text).split()
    lines, line = [], ""
    for word in words:
        probe = word if not line else line + " " + word
        if draw.textbbox((0, 0), probe, font=font)[2] <= max_width:
            line = probe
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def _base_card():
    img = Image.new("RGB", (1080, 1080), (11, 8, 30))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((42, 42, 1038, 1038), radius=52, outline=(110, 82, 255), width=5)
    d.ellipse((760, -120, 1180, 300), fill=(47, 34, 104))
    d.ellipse((-160, 720, 260, 1140), fill=(25, 90, 112))
    d.rounded_rectangle((72, 78, 1008, 190), radius=35, fill=(24, 18, 58))
    d.text((110, 105), "BETROXY", font=_font(54, True), fill=(255, 255, 255))
    d.text((800, 118), "SPORTS", font=_font(28, True), fill=(133, 235, 255))
    return img, d


def _render_hero(campaign):
    img, d = _base_card()
    if campaign.get("test_mode"):
        d.rounded_rectangle((770, 214, 1008, 272), radius=18, fill=(255, 191, 64))
        d.text((815, 228), "TEST RUN", font=_font(24, True), fill=(20, 16, 30))
    d.text((86, 244), "DAILY SPORTS", font=_font(48, True), fill=(145, 233, 255))
    d.text((86, 300), "CHALLENGE", font=_font(82, True), fill=(255, 255, 255))
    prize = int(campaign.get("prize_pool") or 1000)
    d.text((86, 430), f"₹{prize:,} PRIZE POOL", font=_font(62, True), fill=(255, 216, 92))
    d.rounded_rectangle((86, 535, 994, 705), radius=34, fill=(24, 20, 62))
    for x, value, label in [(116, "7", "QUESTIONS"), (411, f"{QUESTION_SECONDS}s", "EACH"), (706, "TOP 3", "WIN")]:
        d.text((x, 565), value, font=_font(46, True), fill=(255, 255, 255))
        d.text((x, 625), label, font=_font(24, True), fill=(158, 165, 212))
    d.text((86, 760), "Skill + accuracy + speed", font=_font(34, True), fill=(192, 200, 255))
    d.text((86, 812), "Free entry • Mobile registration required", font=_font(28), fill=(180, 185, 215))
    d.rounded_rectangle((86, 895, 994, 1000), radius=34, fill=(100, 68, 255))
    d.text((320, 926), "PLAY & WIN", font=_font(42, True), fill=(255, 255, 255))
    out = io.BytesIO(); img.save(out, format="PNG", optimize=True); out.seek(0)
    return out


def _render_question(campaign, question, seq):
    img, d = _base_card()
    difficulty = int(question.get("difficulty") or 1)
    diff_label = {1: "EASY", 2: "MEDIUM", 3: "HARD"}.get(difficulty, "QUIZ")
    d.text((86, 236), f"QUESTION {seq}/7", font=_font(42, True), fill=(144, 231, 255))
    d.rounded_rectangle((790, 222, 995, 280), radius=18, fill=(35, 31, 78))
    d.text((835, 236), diff_label, font=_font(23, True), fill=(255, 216, 92))
    qfont = _font(52, True)
    lines = _wrap(d, question["question"], qfont, 900)
    y = 340
    for line in lines[:5]:
        d.text((86, y), line, font=qfont, fill=(255, 255, 255)); y += 70
    d.rounded_rectangle((86, 760, 994, 900), radius=32, fill=(24, 20, 62))
    d.text((122, 790), f"{QUESTION_SECONDS} SECOND TIMER", font=_font(34, True), fill=(255, 216, 92))
    d.text((122, 842), "Choose one answer below • No going back", font=_font(25), fill=(190, 196, 225))
    d.text((86, 955), "BETROXY DAILY CHALLENGE", font=_font(25, True), fill=(145, 233, 255))
    out = io.BytesIO(); img.save(out, format="PNG", optimize=True); out.seek(0)
    return out


def _render_result(campaign, entry, rank):
    img, d = _base_card()
    d.text((86, 245), "CHALLENGE COMPLETE", font=_font(58, True), fill=(255, 255, 255))
    d.text((86, 350), f"{int(entry.get('correct_count') or 0)}/7 CORRECT", font=_font(76, True), fill=(145, 233, 255))
    d.text((86, 455), f"RANK #{rank}", font=_font(60, True), fill=(255, 216, 92))
    d.rounded_rectangle((86, 565, 994, 740), radius=34, fill=(24, 20, 62))
    d.text((125, 600), "TOP 3 DAILY PRIZES", font=_font(34, True), fill=(255, 255, 255))
    d.text((125, 660), "#1 ₹500    #2 ₹300    #3 ₹200", font=_font(34, True), fill=(255, 216, 92))
    if campaign.get("test_mode"):
        d.text((86, 800), "TEST RUN — NO VOUCHER WILL BE ISSUED", font=_font(28, True), fill=(255, 168, 95))
    d.rounded_rectangle((86, 890, 994, 995), radius=34, fill=(100, 68, 255))
    d.text((295, 922), "EXPLORE BETROXY", font=_font(38, True), fill=(255, 255, 255))
    out = io.BytesIO(); img.save(out, format="PNG", optimize=True); out.seek(0)
    return out


def _render_leaderboard(campaign, rows):
    img, d = _base_card()
    d.text((86, 240), "LIVE LEADERBOARD", font=_font(58, True), fill=(255, 255, 255))
    d.text((86, 315), "Accuracy first • speed breaks ties", font=_font(28), fill=(180, 186, 220))
    y = 410
    for i in range(1, 6):
        row = rows[i-1] if i <= len(rows) else None
        name = str(row.get("telegram_username") or "Player") if row else "—"
        score = f"{int(row.get('correct_count') or 0)}/7" if row else "—"
        d.rounded_rectangle((86, y, 994, y+92), radius=24, fill=(24, 20, 62))
        d.text((115, y+25), f"#{i}", font=_font(30, True), fill=(255, 216, 92))
        d.text((205, y+25), name[:24], font=_font(29, True), fill=(255,255,255))
        d.text((850, y+25), score, font=_font(29, True), fill=(145,233,255))
        y += 108
    out = io.BytesIO(); img.save(out, format="PNG", optimize=True); out.seek(0)
    return out


def _kb_json(rows):
    return json.dumps({"inline_keyboard": rows}, separators=(",", ":"))


def _tg_send_photo(chat_id, photo_io, caption, rows):
    try:
        data = {"chat_id": str(chat_id), "caption": caption, "parse_mode": "HTML", "reply_markup": _kb_json(rows)}
        files = {"photo": ("betroxy_quiz.png", photo_io.getvalue(), "image/png")}
        r = requests.post(f"{TG_API}/sendPhoto", data=data, files=files, timeout=25)
        payload = r.json() if r.content else {}
        return bool(r.ok and payload.get("ok")), payload
    except Exception as exc:
        return False, {"description": str(exc)}


def _ensure_schema():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS v110_quiz_campaigns (
                id BIGSERIAL PRIMARY KEY,campaign_key TEXT UNIQUE NOT NULL,title TEXT NOT NULL,
                campaign_date DATE NOT NULL,prize_pool INTEGER NOT NULL DEFAULT 1000,
                prize_1 INTEGER NOT NULL DEFAULT 500,prize_2 INTEGER NOT NULL DEFAULT 300,
                prize_3 INTEGER NOT NULL DEFAULT 200,status TEXT NOT NULL DEFAULT 'open',
                test_mode BOOLEAN NOT NULL DEFAULT FALSE,opens_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                closes_at TIMESTAMPTZ NOT NULL,channel_posted_at TIMESTAMPTZ,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
            cur.execute("""CREATE TABLE IF NOT EXISTS v110_quiz_questions (
                id BIGSERIAL PRIMARY KEY,campaign_id BIGINT NOT NULL REFERENCES v110_quiz_campaigns(id) ON DELETE CASCADE,
                seq INTEGER NOT NULL,difficulty INTEGER NOT NULL,sport TEXT NOT NULL,question TEXT NOT NULL,
                options_json TEXT NOT NULL,correct_option INTEGER NOT NULL,UNIQUE(campaign_id,seq))""")
            cur.execute("""CREATE TABLE IF NOT EXISTS v110_quiz_entries (
                id BIGSERIAL PRIMARY KEY,campaign_id BIGINT NOT NULL REFERENCES v110_quiz_campaigns(id) ON DELETE CASCADE,
                telegram_user_id BIGINT NOT NULL,telegram_username TEXT,source TEXT NOT NULL DEFAULT 'officialbot',
                started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),completed_at TIMESTAMPTZ,
                correct_count INTEGER NOT NULL DEFAULT 0,hard_correct INTEGER NOT NULL DEFAULT 0,
                points INTEGER NOT NULL DEFAULT 0,total_answer_ms BIGINT NOT NULL DEFAULT 0,
                UNIQUE(campaign_id,telegram_user_id))""")
            cur.execute("""CREATE TABLE IF NOT EXISTS v110_quiz_answers (
                id BIGSERIAL PRIMARY KEY,entry_id BIGINT NOT NULL REFERENCES v110_quiz_entries(id) ON DELETE CASCADE,
                question_id BIGINT NOT NULL REFERENCES v110_quiz_questions(id) ON DELETE CASCADE,
                selected_option INTEGER NOT NULL,is_correct BOOLEAN NOT NULL,difficulty INTEGER NOT NULL,
                answer_ms INTEGER NOT NULL,points INTEGER NOT NULL DEFAULT 0,answered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(entry_id,question_id))""")
            cur.execute("""CREATE TABLE IF NOT EXISTS v110_quiz_sessions (
                telegram_user_id BIGINT PRIMARY KEY,campaign_id BIGINT,entry_id BIGINT,current_question_id BIGINT,
                question_sent_at TIMESTAMPTZ,flow_state TEXT,updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
            cur.execute("""CREATE TABLE IF NOT EXISTS v110_lead_consents (
                telegram_user_id BIGINT PRIMARY KEY,marketing_calls BOOLEAN NOT NULL DEFAULT FALSE,
                whatsapp_updates BOOLEAN NOT NULL DEFAULT FALSE,quiz_terms BOOLEAN NOT NULL DEFAULT FALSE,
                consent_source TEXT,consented_at TIMESTAMPTZ,updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
            cur.execute("""CREATE TABLE IF NOT EXISTS v110_delivery_log (
                id BIGSERIAL PRIMARY KEY,campaign_id BIGINT NOT NULL REFERENCES v110_quiz_campaigns(id) ON DELETE CASCADE,
                target TEXT NOT NULL,delivery_type TEXT NOT NULL,telegram_message_id BIGINT,
                sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),UNIQUE(campaign_id,target,delivery_type))""")
        conn.commit()


def _campaign_key(test_mode=False):
    return f"v110:{'test' if test_mode else 'daily'}:{_local_now().date().isoformat()}"


def _ensure_campaign(test_mode=False):
    key = _campaign_key(test_mode)
    local = _local_now()
    close_local = local.replace(hour=21, minute=0, second=0, microsecond=0)
    if close_local <= local:
        close_local = local + timedelta(hours=4)
    close_utc = close_local - timedelta(hours=TZ_OFFSET)
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO v110_quiz_campaigns(campaign_key,title,campaign_date,prize_pool,status,test_mode,closes_at)
                VALUES (%s,%s,%s,1000,'open',%s,%s) ON CONFLICT(campaign_key) DO UPDATE SET title=EXCLUDED.title RETURNING *""",
                (key, "BETROXY Daily Sports Challenge", local.date(), bool(test_mode), close_utc))
            campaign = cur.fetchone()
            for seq, item in enumerate(QUESTIONS, start=1):
                difficulty, sport, question, options, correct = item
                cur.execute("""INSERT INTO v110_quiz_questions(campaign_id,seq,difficulty,sport,question,options_json,correct_option)
                    VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(campaign_id,seq) DO UPDATE SET
                    difficulty=EXCLUDED.difficulty,sport=EXCLUDED.sport,question=EXCLUDED.question,
                    options_json=EXCLUDED.options_json,correct_option=EXCLUDED.correct_option""",
                    (campaign["id"], seq, difficulty, sport, question, json.dumps(options), correct))
        conn.commit()
    return campaign


def _campaign(campaign_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM v110_quiz_campaigns WHERE id=%s", (int(campaign_id),))
            return cur.fetchone()


def _today_campaign(test_mode=False):
    return _ensure_campaign(test_mode=test_mode)


def _session(uid):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM v110_quiz_sessions WHERE telegram_user_id=%s", (int(uid),))
            return cur.fetchone() or {}


def _set_session(uid, **fields):
    allowed = {"campaign_id", "entry_id", "current_question_id", "question_sent_at", "flow_state"}
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO v110_quiz_sessions(telegram_user_id) VALUES (%s) ON CONFLICT DO NOTHING", (int(uid),))
            pairs, params = [], []
            for key, value in fields.items():
                if key in allowed:
                    pairs.append(f"{key}=%s"); params.append(value)
            pairs.append("updated_at=NOW()"); params.append(int(uid))
            cur.execute(f"UPDATE v110_quiz_sessions SET {', '.join(pairs)} WHERE telegram_user_id=%s", tuple(params))
        conn.commit()


def _clear_mobile_prompt_recency(uid):
    try:
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE user_contact_profiles SET mobile_prompted_at=NULL WHERE telegram_user_id=%s", (int(uid),))
            conn.commit()
    except Exception:
        pass


def _mobile(uid):
    row = v89._mobile_row(uid) or {}
    value = str(row.get("mobile_number") or "").strip()
    return value if v105._is_indian_mobile(value) else None


def _masked_mobile(uid):
    value = _mobile(uid)
    if not value:
        return "Not registered"
    digits = re.sub(r"\D+", "", value)
    return "+91••••••" + digits[-4:]


def _has_consent(uid):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM v110_lead_consents WHERE telegram_user_id=%s", (int(uid),))
            row = cur.fetchone() or {}
    return bool(row.get("marketing_calls") and row.get("whatsapp_updates") and row.get("quiz_terms"))


def _record_consent(uid, source="quiz_registration"):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO v110_lead_consents(telegram_user_id,marketing_calls,whatsapp_updates,quiz_terms,consent_source,consented_at,updated_at)
                VALUES (%s,TRUE,TRUE,TRUE,%s,NOW(),NOW()) ON CONFLICT(telegram_user_id) DO UPDATE SET
                marketing_calls=TRUE,whatsapp_updates=TRUE,quiz_terms=TRUE,consent_source=EXCLUDED.consent_source,
                consented_at=NOW(),updated_at=NOW()""", (int(uid), source))
        conn.commit()
    try:
        v83._set_subscription(uid, "quiz_rewards", True, source="v110_quiz")
    except Exception:
        bot.logger.exception("V110_SUBSCRIPTION_SYNC_FAILED uid=%s", uid)


def _entry(campaign_id, uid, username=None, source="officialbot"):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO v110_quiz_entries(campaign_id,telegram_user_id,telegram_username,source)
                VALUES (%s,%s,%s,%s) ON CONFLICT(campaign_id,telegram_user_id) DO UPDATE SET
                telegram_username=COALESCE(EXCLUDED.telegram_username,v110_quiz_entries.telegram_username) RETURNING *""",
                (int(campaign_id), int(uid), username, source))
            row = cur.fetchone()
        conn.commit()
    return row


def _entry_by_id(entry_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM v110_quiz_entries WHERE id=%s", (int(entry_id),))
            return cur.fetchone()


def _next_question(entry_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT q.* FROM v110_quiz_questions q JOIN v110_quiz_entries e ON e.campaign_id=q.campaign_id
                WHERE e.id=%s AND NOT EXISTS(SELECT 1 FROM v110_quiz_answers a WHERE a.entry_id=e.id AND a.question_id=q.id)
                ORDER BY q.seq LIMIT 1""", (int(entry_id),))
            return cur.fetchone()


def _leaderboard(campaign_id, limit=10):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT * FROM v110_quiz_entries WHERE campaign_id=%s AND completed_at IS NOT NULL
                ORDER BY correct_count DESC, hard_correct DESC, total_answer_ms ASC, completed_at ASC LIMIT %s""",
                (int(campaign_id), int(limit)))
            return cur.fetchall()


def _rank(campaign_id, entry_id):
    rows = _leaderboard(campaign_id, limit=10000)
    for i, row in enumerate(rows, start=1):
        if int(row["id"]) == int(entry_id):
            return i
    return max(1, len(rows) + 1)


def _question_buttons(uid, question):
    opts = json.loads(question["options_json"])
    order = list(range(len(opts)))
    seed = int(hashlib.sha256(f"{uid}:{question['id']}".encode()).hexdigest()[:8], 16)
    random.Random(seed).shuffle(order)
    return [[{"text": opts[idx], "callback_data": f"v110_answer:{question['id']}:{idx}"}] for idx in order]


def _hero_buttons(campaign_id):
    return [[{"text": "🏆 PLAY TODAY'S QUIZ", "callback_data": f"v110_join:{campaign_id}"}], [{"text": "🚀 OPEN BETROXY", "url": OPEN_APP_URL}]]


def _send_hero(chat_id, campaign, delivery_type="hero"):
    caption = "🏆 <b>BETROXY Daily Sports Challenge</b>\n₹1,000 prize pool • 7 timed questions • Top 3 win\nFree entry. Mobile registration is required before participation."
    if campaign.get("test_mode"):
        caption += "\n\n🧪 <b>TEST RUN:</b> no voucher will be issued from this test campaign."
    return _tg_send_photo(chat_id, _render_hero(campaign), caption, _hero_buttons(campaign["id"]))


def _mark_delivery(campaign_id, target, delivery_type, message_id=None):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO v110_delivery_log(campaign_id,target,delivery_type,telegram_message_id)
                VALUES (%s,%s,%s,%s) ON CONFLICT(campaign_id,target,delivery_type) DO NOTHING""",
                (int(campaign_id), str(target), str(delivery_type), message_id))
        conn.commit()


def _delivery_exists(campaign_id, target, delivery_type):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 AS x FROM v110_delivery_log WHERE campaign_id=%s AND target=%s AND delivery_type=%s",
                        (int(campaign_id), str(target), str(delivery_type)))
            return bool(cur.fetchone())


def _find_test_user():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT telegram_user_id,telegram_username,reachable_bot FROM intelligence_leads
                WHERE LOWER(COALESCE(telegram_username,''))=LOWER(%s) ORDER BY last_seen_at DESC NULLS LAST LIMIT 1""",
                (TEST_USERNAME,))
            return cur.fetchone()


def _send_test_probe_once():
    time.sleep(30)
    try:
        campaign = _today_campaign(test_mode=True)
        lead = _find_test_user()
        if not lead or not lead.get("reachable_bot"):
            bot.logger.warning("V110_TEST_RUN target_unavailable username=@%s", TEST_USERNAME)
            return
        uid = int(lead["telegram_user_id"])
        if _delivery_exists(campaign["id"], str(uid), "test_hero"):
            bot.logger.warning("V110_TEST_RUN already_sent uid=%s campaign=%s", uid, campaign["id"])
            return
        ok, data = _send_hero(uid, campaign, "test_hero")
        mid = ((data.get("result") or {}).get("message_id")) if isinstance(data, dict) else None
        if ok:
            _mark_delivery(campaign["id"], str(uid), "test_hero", mid)
        bot.logger.warning("V110_TEST_RUN hero_sent=%s uid=%s campaign=%s message_id=%s", ok, uid, campaign["id"], mid)
    except Exception:
        bot.logger.exception("V110_TEST_RUN_FAILED")


def _public_worker():
    while True:
        try:
            if PUBLIC_ENABLED:
                campaign = _today_campaign(test_mode=False)
                if not _delivery_exists(campaign["id"], CHANNEL_CHAT, "channel_hero"):
                    ok, data = _send_hero(CHANNEL_CHAT, campaign, "channel_hero")
                    mid = ((data.get("result") or {}).get("message_id")) if isinstance(data, dict) else None
                    if ok:
                        _mark_delivery(campaign["id"], CHANNEL_CHAT, "channel_hero", mid)
                        bot.logger.warning("V110_CHANNEL_POST sent campaign=%s message_id=%s", campaign["id"], mid)
        except Exception:
            bot.logger.exception("V110_PUBLIC_WORKER_FAILED")
        time.sleep(120)


def _simple_public_menu(user_id=None):
    return bot.InlineKeyboardMarkup([
        [bot.InlineKeyboardButton("🚀 OPEN BETROXY", url=OPEN_APP_URL)],
        [bot.InlineKeyboardButton("🏆 PLAY TODAY'S QUIZ", callback_data="v110_quiz_home")],
        [bot.InlineKeyboardButton("🎁 MY REWARDS", callback_data="v89_my_rewards"), bot.InlineKeyboardButton("🏆 LEADERBOARD", callback_data="v110_leaderboard")],
        [bot.InlineKeyboardButton("👤 MY ACCOUNT", callback_data="v110_account"), bot.InlineKeyboardButton("🎧 HELP", url=v83.SUPPORT_URL)],
    ])


def _consent_markup(campaign_id):
    return bot.InlineKeyboardMarkup([
        [bot.InlineKeyboardButton("✅ AGREE & START QUIZ", callback_data=f"v110_consent:{campaign_id}")],
        [bot.InlineKeyboardButton("Not now", callback_data="home")],
    ])


async def _registration_prompt(msg, uid, campaign_id, purpose="quiz_register"):
    _clear_mobile_prompt_recency(uid)
    _set_session(uid, campaign_id=int(campaign_id), flow_state=purpose, entry_id=None, current_question_id=None, question_sent_at=None)
    await msg.reply_text("📱 <b>Quick Registration — Step 1 of 2</b>\n\nEnter your <b>10-digit Indian mobile number</b>.\nExample: <code>9876543210</code>\n\nWe add +91 automatically. No contact sharing is required.", parse_mode=bot.ParseMode.HTML)


async def _consent_prompt(msg, uid, campaign_id):
    await msg.reply_text(
        "✅ <b>Registration — Step 2 of 2</b>\n\n"
        f"Mobile: <b>{html.escape(_masked_mobile(uid))}</b>\n\n"
        "By continuing, you agree to participate in the free BETROXY quiz and to receive BETROXY updates, offers and support communication by call/WhatsApp on this number. You can opt out of marketing updates later.\n\n"
        "Quiz ranking is skill-based: accuracy first, then hard-question accuracy, then speed.",
        parse_mode=bot.ParseMode.HTML, reply_markup=_consent_markup(campaign_id))


async def _send_question_to_user(uid, campaign, entry, question):
    seq = int(question["seq"])
    caption = f"🏆 <b>Question {seq}/7</b> • {html.escape(str(question['sport']))}\n⏱ You have {QUESTION_SECONDS} seconds. Choose one answer."
    ok, data = _tg_send_photo(uid, _render_question(campaign, question, seq), caption, _question_buttons(uid, question))
    if ok:
        _set_session(uid, campaign_id=campaign["id"], entry_id=entry["id"], current_question_id=question["id"], question_sent_at=datetime.now(timezone.utc), flow_state="in_quiz")
        bot.logger.warning("V110_QUESTION_SENT uid=%s campaign=%s entry=%s seq=%s", uid, campaign["id"], entry["id"], seq)
    return ok


async def _start_quiz(uid, username, campaign, source="officialbot"):
    entry = _entry(campaign["id"], uid, username, source)
    if entry.get("completed_at"):
        rank = _rank(campaign["id"], entry["id"])
        _tg_send_photo(uid, _render_result(campaign, entry, rank), f"✅ <b>Today's challenge is already complete.</b>\nYour rank: <b>#{rank}</b>",
                       [[{"text": "🏆 LIVE LEADERBOARD", "callback_data": f"v110_leaderboard:{campaign['id']}"}], [{"text": "🚀 OPEN BETROXY", "url": OPEN_APP_URL}]])
        return
    question = _next_question(entry["id"])
    if question:
        await _send_question_to_user(uid, campaign, entry, question)


async def _finish_quiz(uid, campaign, entry):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE v110_quiz_entries SET completed_at=COALESCE(completed_at,NOW()) WHERE id=%s RETURNING *", (int(entry["id"]),))
            final = cur.fetchone()
        conn.commit()
    rank = _rank(campaign["id"], final["id"])
    rows = [[{"text": "🏆 LIVE LEADERBOARD", "callback_data": f"v110_leaderboard:{campaign['id']}"}], [{"text": "🚀 EXPLORE BETROXY", "url": OPEN_APP_URL}]]
    ok, data = _tg_send_photo(uid, _render_result(campaign, final, rank),
        f"🎉 <b>Challenge complete!</b>\nScore: <b>{final['correct_count']}/7</b> • Rank: <b>#{rank}</b>\nDaily ranking: correct answers → hard-question accuracy → total answer time.", rows)
    _set_session(uid, flow_state="complete", current_question_id=None, question_sent_at=None)
    bot.logger.warning("V110_QUIZ_COMPLETE uid=%s campaign=%s entry=%s correct=%s hard=%s ms=%s rank=%s sent=%s", uid, campaign["id"], final["id"], final["correct_count"], final["hard_correct"], final["total_answer_ms"], rank, ok)


async def v110_callback_handler(update, context):
    q = update.callback_query
    data = str(q.data or "") if q else ""
    if not q or not data.startswith("v110_"):
        return await _old_callback_handler(update, context)
    uid = int(q.from_user.id)
    username = q.from_user.username

    if data == "v110_quiz_home":
        campaign = _today_campaign(test_mode=False) if PUBLIC_ENABLED else _today_campaign(test_mode=True)
        await q.answer(); _send_hero(uid, campaign); return

    if data.startswith("v110_join:"):
        campaign_id = int(data.split(":", 1)[1]); campaign = _campaign(campaign_id); await q.answer()
        if not campaign or campaign.get("status") != "open":
            await q.message.reply_text("This challenge is no longer open."); return
        if not _mobile(uid):
            await _registration_prompt(q.message, uid, campaign_id); return
        if not _has_consent(uid):
            await _consent_prompt(q.message, uid, campaign_id); return
        await _start_quiz(uid, username, campaign); return

    if data.startswith("v110_consent:"):
        campaign_id = int(data.split(":", 1)[1]); campaign = _campaign(campaign_id); await q.answer("Registration complete")
        if not campaign: return
        if not _mobile(uid):
            await _registration_prompt(q.message, uid, campaign_id); return
        _record_consent(uid, "v110_quiz_registration")
        await q.message.reply_text("✅ <b>Registration complete.</b> Your quiz starts now.", parse_mode=bot.ParseMode.HTML)
        await _start_quiz(uid, username, campaign); return

    if data.startswith("v110_answer:"):
        parts = data.split(":")
        if len(parts) != 3: return
        question_id, selected = int(parts[1]), int(parts[2]); session = _session(uid)
        if int(session.get("current_question_id") or 0) != question_id:
            await q.answer("That question is already closed.", show_alert=True); return
        entry = _entry_by_id(session.get("entry_id")); campaign = _campaign(session.get("campaign_id"))
        if not entry or not campaign:
            await q.answer("Session expired. Reopen today's quiz.", show_alert=True); return
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM v110_quiz_questions WHERE id=%s", (question_id,)); question = cur.fetchone()
        if not question: return
        sent_at = session.get("question_sent_at"); now = datetime.now(timezone.utc)
        if sent_at and not getattr(sent_at, "tzinfo", None): sent_at = sent_at.replace(tzinfo=timezone.utc)
        elapsed_ms = int(max(0, min(999999, (now - sent_at).total_seconds() * 1000))) if sent_at else QUESTION_SECONDS * 1000
        on_time = elapsed_ms <= QUESTION_SECONDS * 1000
        is_correct = bool(on_time and selected == int(question["correct_option"]))
        difficulty = int(question["difficulty"]); pts = ({1: 10, 2: 15, 3: 20}.get(difficulty, 10) if is_correct else 0)
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""INSERT INTO v110_quiz_answers(entry_id,question_id,selected_option,is_correct,difficulty,answer_ms,points)
                    VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(entry_id,question_id) DO NOTHING RETURNING id""",
                    (entry["id"], question_id, selected, is_correct, difficulty, min(elapsed_ms, QUESTION_SECONDS*1000), pts))
                inserted = cur.fetchone()
                if inserted:
                    cur.execute("""UPDATE v110_quiz_entries SET correct_count=correct_count+%s,hard_correct=hard_correct+%s,
                        points=points+%s,total_answer_ms=total_answer_ms+%s WHERE id=%s""",
                        (1 if is_correct else 0, 1 if is_correct and difficulty == 3 else 0, pts, min(elapsed_ms, QUESTION_SECONDS*1000), entry["id"]))
            conn.commit()
        if not inserted:
            await q.answer("Already answered.", show_alert=True); return
        try: await q.edit_message_reply_markup(reply_markup=None)
        except Exception: pass
        if not on_time:
            await q.answer("Time expired"); await q.message.reply_text("⏱ <b>Time expired.</b> Moving to the next question.", parse_mode=bot.ParseMode.HTML)
        elif is_correct:
            await q.answer("Correct! ✅"); await q.message.reply_text(f"✅ <b>Correct! +{pts} points</b>", parse_mode=bot.ParseMode.HTML)
        else:
            options = json.loads(question["options_json"]); correct_text = options[int(question["correct_option"])]
            await q.answer("Next question"); await q.message.reply_text(f"❌ Not this time. Correct answer: <b>{html.escape(correct_text)}</b>", parse_mode=bot.ParseMode.HTML)
        entry = _entry_by_id(entry["id"]); nxt = _next_question(entry["id"])
        if nxt: await _send_question_to_user(uid, campaign, entry, nxt)
        else: await _finish_quiz(uid, campaign, entry)
        return

    if data.startswith("v110_leaderboard"):
        parts = data.split(":")
        campaign = _campaign(int(parts[1])) if len(parts) == 2 and parts[1].isdigit() else (_today_campaign(False) if PUBLIC_ENABLED else _today_campaign(True))
        await q.answer(); rows = _leaderboard(campaign["id"], 5)
        _tg_send_photo(uid, _render_leaderboard(campaign, rows), "🏆 <b>Live BETROXY leaderboard</b>\nAccuracy first; hard-question accuracy and speed break ties.", [[{"text": "🚀 OPEN BETROXY", "url": OPEN_APP_URL}]])
        return

    if data == "v110_account":
        await q.answer()
        await q.message.reply_text("👤 <b>My Account</b>\n\n" f"📱 Mobile: <b>{html.escape(_masked_mobile(uid))}</b>\n" f"📣 Marketing consent: <b>{'ON ✅' if _has_consent(uid) else 'Not completed'}</b>",
            parse_mode=bot.ParseMode.HTML, reply_markup=bot.InlineKeyboardMarkup([
                [bot.InlineKeyboardButton("✏️ Change Mobile", callback_data="v110_change_mobile")],
                [bot.InlineKeyboardButton("🔔 Notification Preferences", callback_data="eng_preferences")],
                [bot.InlineKeyboardButton("⬅️ Main Menu", callback_data="home")]]))
        return

    if data == "v110_change_mobile":
        await q.answer(); campaign = _today_campaign(test_mode=not PUBLIC_ENABLED)
        await _registration_prompt(q.message, uid, campaign["id"], purpose="change_mobile"); return

    return await _old_callback_handler(update, context)


async def v110_chat_handler(update, context):
    user = update.effective_user; msg = update.effective_message; chat = update.effective_chat
    if user and msg and chat and str(getattr(chat, "type", "")) == "private":
        state = _session(user.id); flow = str(state.get("flow_state") or "")
        if flow in {"quiz_register", "change_mobile"}:
            raw = str(getattr(msg, "text", "") or "").strip(); digits = re.sub(r"\D+", "", raw)
            if len(digits) == 12 and digits.startswith("91"): digits = digits[2:]
            if not re.fullmatch(r"[6-9]\d{9}", digits):
                await msg.reply_text("❌ Please enter a valid 10-digit Indian mobile number, for example <code>9876543210</code>.", parse_mode=bot.ParseMode.HTML); return
            saved = v105._save_indian_mobile(user.id, "+91" + digits, "v110_quiz_lead_registration")
            if not saved:
                await msg.reply_text("❌ Could not save that number. Please try again."); return
            campaign_id = int(state.get("campaign_id") or _today_campaign(test_mode=not PUBLIC_ENABLED)["id"])
            if flow == "change_mobile":
                _set_session(user.id, flow_state=None)
                await msg.reply_text(f"✅ Mobile updated: <b>{html.escape(_masked_mobile(user.id))}</b>", parse_mode=bot.ParseMode.HTML); return
            _set_session(user.id, flow_state="awaiting_consent")
            bot.logger.warning("V110_MOBILE_CAPTURED uid=%s username=%s source=quiz_registration", user.id, user.username)
            await _consent_prompt(msg, user.id, campaign_id); return
    return await _old_chat_handler(update, context)


def _selftest():
    test_uid = -110000000001
    ok_schema = ok_graphics = ok_rank = ok_cleanup = False
    try:
        campaign = _ensure_campaign(test_mode=True)
        ok_graphics = len(_render_hero(campaign).getvalue()) > 5000
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM v110_quiz_entries WHERE telegram_user_id=%s", (test_uid,))
                cur.execute("""INSERT INTO v110_quiz_entries(campaign_id,telegram_user_id,telegram_username,completed_at,correct_count,hard_correct,total_answer_ms)
                    VALUES (%s,%s,'selftest',NOW(),7,2,7000) RETURNING id""", (campaign["id"], test_uid))
                eid = cur.fetchone()["id"]
            conn.commit()
        ok_rank = _rank(campaign["id"], eid) >= 1; ok_schema = bool(campaign.get("id"))
    finally:
        try:
            with bot.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM v110_quiz_entries WHERE telegram_user_id=%s", (test_uid,))
                    cur.execute("DELETE FROM v110_quiz_sessions WHERE telegram_user_id=%s", (test_uid,))
                conn.commit()
            ok_cleanup = True
        except Exception:
            bot.logger.exception("V110_SELFTEST_CLEANUP_FAILED")
    bot.logger.warning("V110_SELFTEST schema=%s graphics=%s rank=%s cleanup=%s public_enabled=%s", ok_schema, ok_graphics, ok_rank, ok_cleanup, PUBLIC_ENABLED)
    if not all([ok_schema, ok_graphics, ok_rank, ok_cleanup]):
        raise RuntimeError("V110 Daily Challenge self-test failed")


bot.public_menu = _simple_public_menu
v96.v96_public_menu = _simple_public_menu
v89.v89_public_menu = _simple_public_menu
v88.v88_public_menu = _simple_public_menu
bot.callback_handler = v110_callback_handler
bot.chat_handler = v110_chat_handler

bot.logger.warning("V110_BETROXY_DAILY_CHALLENGE active=on graphics_first=on mobile_before_quiz=on consent=call+whatsapp quiz_questions=7 timer=%ss ranking=accuracy+hard+speed public_enabled=%s channel=%s", QUESTION_SECONDS, PUBLIC_ENABLED, CHANNEL_CHAT)

if __name__ == "__main__":
    _ensure_schema()
    _selftest()
    v107._db_prompt_state_selftest()
    v105._startup_selftest()
    v97._startup_diagnostic()
    v96._startup_diagnostic()
    v93._startup_pdf_diagnostic()
    v101._phonepe_probe()
    v104._startup_mobile_diagnostic()
    v88.v63.apply_signup_cta()
    v85._enable_smart_reply_without_reset()
    threading.Thread(target=v83._worker_loop, name="betroxy-engagement-worker", daemon=True).start()
    threading.Thread(target=_public_worker, name="betroxy-v110-public-worker", daemon=True).start()
    threading.Thread(target=_send_test_probe_once, name="betroxy-v110-test-run", daemon=True).start()
    bot.logger.warning("V110 polling handover delay=12s")
    time.sleep(12)
    bot.main()
