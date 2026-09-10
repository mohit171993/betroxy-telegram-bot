import html
import json
import os
import random
import threading
import time
from datetime import datetime, timedelta, timezone

import requests

import bot
import v82_intelligence_reporting_center as v82

# ============================================================
# V83 - BETROXY AUTOPILOT ENGAGEMENT
# ============================================================
# Goals:
# - minimal human intervention
# - one unified lead profile
# - one-time opt-in invitation for known bot leads
# - one Business follow-up within Telegram's recent-chat window
# - opted-in reminders / sports hub / promotions / quizzes
# - weekly quiz leaderboard
# - automatic frequency caps, quiet hours, ignore suppression, block handling
# - admin-only Autopilot control + reporting
#
# This runs in a daemon thread inside the existing Railway service. It never
# calls getUpdates and therefore does not compete with the Telegram poller.
# PostgreSQL dedupe keys prevent duplicate sends during Railway deployment overlap.

v81 = v82.v81
v80 = v81.v80
v78 = v81.v78
v75 = v81.v75
v63 = v81.v63
v59 = v81.v59
biz51 = v81.biz51
v53 = v81.v53

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
OFFICIAL_BOT = "BetroxyOfficialBot"
OPEN_APP_URL = v78.MINIAPP_URL
PROMOTIONS_URL = v81.PROMOTIONS_URL
SPORTSBOOK_URL = "tg://resolve?domain=BetroxyBot&appname=sportsbook&startapp=sportsbook"
UPDATES_URL = v78.UPDATES_URL
SUPPORT_URL = v78.TELEGRAM_SUPPORT_URL
PREFERENCES_DEEPLINK = f"https://t.me/{OFFICIAL_BOT}?start=engage"

TZ_OFFSET = int(os.getenv("ENGAGEMENT_TZ_OFFSET", "4"))
WORKER_INTERVAL = max(60, int(os.getenv("ENGAGEMENT_WORKER_INTERVAL", "180")))

_previous_callback_handler = bot.callback_handler
_previous_start = bot.start
_previous_chat_handler = getattr(bot, "chat_handler", None)

QUIZ_BANK = [
    ("How many legal balls are there in a standard cricket over?", ["5", "6", "7", "8"], 1),
    ("In football, how many players does one team normally start with on the pitch?", ["9", "10", "11", "12"], 2),
    ("What does LBW stand for in cricket?", ["Leg Before Wicket", "Long Ball Wide", "Last Bat Wins", "Line Behind Wicket"], 0),
    ("A hat-trick usually means how many successes by the same player in sequence?", ["2", "3", "4", "5"], 1),
    ("How many runs is a boundary worth when the ball reaches the rope after touching the ground?", ["3", "4", "5", "6"], 1),
    ("Which card sends a football player off the field?", ["Blue", "Green", "Yellow", "Red"], 3),
    ("How many points is a six worth in cricket?", ["4", "5", "6", "7"], 2),
    ("In a 50-over cricket match, what is the maximum scheduled overs per side?", ["20", "40", "50", "60"], 2),
]


def _local_now():
    return datetime.now(timezone.utc) + timedelta(hours=TZ_OFFSET)


def _ensure_v83_schema():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS engagement_engine_settings (
                    id INTEGER PRIMARY KEY,
                    master_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                    reminders_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                    sports_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                    promotions_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                    quiz_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                    reactivation_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                    max_weekly_messages INTEGER NOT NULL DEFAULT 3,
                    quiet_start_hour INTEGER NOT NULL DEFAULT 21,
                    quiet_end_hour INTEGER NOT NULL DEFAULT 10,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute("INSERT INTO engagement_engine_settings(id) VALUES (1) ON CONFLICT(id) DO NOTHING")

            cur.execute("ALTER TABLE engagement_subscriptions ADD COLUMN IF NOT EXISTS consent_at TIMESTAMPTZ")
            cur.execute("ALTER TABLE engagement_subscriptions ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'officialbot'")
            cur.execute("ALTER TABLE engagement_subscriptions ADD COLUMN IF NOT EXISTS last_preference_action_at TIMESTAMPTZ")

            cur.execute("ALTER TABLE intelligence_leads ADD COLUMN IF NOT EXISTS suppressed_at TIMESTAMPTZ")
            cur.execute("ALTER TABLE intelligence_leads ADD COLUMN IF NOT EXISTS reachable_bot BOOLEAN NOT NULL DEFAULT FALSE")

            cur.execute("ALTER TABLE engagement_log ADD COLUMN IF NOT EXISTS message_key TEXT")
            cur.execute("ALTER TABLE engagement_log ADD COLUMN IF NOT EXISTS telegram_message_id BIGINT")
            cur.execute("ALTER TABLE engagement_log ADD COLUMN IF NOT EXISTS sent_at TIMESTAMPTZ")
            cur.execute("ALTER TABLE engagement_log ADD COLUMN IF NOT EXISTS clicked_at TIMESTAMPTZ")
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_engagement_log_user_message_key ON engagement_log(telegram_user_id, message_key) WHERE message_key IS NOT NULL")

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS engagement_quizzes (
                    id BIGSERIAL PRIMARY KEY,
                    quiz_key TEXT UNIQUE NOT NULL,
                    question TEXT NOT NULL,
                    options_json TEXT NOT NULL,
                    correct_option INTEGER NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS engagement_quiz_answers (
                    id BIGSERIAL PRIMARY KEY,
                    quiz_id BIGINT NOT NULL REFERENCES engagement_quizzes(id) ON DELETE CASCADE,
                    telegram_user_id BIGINT NOT NULL,
                    selected_option INTEGER NOT NULL,
                    is_correct BOOLEAN NOT NULL,
                    points INTEGER NOT NULL DEFAULT 0,
                    answered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(quiz_id, telegram_user_id)
                )
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_quiz_answers_user_time ON engagement_quiz_answers(telegram_user_id, answered_at DESC)")

            # Existing referrals have previously started the OfficialBot, so they are reachable.
            cur.execute(
                """
                UPDATE intelligence_leads l
                SET reachable_bot=TRUE
                WHERE EXISTS (SELECT 1 FROM referrals r WHERE r.telegram_user_id=l.telegram_user_id)
                """
            )
        conn.commit()


def _settings():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM engagement_engine_settings WHERE id=1")
            return cur.fetchone() or {}


def _touch_user(user_id, event="interaction"):
    if not user_id or int(user_id) == int(bot.ADMIN_ID):
        return
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE intelligence_leads
                SET last_seen_at=NOW(), last_event=%s, ignored_count=0,
                    lifecycle_stage=CASE WHEN opt_out THEN lifecycle_stage ELSE 'engaged' END,
                    suppressed_at=CASE WHEN opt_out THEN suppressed_at ELSE NULL END
                WHERE telegram_user_id=%s
                """,
                (event, int(user_id)),
            )
            cur.execute(
                """
                UPDATE engagement_subscriptions
                SET master_enabled=CASE
                    WHEN EXISTS (SELECT 1 FROM intelligence_leads l WHERE l.telegram_user_id=%s AND l.opt_out=TRUE)
                    THEN master_enabled ELSE TRUE END,
                    updated_at=NOW()
                WHERE telegram_user_id=%s
                """,
                (int(user_id), int(user_id)),
            )
        conn.commit()


def _subscription(user_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM engagement_subscriptions WHERE telegram_user_id=%s", (int(user_id),))
            return cur.fetchone()


def _set_subscription(user_id, field=None, enabled=None, stop_all=False, source="officialbot"):
    allowed = {"sports_updates", "promotions", "quiz_rewards"}
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO engagement_subscriptions(telegram_user_id, source)
                VALUES (%s,%s)
                ON CONFLICT(telegram_user_id) DO NOTHING
                """,
                (int(user_id), source),
            )
            if stop_all:
                cur.execute(
                    """
                    UPDATE engagement_subscriptions
                    SET sports_updates=FALSE, promotions=FALSE, quiz_rewards=FALSE,
                        master_enabled=FALSE, last_preference_action_at=NOW(), updated_at=NOW()
                    WHERE telegram_user_id=%s
                    """,
                    (int(user_id),),
                )
                cur.execute(
                    """
                    UPDATE intelligence_leads
                    SET opt_out=TRUE, lifecycle_stage='opted_out', last_event='stop_updates'
                    WHERE telegram_user_id=%s
                    """,
                    (int(user_id),),
                )
            elif field in allowed:
                cur.execute(
                    f"""
                    UPDATE engagement_subscriptions
                    SET {field}=%s, master_enabled=TRUE,
                        consent_at=COALESCE(consent_at,NOW()),
                        last_preference_action_at=NOW(), updated_at=NOW()
                    WHERE telegram_user_id=%s
                    """,
                    (bool(enabled), int(user_id)),
                )
                cur.execute(
                    """
                    UPDATE intelligence_leads
                    SET opt_out=FALSE, lifecycle_stage='subscribed', ignored_count=0,
                        suppressed_at=NULL, last_event=%s
                    WHERE telegram_user_id=%s
                    """,
                    (f"subscription_{field}_{'on' if enabled else 'off'}", int(user_id)),
                )
        conn.commit()
    return _subscription(user_id)


def _preferences_text(user_id):
    s = _subscription(user_id) or {}
    return (
        "🔔 <b>BETROXY Updates & Challenges</b>\n\n"
        "Choose only what you want to receive. You can switch any category off at any time.\n\n"
        f"🏏 Sports updates: <b>{'ON ✅' if s.get('sports_updates') else 'OFF'}</b>\n"
        f"🎁 Promotions: <b>{'ON ✅' if s.get('promotions') else 'OFF'}</b>\n"
        f"🏆 Quiz & rewards: <b>{'ON ✅' if s.get('quiz_rewards') else 'OFF'}</b>\n\n"
        "Automatic messages are frequency-limited."
    )


def _preferences_keyboard(user_id):
    s = _subscription(user_id) or {}
    return bot.InlineKeyboardMarkup([
        [bot.InlineKeyboardButton(f"🏏 Sports {'✅' if s.get('sports_updates') else '➕'}", callback_data="eng_pref:sports_updates")],
        [bot.InlineKeyboardButton(f"🎁 Promotions {'✅' if s.get('promotions') else '➕'}", callback_data="eng_pref:promotions")],
        [bot.InlineKeyboardButton(f"🏆 Quiz & Rewards {'✅' if s.get('quiz_rewards') else '➕'}", callback_data="eng_pref:quiz_rewards")],
        [bot.InlineKeyboardButton("🔕 Stop All Updates", callback_data="eng_stop_all")],
        [bot.InlineKeyboardButton("⬅️ Main Menu", callback_data="home")],
    ])


def _add_updates_button(markup):
    rows = [list(r) for r in markup.inline_keyboard]
    if any(any("Updates & Quiz" in str(getattr(b, "text", "")) for b in r) for r in rows):
        return markup
    insert_at = max(1, len(rows) - 2)
    rows.insert(insert_at, [bot.InlineKeyboardButton("🔔 Updates & Quiz", callback_data="eng_preferences")])
    return bot.InlineKeyboardMarkup(rows)


_current_v53_menu = v53.v53_public_menu

def _v83_v53_menu(user_id=None):
    return _add_updates_button(_current_v53_menu(user_id))


_current_v59_menu = v59.v59_public_menu

def _v83_v59_menu(user_id=None):
    return _add_updates_button(_current_v59_menu(user_id))


_current_business_menu = v75._business_menu

def _v83_business_menu(styled=True):
    markup = _current_business_menu(styled=styled)
    rows = [list(r) for r in markup.inline_keyboard]
    if not any(any("Updates & Quiz" in str(getattr(b, "text", "")) for b in r) for r in rows):
        rows.insert(max(1, len(rows)-2), [bot.InlineKeyboardButton("🔔 Updates & Quiz", url=PREFERENCES_DEEPLINK)])
    return bot.InlineKeyboardMarkup(rows)


v53.v53_public_menu = _v83_v53_menu
v59.v59_public_menu = _v83_v59_menu
bot.public_menu = _v83_v53_menu
v75._business_menu = _v83_business_menu
v78.business_main_menu = _v83_business_menu


def _quiz_for_today():
    local = _local_now()
    idx = (local.toordinal() + local.isocalendar().week) % len(QUIZ_BANK)
    question, options, correct = QUIZ_BANK[idx]
    key = f"trivia:{local.strftime('%Y-%m-%d')}:{idx}"
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO engagement_quizzes(quiz_key,question,options_json,correct_option)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT(quiz_key) DO UPDATE SET question=EXCLUDED.question
                RETURNING *
                """,
                (key, question, json.dumps(options), correct),
            )
            row = cur.fetchone()
        conn.commit()
    return row


def _weekly_score(user_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(SUM(points),0) AS score, COUNT(*) AS answered,
                       COUNT(*) FILTER(WHERE is_correct) AS correct
                FROM engagement_quiz_answers
                WHERE telegram_user_id=%s
                  AND answered_at >= DATE_TRUNC('week',NOW())
                """,
                (int(user_id),),
            )
            return cur.fetchone() or {}


async def v83_start(update, context):
    user = update.effective_user
    uid = getattr(user, "id", None)
    args = list(getattr(context, "args", []) or [])
    payload = str(args[0]).lower() if args else ""

    if uid and int(uid) != int(bot.ADMIN_ID):
        _touch_user(uid, "bot_start")
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE intelligence_leads SET reachable_bot=TRUE WHERE telegram_user_id=%s", (int(uid),))
            conn.commit()

    if payload in {"opt_sports", "opt_promos", "opt_quiz"} and uid:
        mapping = {"opt_sports": "sports_updates", "opt_promos": "promotions", "opt_quiz": "quiz_rewards"}
        _set_subscription(uid, mapping[payload], True)

    result = await _previous_start(update, context)

    if uid and payload in {"engage", "opt_sports", "opt_promos", "opt_quiz"}:
        try:
            await update.effective_message.reply_text(
                _preferences_text(uid),
                parse_mode=bot.ParseMode.HTML,
                reply_markup=_preferences_keyboard(uid),
            )
        except Exception:
            bot.logger.exception("V83_PREFERENCES_START_FAILED")
    return result


async def v83_chat_handler(update, context):
    user = update.effective_user
    if user and int(user.id) != int(bot.ADMIN_ID):
        _touch_user(user.id, "message")
        text = str(getattr(update.effective_message, "text", "") or "").strip().lower()
        if text in {"stop", "/stop", "unsubscribe", "stop updates"}:
            _set_subscription(user.id, stop_all=True)
            await update.effective_message.reply_text("🔕 Automatic BETROXY updates have been stopped. You can re-enable individual categories anytime from Updates & Quiz.")
            return
    if _previous_chat_handler:
        return await _previous_chat_handler(update, context)


async def v83_callback_handler(update, context):
    q = update.callback_query
    data = (q.data or "") if q else ""
    if not q:
        return await _previous_callback_handler(update, context)

    uid = q.from_user.id
    if not bot.is_admin(uid):
        _touch_user(uid, f"callback:{data[:80]}")

    if data == "eng_preferences":
        await q.answer()
        await q.message.reply_text(_preferences_text(uid), parse_mode=bot.ParseMode.HTML, reply_markup=_preferences_keyboard(uid))
        return

    if data.startswith("eng_pref:"):
        field = data.split(":", 1)[1]
        current = _subscription(uid) or {}
        new_value = not bool(current.get(field))
        _set_subscription(uid, field, new_value)
        await q.answer("Updated")
        try:
            await q.edit_message_text(_preferences_text(uid), parse_mode=bot.ParseMode.HTML, reply_markup=_preferences_keyboard(uid))
        except Exception:
            await q.message.reply_text(_preferences_text(uid), parse_mode=bot.ParseMode.HTML, reply_markup=_preferences_keyboard(uid))
        return

    if data == "eng_stop_all":
        _set_subscription(uid, stop_all=True)
        await q.answer("Updates stopped", show_alert=True)
        try:
            await q.edit_message_text("🔕 <b>Automatic updates stopped.</b>\n\nOpen Updates & Quiz from the main menu whenever you want to subscribe again.", parse_mode=bot.ParseMode.HTML)
        except Exception:
            pass
        return

    if data.startswith("eng_quiz:"):
        try:
            _, quiz_id, option = data.split(":", 2)
            quiz_id, option = int(quiz_id), int(option)
            with bot.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM engagement_quizzes WHERE id=%s", (quiz_id,))
                    quiz = cur.fetchone()
                    if not quiz:
                        await q.answer("Quiz not found", show_alert=True)
                        return
                    is_correct = option == int(quiz["correct_option"])
                    points = 10 if is_correct else 0
                    cur.execute(
                        """
                        INSERT INTO engagement_quiz_answers(quiz_id,telegram_user_id,selected_option,is_correct,points)
                        VALUES (%s,%s,%s,%s,%s)
                        ON CONFLICT(quiz_id,telegram_user_id) DO NOTHING
                        RETURNING id
                        """,
                        (quiz_id, uid, option, is_correct, points),
                    )
                    inserted = cur.fetchone()
                conn.commit()
            if not inserted:
                await q.answer("You already answered this challenge.", show_alert=True)
                return
            score = _weekly_score(uid)
            options = json.loads(quiz["options_json"])
            correct_label = options[int(quiz["correct_option"])]
            if is_correct:
                await q.answer(f"✅ Correct! +10 points • Weekly score {int(score.get('score') or 0)}", show_alert=True)
            else:
                await q.answer(f"❌ Not this time. Correct: {correct_label} • Weekly score {int(score.get('score') or 0)}", show_alert=True)
            with bot.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE engagement_log SET clicked_at=NOW() WHERE telegram_user_id=%s AND campaign_key=%s", (uid, f"quiz:{quiz['quiz_key']}"))
                conn.commit()
            return
        except Exception:
            bot.logger.exception("V83_QUIZ_ANSWER_FAILED")
            try:
                await q.answer("Could not record answer. Please try again.", show_alert=True)
            except Exception:
                pass
            return

    if data.startswith("autopilot_"):
        if not bot.is_admin(uid):
            await q.answer("Admin only", show_alert=True)
            return
        return await _autopilot_callback(q, data)

    return await _previous_callback_handler(update, context)


def _autopilot_text():
    s = _settings()
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM engagement_subscriptions WHERE master_enabled=TRUE AND (sports_updates OR promotions OR quiz_rewards)")
            subs = int((cur.fetchone() or {}).get("n") or 0)
            cur.execute("SELECT COUNT(*) AS n FROM engagement_log WHERE sent_at>=NOW()-INTERVAL '24 hours' AND status='sent'")
            sent24 = int((cur.fetchone() or {}).get("n") or 0)
            cur.execute("SELECT COUNT(*) AS n FROM intelligence_leads WHERE lifecycle_stage='suppressed'")
            suppressed = int((cur.fetchone() or {}).get("n") or 0)
    on = lambda k: "ON ✅" if s.get(k) else "OFF"
    return (
        "🤖 <b>BETROXY AUTOPILOT</b>\n\n"
        f"Master: <b>{on('master_enabled')}</b>\n"
        f"Reminders: <b>{on('reminders_enabled')}</b>\n"
        f"Sports: <b>{on('sports_enabled')}</b>\n"
        f"Promotions: <b>{on('promotions_enabled')}</b>\n"
        f"Quiz: <b>{on('quiz_enabled')}</b>\n"
        f"Reactivation: <b>{on('reactivation_enabled')}</b>\n"
        f"Weekly cap: <b>{int(s.get('max_weekly_messages') or 3)}</b> messages/user\n\n"
        f"Opted-in subscribers: <b>{subs:,}</b>\n"
        f"Messages sent 24h: <b>{sent24:,}</b>\n"
        f"Auto-suppressed leads: <b>{suppressed:,}</b>\n\n"
        f"Quiet hours: {int(s.get('quiet_start_hour') or 21):02d}:00–{int(s.get('quiet_end_hour') or 10):02d}:00 (UTC{TZ_OFFSET:+d})"
    )


def _autopilot_keyboard():
    s = _settings()
    label = lambda title, key: f"{title}: {'ON ✅' if s.get(key) else 'OFF'}"
    return bot.InlineKeyboardMarkup([
        [bot.InlineKeyboardButton(label("🤖 Master", "master_enabled"), callback_data="autopilot_toggle:master_enabled")],
        [bot.InlineKeyboardButton(label("⏰ Reminders", "reminders_enabled"), callback_data="autopilot_toggle:reminders_enabled"), bot.InlineKeyboardButton(label("🏏 Sports", "sports_enabled"), callback_data="autopilot_toggle:sports_enabled")],
        [bot.InlineKeyboardButton(label("🎁 Promotions", "promotions_enabled"), callback_data="autopilot_toggle:promotions_enabled"), bot.InlineKeyboardButton(label("🏆 Quiz", "quiz_enabled"), callback_data="autopilot_toggle:quiz_enabled")],
        [bot.InlineKeyboardButton(label("♻️ Reactivation", "reactivation_enabled"), callback_data="autopilot_toggle:reactivation_enabled")],
        [bot.InlineKeyboardButton("▶️ Run Safe Scan Now", callback_data="autopilot_run")],
        [bot.InlineKeyboardButton("⬅️ Intelligence Center", callback_data="intel_home")],
    ])


async def _autopilot_callback(q, data):
    if data == "autopilot_home":
        await q.answer()
        await q.message.reply_text(_autopilot_text(), parse_mode=bot.ParseMode.HTML, reply_markup=_autopilot_keyboard())
        return
    if data.startswith("autopilot_toggle:"):
        key = data.split(":", 1)[1]
        allowed = {"master_enabled", "reminders_enabled", "sports_enabled", "promotions_enabled", "quiz_enabled", "reactivation_enabled"}
        if key not in allowed:
            await q.answer("Unknown setting")
            return
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(f"UPDATE engagement_engine_settings SET {key}=NOT {key}, updated_at=NOW() WHERE id=1")
                cur.execute("INSERT INTO intelligence_audit_log(actor_user_id,action,detail) VALUES (%s,'autopilot_toggle',%s)", (q.from_user.id, key))
            conn.commit()
        await q.answer("Updated")
        try:
            await q.edit_message_text(_autopilot_text(), parse_mode=bot.ParseMode.HTML, reply_markup=_autopilot_keyboard())
        except Exception:
            pass
        return
    if data == "autopilot_run":
        await q.answer("Safe scan started")
        threading.Thread(target=_worker_cycle, kwargs={"force": True}, daemon=True).start()
        return


_old_intelligence_menu = v82.intelligence_menu

def _v83_intelligence_menu():
    markup = _old_intelligence_menu()
    rows = [list(r) for r in markup.inline_keyboard]
    if not any(any(getattr(b, "callback_data", "") == "autopilot_home" for b in r) for r in rows):
        rows.insert(4, [bot.InlineKeyboardButton("🤖 Autopilot Control", callback_data="autopilot_home")])
    return bot.InlineKeyboardMarkup(rows)


def _v83_engagement_text():
    base = v82.engagement_text_original() if hasattr(v82, "engagement_text_original") else ""
    s = _settings()
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM engagement_quiz_answers WHERE answered_at>=NOW()-INTERVAL '7 days'")
            answers = int((cur.fetchone() or {}).get("n") or 0)
            cur.execute("SELECT COALESCE(SUM(points),0) AS n FROM engagement_quiz_answers WHERE answered_at>=NOW()-INTERVAL '7 days'")
            points = int((cur.fetchone() or {}).get("n") or 0)
            cur.execute("SELECT COUNT(*) AS n FROM engagement_log WHERE status='failed' AND created_at>=NOW()-INTERVAL '7 days'")
            failed = int((cur.fetchone() or {}).get("n") or 0)
    return (
        (base + "\n\n" if base else "")
        + f"🤖 Autopilot master: <b>{'ON ✅' if s.get('master_enabled') else 'OFF'}</b>\n"
        + f"🏆 Quiz answers 7d: <b>{answers:,}</b>\n"
        + f"⭐ Quiz points awarded 7d: <b>{points:,}</b>\n"
        + f"⚠️ Failed engagement sends 7d: <b>{failed:,}</b>"
    )


# Preserve original reporting body, then let V82 resolve the patched functions dynamically.
v82.engagement_text_original = v82.engagement_text
v82.engagement_text = _v83_engagement_text
v82.intelligence_menu = _v83_intelligence_menu


def _tg_send(chat_id, text, keyboard=None, business_connection_id=None):
    payload = {
        "chat_id": int(chat_id),
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    if business_connection_id:
        payload["business_connection_id"] = str(business_connection_id)
    try:
        r = requests.post(f"{TG_API}/sendMessage", json=payload, timeout=20)
        data = r.json() if r.content else {}
        return bool(r.ok and data.get("ok")), data
    except Exception as exc:
        return False, {"description": str(exc)}


def _claim_job(user_id, action, key, detail=""):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO engagement_log(telegram_user_id,channel,action_type,campaign_key,message_key,status,detail)
                VALUES (%s,'officialbot',%s,%s,%s,'sending',%s)
                ON CONFLICT(telegram_user_id,message_key) WHERE message_key IS NOT NULL DO NOTHING
                RETURNING id
                """,
                (int(user_id), action, key, key, detail[:2000]),
            )
            row = cur.fetchone()
        conn.commit()
    return int(row["id"]) if row else None


def _finish_job(job_id, ok, data):
    desc = str(data.get("description") or "")[:1000] if isinstance(data, dict) else ""
    message_id = None
    if ok and isinstance(data, dict):
        message_id = ((data.get("result") or {}).get("message_id"))
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE engagement_log
                SET status=%s, detail=COALESCE(NULLIF(%s,''),detail), telegram_message_id=%s,
                    sent_at=CASE WHEN %s THEN NOW() ELSE sent_at END
                WHERE id=%s
                """,
                ("sent" if ok else "failed", desc, message_id, bool(ok), int(job_id)),
            )
        conn.commit()


def _send_claimed(user_id, action, key, text, keyboard, business_connection_id=None):
    job_id = _claim_job(user_id, action, key)
    if not job_id:
        return False
    ok, data = _tg_send(user_id, text, keyboard, business_connection_id=business_connection_id)
    _finish_job(job_id, ok, data)
    if ok:
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE intelligence_leads SET last_contact_at=NOW(), last_event=%s WHERE telegram_user_id=%s", (f"auto_{action}", int(user_id)))
            conn.commit()
    else:
        desc = str((data or {}).get("description") or "").lower()
        if "blocked" in desc or "forbidden" in desc or "chat not found" in desc:
            with bot.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE intelligence_leads SET opt_out=TRUE,lifecycle_stage='unreachable',last_event='telegram_unreachable' WHERE telegram_user_id=%s", (int(user_id),))
                    cur.execute("UPDATE engagement_subscriptions SET master_enabled=FALSE WHERE telegram_user_id=%s", (int(user_id),))
                conn.commit()
    return ok


def _sync_business_leads():
    try:
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.telegram_business_enquiries') AS t")
                if not (cur.fetchone() or {}).get("t"):
                    return
                cur.execute(
                    """
                    INSERT INTO intelligence_leads(
                        telegram_user_id,telegram_username,first_name,last_name,source,lifecycle_stage,
                        lead_score,first_seen_at,last_seen_at,last_event,reachable_bot
                    )
                    SELECT customer_user_id,customer_username,customer_first_name,customer_last_name,
                           'business',COALESCE(lead_stage,'engaged'),LEAST(85,30+COALESCE(message_count,0)*2),
                           first_message_at,last_message_at,'business_enquiry',
                           EXISTS(SELECT 1 FROM referrals r WHERE r.telegram_user_id=e.customer_user_id)
                    FROM telegram_business_enquiries e
                    WHERE customer_user_id IS NOT NULL
                    ON CONFLICT(telegram_user_id) DO UPDATE SET
                        telegram_username=COALESCE(EXCLUDED.telegram_username,intelligence_leads.telegram_username),
                        first_name=COALESCE(EXCLUDED.first_name,intelligence_leads.first_name),
                        last_name=COALESCE(EXCLUDED.last_name,intelligence_leads.last_name),
                        last_seen_at=GREATEST(intelligence_leads.last_seen_at,EXCLUDED.last_seen_at),
                        lead_score=GREATEST(intelligence_leads.lead_score,EXCLUDED.lead_score),
                        reachable_bot=intelligence_leads.reachable_bot OR EXCLUDED.reachable_bot
                    """
                )
            conn.commit()
    except Exception:
        bot.logger.exception("V83_BUSINESS_SYNC_FAILED")


def _within_send_hours(settings):
    h = _local_now().hour
    quiet_start = int(settings.get("quiet_start_hour") or 21)
    quiet_end = int(settings.get("quiet_end_hour") or 10)
    if quiet_start > quiet_end:
        return not (h >= quiet_start or h < quiet_end)
    return not (quiet_start <= h < quiet_end)


def _weekly_sent(user_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM engagement_log WHERE telegram_user_id=%s AND status='sent' AND sent_at>=NOW()-INTERVAL '7 days' AND action_type IN ('reminder','sports','promotion','quiz','reactivation','leaderboard')", (int(user_id),))
            return int((cur.fetchone() or {}).get("n") or 0)


def _prepare_for_contact(lead, max_weekly):
    uid = int(lead["telegram_user_id"])
    if lead.get("opt_out") or not lead.get("reachable_bot"):
        return False
    if _weekly_sent(uid) >= int(max_weekly):
        return False
    last_contact = lead.get("last_contact_at")
    last_seen = lead.get("last_seen_at")
    ignored = int(lead.get("ignored_count") or 0)
    if last_contact and last_seen and last_contact > last_seen:
        ignored += 1
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                if ignored >= 3:
                    cur.execute("UPDATE intelligence_leads SET ignored_count=%s,lifecycle_stage='suppressed',suppressed_at=NOW(),last_event='auto_suppressed' WHERE telegram_user_id=%s", (ignored, uid))
                    cur.execute("UPDATE engagement_subscriptions SET master_enabled=FALSE WHERE telegram_user_id=%s", (uid,))
                else:
                    cur.execute("UPDATE intelligence_leads SET ignored_count=%s WHERE telegram_user_id=%s", (ignored, uid))
            conn.commit()
        if ignored >= 3:
            return False
    return True


def _send_optin_invites(limit=12):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT l.* FROM intelligence_leads l
                WHERE l.reachable_bot=TRUE AND l.opt_out=FALSE
                  AND NOT EXISTS (SELECT 1 FROM engagement_subscriptions s WHERE s.telegram_user_id=l.telegram_user_id AND s.consent_at IS NOT NULL)
                  AND NOT EXISTS (SELECT 1 FROM engagement_log e WHERE e.telegram_user_id=l.telegram_user_id AND e.message_key='optin_invite')
                  AND l.last_seen_at <= NOW()-INTERVAL '30 minutes'
                ORDER BY l.last_seen_at DESC
                LIMIT %s
                """,
                (int(limit),),
            )
            rows = cur.fetchall()
    kb = [
        [{"text": "🔔 Choose My Updates", "url": PREFERENCES_DEEPLINK}],
        [{"text": "🚀 Open BETROXY App", "url": OPEN_APP_URL}],
    ]
    for lead in rows:
        _send_claimed(
            int(lead["telegram_user_id"]), "optin", "optin_invite",
            "👋 <b>Choose what you want from BETROXY</b>\n\nGet sports updates, Promotions alerts or the BETROXY Sports Challenge — only the categories you choose. You can stop anytime.",
            kb,
        )
        time.sleep(0.08)


def _send_business_recent_followups(limit=8):
    # Business chats are only used for one recent follow-up; long-term automation moves to OfficialBot.
    try:
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.telegram_business_enquiries') AS t")
                if not (cur.fetchone() or {}).get("t"):
                    return
                cur.execute(
                    """
                    SELECT e.* FROM telegram_business_enquiries e
                    WHERE e.customer_user_id IS NOT NULL
                      AND e.last_message_at BETWEEN NOW()-INTERVAL '22 hours' AND NOW()-INTERVAL '6 hours'
                      AND NOT EXISTS (
                        SELECT 1 FROM engagement_log g
                        WHERE g.telegram_user_id=e.customer_user_id
                          AND g.message_key=('business_followup:'||e.id::text)
                      )
                    ORDER BY e.last_message_at DESC LIMIT %s
                    """,
                    (int(limit),),
                )
                rows = cur.fetchall()
        kb = [[{"text": "🔔 Updates & Quiz", "url": PREFERENCES_DEEPLINK}], [{"text": "🎁 Promotions", "url": "https://t.me/BetroxyBot/promotions"}], [{"text": "🎧 Support", "url": SUPPORT_URL}]]
        for e in rows:
            uid = int(e["customer_user_id"])
            key = f"business_followup:{int(e['id'])}"
            job_id = _claim_job(uid, "business_followup", key)
            if not job_id:
                continue
            ok, data = _tg_send(int(e["customer_chat_id"]), "👋 <b>Need anything else?</b>\n\nYou can choose BETROXY updates & quizzes for future messages, view Promotions, or contact support below.", kb, business_connection_id=str(e["connection_id"]))
            _finish_job(job_id, ok, data)
            time.sleep(0.08)
    except Exception:
        bot.logger.exception("V83_BUSINESS_FOLLOWUP_FAILED")


def _subscribed_rows():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT l.*,s.sports_updates,s.promotions,s.quiz_rewards,s.master_enabled,s.consent_at
                FROM intelligence_leads l
                JOIN engagement_subscriptions s USING(telegram_user_id)
                WHERE s.master_enabled=TRUE AND l.opt_out=FALSE AND l.reachable_bot=TRUE
                  AND s.consent_at IS NOT NULL
                  AND (s.sports_updates OR s.promotions OR s.quiz_rewards)
                ORDER BY l.last_contact_at NULLS FIRST, l.last_seen_at
                LIMIT 100
                """
            )
            return cur.fetchall()


def _last_action_at(uid, action):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(sent_at) AS t FROM engagement_log WHERE telegram_user_id=%s AND action_type=%s AND status='sent'", (int(uid), action))
            return (cur.fetchone() or {}).get("t")


def _send_quiz(lead):
    quiz = _quiz_for_today()
    options = json.loads(quiz["options_json"])
    kb = [[{"text": str(opt), "callback_data": f"eng_quiz:{int(quiz['id'])}:{i}"}] for i, opt in enumerate(options)]
    return _send_claimed(int(lead["telegram_user_id"]), "quiz", f"quiz:{quiz['quiz_key']}", f"🏆 <b>BETROXY Sports Challenge</b>\n\n{html.escape(quiz['question'])}\n\nCorrect answer = <b>10 points</b>. Your weekly score updates instantly.", kb)


def _send_next_best_actions(settings):
    now = datetime.now(timezone.utc)
    for lead in _subscribed_rows():
        uid = int(lead["telegram_user_id"])
        if not _prepare_for_contact(lead, int(settings.get("max_weekly_messages") or 3)):
            continue
        inactive = now - (lead.get("last_seen_at") or now)
        last_contact = lead.get("last_contact_at")
        since_contact = now - last_contact if last_contact else timedelta(days=999)

        # Reactivation has highest priority after a full week of inactivity.
        if settings.get("reactivation_enabled") and inactive >= timedelta(days=7) and since_contact >= timedelta(days=7):
            key = f"reactivation:{_local_now().strftime('%Y-%m-%d')}"
            kb = [[{"text": "🚀 Open App", "url": OPEN_APP_URL}, {"text": "🎁 Promotions", "url": PROMOTIONS_URL}], [{"text": "🎧 Support", "url": SUPPORT_URL}]]
            _send_claimed(uid, "reactivation", key, "👋 <b>Welcome back to BETROXY</b>\n\nYour account access, Promotions and support are one tap away. Choose what you need below.", kb)
            time.sleep(0.08)
            continue

        # Quiz every ~3 days for opted-in quiz users, during daytime/evening.
        last_quiz = _last_action_at(uid, "quiz")
        if settings.get("quiz_enabled") and lead.get("quiz_rewards") and (not last_quiz or now-last_quiz >= timedelta(days=3)):
            if _send_quiz(lead):
                time.sleep(0.08)
                continue

        # Promotion alert no more than every 4 days.
        last_promo = _last_action_at(uid, "promotion")
        if settings.get("promotions_enabled") and lead.get("promotions") and (not last_promo or now-last_promo >= timedelta(days=4)) and since_contact >= timedelta(hours=24):
            key = f"promotion:{_local_now().strftime('%Y-%m-%d')}"
            kb = [[{"text": "🎁 View Promotions", "url": PROMOTIONS_URL}], [{"text": "🔕 Preferences", "url": PREFERENCES_DEEPLINK}]]
            _send_claimed(uid, "promotion", key, "🎁 <b>BETROXY Promotions</b>\n\nOpen the Promotions section to see the offers currently available in the app.", kb)
            time.sleep(0.08)
            continue

        # Sports digest no more than every 2 days. No fabricated fixtures: link to live Sportsbook.
        last_sports = _last_action_at(uid, "sports")
        if settings.get("sports_enabled") and lead.get("sports_updates") and (not last_sports or now-last_sports >= timedelta(days=2)) and since_contact >= timedelta(hours=24):
            key = f"sports:{_local_now().strftime('%Y-%m-%d')}"
            kb = [[{"text": "🏏 Open Sportsbook", "url": SPORTSBOOK_URL}], [{"text": "🏆 Quiz & Rewards", "url": PREFERENCES_DEEPLINK}]]
            _send_claimed(uid, "sports", key, "🏏 <b>BETROXY Sports Update</b>\n\nLive fixtures and sports markets are available in Sportsbook. Tap below to see what is on now.", kb)
            time.sleep(0.08)
            continue

        # Gentle 24h reminder only for consented users who have not been contacted recently.
        if settings.get("reminders_enabled") and since_contact >= timedelta(hours=24) and inactive >= timedelta(hours=24):
            key = f"reminder:{_local_now().strftime('%Y-%m-%d')}"
            kb = [[{"text": "🚀 Open BETROXY App", "url": OPEN_APP_URL}], [{"text": "🎧 Help & Support", "url": SUPPORT_URL}]]
            _send_claimed(uid, "reminder", key, "👋 <b>Need help getting started?</b>\n\nYour BETROXY app and support are available below.", kb)
            time.sleep(0.08)


def _send_weekly_leaderboard(settings):
    local = _local_now()
    if not settings.get("quiz_enabled") or local.weekday() != 6 or local.hour < 20:
        return
    week_key = f"leaderboard:{local.strftime('%G-W%V')}"
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT a.telegram_user_id, SUM(a.points) AS score,
                       COALESCE(NULLIF(l.first_name,''),NULLIF(l.telegram_username,''),'Player') AS name
                FROM engagement_quiz_answers a
                LEFT JOIN intelligence_leads l USING(telegram_user_id)
                WHERE a.answered_at>=DATE_TRUNC('week',NOW())
                GROUP BY a.telegram_user_id,l.first_name,l.telegram_username
                ORDER BY score DESC, MIN(a.answered_at)
                LIMIT 5
                """
            )
            top = cur.fetchall()
            cur.execute(
                """
                SELECT s.telegram_user_id FROM engagement_subscriptions s
                JOIN intelligence_leads l USING(telegram_user_id)
                WHERE s.master_enabled=TRUE AND s.quiz_rewards=TRUE AND l.opt_out=FALSE AND l.reachable_bot=TRUE
                """
            )
            recipients = cur.fetchall()
    if not top:
        return
    lines = ["🏆 <b>BETROXY Weekly Challenge Leaderboard</b>", ""]
    for i, row in enumerate(top, 1):
        lines.append(f"{i}. {html.escape(str(row['name'])[:24])} — <b>{int(row['score'] or 0)} pts</b>")
    text = "\n".join(lines)
    for row in recipients[:500]:
        uid = int(row["telegram_user_id"])
        _send_claimed(uid, "leaderboard", week_key, text, [[{"text": "🏆 My Quiz Preferences", "url": PREFERENCES_DEEPLINK}]])
        time.sleep(0.08)


def _worker_cycle(force=False):
    # PostgreSQL transaction-level advisory lock prevents two overlapping Railway
    # containers from running the same scan concurrently during deploy handover.
    try:
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(830083) AS ok")
                locked = bool((cur.fetchone() or {}).get("ok"))
            if not locked:
                return
            try:
                _sync_business_leads()
                settings = _settings()
                if not settings.get("master_enabled"):
                    return
                if not force and not _within_send_hours(settings):
                    return
                _send_optin_invites(limit=12 if not force else 25)
                _send_business_recent_followups(limit=8 if not force else 15)
                _send_next_best_actions(settings)
                _send_weekly_leaderboard(settings)
            finally:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(830083)")
                conn.commit()
    except Exception:
        bot.logger.exception("V83_AUTOPILOT_CYCLE_FAILED")


def _worker_loop():
    bot.logger.warning("V83_AUTOPILOT_WORKER started interval=%ss tz=UTC%+d", WORKER_INTERVAL, TZ_OFFSET)
    time.sleep(45)
    while True:
        _worker_cycle()
        time.sleep(WORKER_INTERVAL)


try:
    _ensure_v83_schema()
    bot.logger.warning("V83_AUTOPILOT_SCHEMA ready=on")
except Exception:
    bot.logger.exception("V83_AUTOPILOT_SCHEMA_FAILED")

bot.start = v83_start
bot.callback_handler = v83_callback_handler
if _previous_chat_handler:
    bot.chat_handler = v83_chat_handler

bot.logger.warning(
    "V83_AUTOPILOT active=on optin=on business_followup=on reminders=on sports=on "
    "promotions=on quiz=on leaderboard=on suppression=on reporting=on"
)

if __name__ == "__main__":
    v63.apply_signup_cta()
    v63.enable_business_smart_auto_reply()
    threading.Thread(target=_worker_loop, name="betroxy-engagement-worker", daemon=True).start()
    bot.logger.warning("V83 polling handover delay=12s")
    time.sleep(12)
    bot.main()
