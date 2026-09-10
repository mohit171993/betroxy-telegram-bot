import html
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone

import requests

import bot
import v85_silent_business_inbox as v85

# ============================================================
# V86 - LIVE SPORTS AUTOPILOT
# ============================================================
# Adds a zero-human-intervention live sports layer on top of V85:
# - hourly fixture discovery (Cricket + Soccer)
# - live match prediction challenge for opted-in Quiz users
# - automatic post-match result lookup and settlement
# - 10 points for correct live predictions
# - combined weekly score + leaderboard (trivia + live predictions)
# - result notifications in OfficialBot personal chat
# - safe fallback to the existing trivia bank if no suitable live fixture exists
#
# TheSportsDB v1 free key 123 is public/documented; SPORTSDB_API_KEY may be
# supplied later for a dedicated premium key without any code change.

v84 = v85.v84
v83 = v85.v83
v63 = v85.v63

SPORTSDB_API_KEY = os.getenv("SPORTSDB_API_KEY", "123").strip() or "123"
SPORTSDB_BASE = f"https://www.thesportsdb.com/api/v1/json/{SPORTSDB_API_KEY}"
DISCOVERY_INTERVAL = timedelta(hours=1)
SETTLEMENT_INTERVAL = timedelta(minutes=30)
PREDICTION_MIN_LEAD = timedelta(minutes=45)
PREDICTION_MAX_LEAD = timedelta(hours=18)

_previous_callback_handler = bot.callback_handler
_old_worker_cycle = v83._worker_cycle
_old_autopilot_text = v83._autopilot_text
_old_autopilot_keyboard = v83._autopilot_keyboard


def _ensure_v86_schema():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "ALTER TABLE engagement_engine_settings ADD COLUMN IF NOT EXISTS live_predictions_enabled BOOLEAN NOT NULL DEFAULT TRUE"
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS live_sports_state (
                    id INTEGER PRIMARY KEY,
                    provider TEXT NOT NULL DEFAULT 'thesportsdb',
                    last_discovery_at TIMESTAMPTZ,
                    last_settlement_scan_at TIMESTAMPTZ,
                    last_success_at TIMESTAMPTZ,
                    last_error TEXT,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute("INSERT INTO live_sports_state(id) VALUES (1) ON CONFLICT(id) DO NOTHING")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS live_sports_events (
                    id BIGSERIAL PRIMARY KEY,
                    provider_event_id TEXT UNIQUE NOT NULL,
                    sport TEXT NOT NULL,
                    league TEXT,
                    event_name TEXT,
                    home_team TEXT NOT NULL,
                    away_team TEXT NOT NULL,
                    event_time TIMESTAMPTZ NOT NULL,
                    provider_status TEXT,
                    home_score TEXT,
                    away_score TEXT,
                    result_text TEXT,
                    outcome_option INTEGER,
                    discovered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_checked_at TIMESTAMPTZ,
                    settled_at TIMESTAMPTZ
                )
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_live_sports_event_time ON live_sports_events(event_time, settled_at)")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS live_sports_predictions (
                    id BIGSERIAL PRIMARY KEY,
                    event_id BIGINT NOT NULL REFERENCES live_sports_events(id) ON DELETE CASCADE,
                    telegram_user_id BIGINT NOT NULL,
                    selected_option INTEGER NOT NULL,
                    is_correct BOOLEAN,
                    points INTEGER NOT NULL DEFAULT 0,
                    predicted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    settled_at TIMESTAMPTZ,
                    result_notified_at TIMESTAMPTZ,
                    UNIQUE(event_id, telegram_user_id)
                )
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_live_predictions_user_time ON live_sports_predictions(telegram_user_id, predicted_at DESC)")
        conn.commit()


def _state():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM live_sports_state WHERE id=1")
            return cur.fetchone() or {}


def _set_state(**kwargs):
    allowed = {"last_discovery_at", "last_settlement_scan_at", "last_success_at", "last_error"}
    pairs = [(k, v) for k, v in kwargs.items() if k in allowed]
    if not pairs:
        return
    sql = ", ".join(f"{k}=%s" for k, _ in pairs)
    vals = [v for _, v in pairs]
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE live_sports_state SET {sql}, updated_at=NOW() WHERE id=1", vals)
        conn.commit()


def _api_get(endpoint, params=None):
    r = requests.get(f"{SPORTSDB_BASE}/{endpoint}", params=params or {}, timeout=20)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict):
        raise RuntimeError("Unexpected sports API response")
    return data


def _event_list(data):
    rows = data.get("events") or data.get("results") or []
    return rows if isinstance(rows, list) else []


def _parse_event_time(event):
    ts = str(event.get("strTimestamp") or "").strip()
    if ts:
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            pass

    d = str(event.get("dateEvent") or event.get("strDate") or "").strip()
    t = str(event.get("strTime") or event.get("strTimeEvent") or "00:00:00").strip()
    if not d:
        return None
    t = t.replace("Z", "").split("+")[0]
    try:
        dt = datetime.fromisoformat(f"{d}T{t}")
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except Exception:
        try:
            dt = datetime.strptime(f"{d} {t[:8]}", "%Y-%m-%d %H:%M:%S")
            return dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None


def _safe_score(value):
    if value is None:
        return None
    m = re.search(r"-?\d+", str(value))
    return int(m.group(0)) if m else None


def _upsert_event(event, sport_hint=None):
    provider_id = str(event.get("idEvent") or "").strip()
    home = str(event.get("strHomeTeam") or "").strip()
    away = str(event.get("strAwayTeam") or "").strip()
    event_time = _parse_event_time(event)
    if not provider_id or not home or not away or not event_time or home.lower() == away.lower():
        return None
    sport = str(event.get("strSport") or sport_hint or "Sports").strip()
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO live_sports_events(
                    provider_event_id,sport,league,event_name,home_team,away_team,event_time,
                    provider_status,home_score,away_score,result_text,discovered_at,updated_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())
                ON CONFLICT(provider_event_id) DO UPDATE SET
                    sport=EXCLUDED.sport, league=EXCLUDED.league, event_name=EXCLUDED.event_name,
                    home_team=EXCLUDED.home_team, away_team=EXCLUDED.away_team,
                    event_time=EXCLUDED.event_time, provider_status=EXCLUDED.provider_status,
                    home_score=COALESCE(EXCLUDED.home_score,live_sports_events.home_score),
                    away_score=COALESCE(EXCLUDED.away_score,live_sports_events.away_score),
                    result_text=COALESCE(NULLIF(EXCLUDED.result_text,''),live_sports_events.result_text),
                    updated_at=NOW()
                RETURNING *
                """,
                (
                    provider_id, sport, event.get("strLeague"), event.get("strEvent"), home, away,
                    event_time, event.get("strStatus"),
                    None if event.get("intHomeScore") is None else str(event.get("intHomeScore")),
                    None if event.get("intAwayScore") is None else str(event.get("intAwayScore")),
                    str(event.get("strResult") or "").strip() or None,
                ),
            )
            row = cur.fetchone()
        conn.commit()
    return row


def _refresh_live_events_if_due(force=False):
    st = _state()
    last = st.get("last_discovery_at")
    now = datetime.now(timezone.utc)
    if not force and last and now - last < DISCOVERY_INTERVAL:
        return 0

    found = 0
    errors = []
    dates = [now.date(), (now + timedelta(days=1)).date()]
    for sport in ("Cricket", "Soccer"):
        for day in dates:
            try:
                data = _api_get("eventsday.php", {"d": day.isoformat(), "s": sport})
                for event in _event_list(data):
                    if _upsert_event(event, sport):
                        found += 1
            except Exception as exc:
                errors.append(f"{sport} {day}: {exc}")
            time.sleep(0.10)

    _set_state(
        last_discovery_at=now,
        last_success_at=now if found else st.get("last_success_at"),
        last_error=" | ".join(errors)[:1800] if errors else None,
    )
    bot.logger.warning("V86_SPORTS_DISCOVERY events=%s errors=%s", found, len(errors))
    return found


def _next_live_event():
    now = datetime.now(timezone.utc)
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM live_sports_events
                WHERE settled_at IS NULL
                  AND event_time BETWEEN %s AND %s
                ORDER BY CASE WHEN LOWER(sport)='cricket' THEN 0 ELSE 1 END, event_time ASC
                LIMIT 1
                """,
                (now + PREDICTION_MIN_LEAD, now + PREDICTION_MAX_LEAD),
            )
            return cur.fetchone()


def _format_local_time(dt):
    if not dt:
        return "Upcoming"
    local = dt.astimezone(timezone(timedelta(hours=v83.TZ_OFFSET)))
    return local.strftime("%d %b • %I:%M %p") + f" UTC{v83.TZ_OFFSET:+d}"


def _send_live_prediction(lead, event):
    uid = int(lead["telegram_user_id"])
    event_id = int(event["id"])
    key = f"live_prediction:{event['provider_event_id']}"
    kb = [
        [{"text": f"🏠 {str(event['home_team'])[:35]}", "callback_data": f"livepred:{event_id}:0"}],
        [{"text": f"✈️ {str(event['away_team'])[:35]}", "callback_data": f"livepred:{event_id}:1"}],
        [{"text": "🤝 Draw / No Result", "callback_data": f"livepred:{event_id}:2"}],
    ]
    text = (
        "⚡ <b>BETROXY Live Match Challenge</b>\n\n"
        f"{html.escape(str(event['home_team']))} vs {html.escape(str(event['away_team']))}\n"
        f"{html.escape(str(event.get('league') or event.get('sport') or 'Sports'))}\n"
        f"🕒 {_format_local_time(event.get('event_time'))}\n\n"
        "Who do you think will win?\n"
        "Correct prediction = <b>10 points</b>. The result is checked automatically after the match."
    )
    return v83._send_claimed(uid, "quiz", key, text, kb)


def _is_final_event(event):
    status = str(event.get("strStatus") or "").strip().lower()
    result = str(event.get("strResult") or "").strip().lower()
    final_terms = ("finished", "completed", "final", "full time", "match over", "after penalties")
    return any(x in status for x in final_terms) or bool(result and any(x in result for x in (" won", "draw", "no result", "tied")))


def _event_outcome(api_event, db_event):
    result = str(api_event.get("strResult") or "").strip().lower()
    home = str(db_event.get("home_team") or "").strip().lower()
    away = str(db_event.get("away_team") or "").strip().lower()
    if any(x in result for x in ("no result", "draw", "tied", "tie")):
        return 2
    if home and home in result and "won" in result:
        return 0
    if away and away in result and "won" in result:
        return 1
    hs = _safe_score(api_event.get("intHomeScore"))
    aws = _safe_score(api_event.get("intAwayScore"))
    if hs is None or aws is None:
        return None
    if hs > aws:
        return 0
    if aws > hs:
        return 1
    return 2


def _settle_live_events_if_due(force=False):
    st = _state()
    now = datetime.now(timezone.utc)
    last = st.get("last_settlement_scan_at")
    if not force and last and now - last < SETTLEMENT_INTERVAL:
        return 0
    _set_state(last_settlement_scan_at=now)

    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM live_sports_events
                WHERE settled_at IS NULL AND event_time < NOW()-INTERVAL '60 minutes'
                  AND (last_checked_at IS NULL OR last_checked_at < NOW()-INTERVAL '30 minutes')
                ORDER BY event_time ASC LIMIT 5
                """
            )
            rows = cur.fetchall()

    settled = 0
    for event in rows:
        try:
            data = _api_get("lookupevent.php", {"id": event["provider_event_id"]})
            api_rows = _event_list(data)
            api_event = api_rows[0] if api_rows else None
            with bot.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE live_sports_events SET last_checked_at=NOW() WHERE id=%s", (int(event["id"]),))
                conn.commit()
            if not api_event or not _is_final_event(api_event):
                continue
            outcome = _event_outcome(api_event, event)
            if outcome is None:
                continue
            hs = None if api_event.get("intHomeScore") is None else str(api_event.get("intHomeScore"))
            aws = None if api_event.get("intAwayScore") is None else str(api_event.get("intAwayScore"))
            result_text = str(api_event.get("strResult") or "").strip() or None
            status = str(api_event.get("strStatus") or "").strip() or None
            with bot.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE live_sports_events
                        SET provider_status=%s,home_score=%s,away_score=%s,result_text=%s,
                            outcome_option=%s,settled_at=NOW(),updated_at=NOW()
                        WHERE id=%s
                        """,
                        (status, hs, aws, result_text, outcome, int(event["id"])),
                    )
                    cur.execute(
                        """
                        UPDATE live_sports_predictions
                        SET is_correct=(selected_option=%s),
                            points=CASE WHEN selected_option=%s THEN 10 ELSE 0 END,
                            settled_at=NOW()
                        WHERE event_id=%s AND settled_at IS NULL
                        """,
                        (outcome, outcome, int(event["id"])),
                    )
                conn.commit()
            settled += 1
        except Exception as exc:
            bot.logger.exception("V86_SPORTS_SETTLEMENT_FAILED event=%s", event.get("provider_event_id"))
            _set_state(last_error=str(exc)[:1800])
        time.sleep(0.10)
    if settled:
        _set_state(last_success_at=now, last_error=None)
    return settled


def _combined_weekly_score(user_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(SUM(points),0) AS score, COUNT(*) AS answered,
                       COUNT(*) FILTER(WHERE is_correct=TRUE) AS correct
                FROM (
                    SELECT points,is_correct,answered_at AS t
                    FROM engagement_quiz_answers WHERE telegram_user_id=%s
                    UNION ALL
                    SELECT points,is_correct,predicted_at AS t
                    FROM live_sports_predictions WHERE telegram_user_id=%s
                ) x
                WHERE t >= DATE_TRUNC('week',NOW())
                """,
                (int(user_id), int(user_id)),
            )
            return cur.fetchone() or {}


def _option_label(event, option):
    if int(option) == 0:
        return str(event.get("home_team") or "Home")
    if int(option) == 1:
        return str(event.get("away_team") or "Away")
    return "Draw / No Result"


def _send_result_notifications(settings, force=False):
    if not settings.get("master_enabled") or not settings.get("quiz_enabled"):
        return 0
    if not force and not v83._within_send_hours(settings):
        return 0
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.*,e.provider_event_id,e.home_team,e.away_team,e.home_score,e.away_score,
                       e.result_text,e.outcome_option,l.reachable_bot,l.opt_out
                FROM live_sports_predictions p
                JOIN live_sports_events e ON e.id=p.event_id
                LEFT JOIN intelligence_leads l USING(telegram_user_id)
                WHERE p.settled_at IS NOT NULL AND p.result_notified_at IS NULL
                  AND COALESCE(l.reachable_bot,FALSE)=TRUE AND COALESCE(l.opt_out,FALSE)=FALSE
                ORDER BY p.settled_at ASC LIMIT 50
                """
            )
            rows = cur.fetchall()
    sent = 0
    for row in rows:
        uid = int(row["telegram_user_id"])
        correct = bool(row.get("is_correct"))
        score = _combined_weekly_score(uid)
        result_line = str(row.get("result_text") or "").strip()
        if not result_line:
            hs = row.get("home_score")
            aws = row.get("away_score")
            result_line = f"{row['home_team']} {hs or '-'} – {aws or '-'} {row['away_team']}"
        selected = _option_label(row, row.get("selected_option"))
        outcome = _option_label(row, row.get("outcome_option"))
        text = (
            "🏆 <b>BETROXY Match Challenge Result</b>\n\n"
            f"{html.escape(str(row['home_team']))} vs {html.escape(str(row['away_team']))}\n"
            f"Result: <b>{html.escape(result_line[:300])}</b>\n"
            f"Your prediction: <b>{html.escape(selected)}</b>\n"
            f"Outcome: <b>{html.escape(outcome)}</b>\n\n"
            + ("✅ <b>Correct! +10 points</b>" if correct else "❌ Not this time. <b>0 points</b>")
            + f"\nWeekly score: <b>{int(score.get('score') or 0)} points</b>"
        )
        key = f"live_result:{row['provider_event_id']}"
        ok = v83._send_claimed(uid, "quiz_result", key, text, [[{"text": "🏆 Updates & Quiz", "url": v83.PREFERENCES_DEEPLINK}]])
        if ok:
            with bot.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE live_sports_predictions SET result_notified_at=NOW() WHERE id=%s", (int(row["id"]),))
                conn.commit()
            sent += 1
        time.sleep(0.08)
    return sent


def _v86_send_next_best_actions(settings):
    now = datetime.now(timezone.utc)
    live_event = _next_live_event() if settings.get("live_predictions_enabled", True) else None
    for lead in v83._subscribed_rows():
        uid = int(lead["telegram_user_id"])
        if not v83._prepare_for_contact(lead, int(settings.get("max_weekly_messages") or 3)):
            continue
        inactive = now - (lead.get("last_seen_at") or now)
        last_contact = lead.get("last_contact_at")
        since_contact = now - last_contact if last_contact else timedelta(days=999)

        if settings.get("reactivation_enabled") and inactive >= timedelta(days=7) and since_contact >= timedelta(days=7):
            key = f"reactivation:{v83._local_now().strftime('%Y-%m-%d')}"
            kb = [[{"text": "🚀 Open App", "url": v83.OPEN_APP_URL}, {"text": "🎁 Promotions", "url": v83.PROMOTIONS_URL}], [{"text": "🎧 Support", "url": v83.SUPPORT_URL}]]
            v83._send_claimed(uid, "reactivation", key, "👋 <b>Welcome back to BETROXY</b>\n\nYour account access, Promotions and support are one tap away. Choose what you need below.", kb)
            time.sleep(0.08)
            continue

        last_quiz = v83._last_action_at(uid, "quiz")
        if settings.get("quiz_enabled") and lead.get("quiz_rewards") and (not last_quiz or now-last_quiz >= timedelta(days=3)):
            if live_event and _send_live_prediction(lead, live_event):
                time.sleep(0.08)
                continue
            if v83._send_quiz(lead):
                time.sleep(0.08)
                continue

        last_promo = v83._last_action_at(uid, "promotion")
        if settings.get("promotions_enabled") and lead.get("promotions") and (not last_promo or now-last_promo >= timedelta(days=4)) and since_contact >= timedelta(hours=24):
            key = f"promotion:{v83._local_now().strftime('%Y-%m-%d')}"
            kb = [[{"text": "🎁 View Promotions", "url": v83.PROMOTIONS_URL}], [{"text": "🔕 Preferences", "url": v83.PREFERENCES_DEEPLINK}]]
            v83._send_claimed(uid, "promotion", key, "🎁 <b>BETROXY Promotions</b>\n\nOpen the Promotions section to see the offers currently available in the app.", kb)
            time.sleep(0.08)
            continue

        last_sports = v83._last_action_at(uid, "sports")
        if settings.get("sports_enabled") and lead.get("sports_updates") and (not last_sports or now-last_sports >= timedelta(days=2)) and since_contact >= timedelta(hours=24):
            key = f"sports:{v83._local_now().strftime('%Y-%m-%d')}"
            kb = [[{"text": "🏏 Open Sportsbook", "url": v83.SPORTSBOOK_URL}], [{"text": "🏆 Quiz & Rewards", "url": v83.PREFERENCES_DEEPLINK}]]
            v83._send_claimed(uid, "sports", key, "🏏 <b>BETROXY Sports Update</b>\n\nLive fixtures and sports markets are available in Sportsbook. Tap below to see what is on now.", kb)
            time.sleep(0.08)
            continue

        if settings.get("reminders_enabled") and since_contact >= timedelta(hours=24) and inactive >= timedelta(hours=24):
            key = f"reminder:{v83._local_now().strftime('%Y-%m-%d')}"
            kb = [[{"text": "🚀 Open BETROXY App", "url": v83.OPEN_APP_URL}], [{"text": "🎧 Help & Support", "url": v83.SUPPORT_URL}]]
            v83._send_claimed(uid, "reminder", key, "👋 <b>Need help getting started?</b>\n\nYour BETROXY app and support are available below.", kb)
            time.sleep(0.08)


def _v86_weekly_leaderboard(settings):
    local = v83._local_now()
    if not settings.get("quiz_enabled") or local.weekday() != 6 or local.hour < 20:
        return
    week_key = f"leaderboard:{local.strftime('%G-W%V')}"
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT x.telegram_user_id,SUM(x.points) AS score,
                       COALESCE(NULLIF(l.first_name,''),NULLIF(l.telegram_username,''),'Player') AS name,
                       MIN(x.t) AS first_answer
                FROM (
                    SELECT telegram_user_id,points,answered_at AS t FROM engagement_quiz_answers
                    WHERE answered_at>=DATE_TRUNC('week',NOW())
                    UNION ALL
                    SELECT telegram_user_id,points,predicted_at AS t FROM live_sports_predictions
                    WHERE predicted_at>=DATE_TRUNC('week',NOW())
                ) x
                LEFT JOIN intelligence_leads l USING(telegram_user_id)
                GROUP BY x.telegram_user_id,l.first_name,l.telegram_username
                ORDER BY score DESC, first_answer ASC LIMIT 5
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
        v83._send_claimed(int(row["telegram_user_id"]), "leaderboard", week_key, text, [[{"text": "🏆 My Quiz Preferences", "url": v83.PREFERENCES_DEEPLINK}]])
        time.sleep(0.08)


def _sports_stats():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM live_sports_events WHERE event_time BETWEEN NOW() AND NOW()+INTERVAL '24 hours'")
            upcoming = int((cur.fetchone() or {}).get("n") or 0)
            cur.execute("SELECT COUNT(*) AS n FROM live_sports_predictions WHERE predicted_at>=NOW()-INTERVAL '7 days'")
            predictions = int((cur.fetchone() or {}).get("n") or 0)
            cur.execute("SELECT COUNT(*) AS n FROM live_sports_predictions WHERE settled_at>=NOW()-INTERVAL '7 days' AND is_correct=TRUE")
            correct = int((cur.fetchone() or {}).get("n") or 0)
    return upcoming, predictions, correct


def _v86_autopilot_text():
    base = _old_autopilot_text()
    s = v83._settings()
    st = _state()
    upcoming, predictions, correct = _sports_stats()
    live = "ON ✅" if s.get("live_predictions_enabled", True) else "OFF"
    provider = "TheSportsDB"
    last = st.get("last_success_at")
    last_txt = last.strftime("%d %b %H:%M UTC") if last else "Waiting for first successful refresh"
    err = str(st.get("last_error") or "").strip()
    extra = (
        f"\n\n⚡ Live match predictions: <b>{live}</b>\n"
        f"Sports feed: <b>{provider}</b>\n"
        f"Upcoming events 24h: <b>{upcoming}</b>\n"
        f"Live predictions 7d: <b>{predictions}</b>\n"
        f"Correct live predictions 7d: <b>{correct}</b>\n"
        f"Last sports refresh: <b>{html.escape(last_txt)}</b>"
    )
    if err:
        extra += f"\nFeed note: <code>{html.escape(err[:250])}</code>"
    return base + extra


def _v86_autopilot_keyboard():
    markup = _old_autopilot_keyboard()
    rows = [list(r) for r in markup.inline_keyboard]
    s = v83._settings()
    label = f"⚡ Live Match Predictions: {'ON ✅' if s.get('live_predictions_enabled', True) else 'OFF'}"
    insert_at = max(0, len(rows)-2)
    rows.insert(insert_at, [bot.InlineKeyboardButton(label, callback_data="autopilot_toggle:live_predictions_enabled")])
    rows.insert(insert_at+1, [bot.InlineKeyboardButton("🔄 Refresh Live Sports Feed", callback_data="autopilot_refresh_sports")])
    return bot.InlineKeyboardMarkup(rows)


def _v86_worker_cycle(force=False):
    try:
        _refresh_live_events_if_due(force=force)
    except Exception:
        bot.logger.exception("V86_SPORTS_DISCOVERY_FAILED")
    # V85/V83 performs the normal safe engagement cycle + Business digest.
    _old_worker_cycle(force=force)
    try:
        _settle_live_events_if_due(force=force)
        _send_result_notifications(v83._settings(), force=force)
    except Exception:
        bot.logger.exception("V86_SPORTS_POSTMATCH_CYCLE_FAILED")


async def v86_callback_handler(update, context):
    q = update.callback_query
    data = (q.data or "") if q else ""
    if not q:
        return await _previous_callback_handler(update, context)

    if data.startswith("livepred:"):
        uid = int(q.from_user.id)
        try:
            _, event_id, option = data.split(":", 2)
            event_id, option = int(event_id), int(option)
            if option not in (0, 1, 2):
                raise ValueError("Invalid option")
            with bot.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM live_sports_events WHERE id=%s", (event_id,))
                    event = cur.fetchone()
                    if not event:
                        await q.answer("Match not found.", show_alert=True)
                        return
                    if event.get("settled_at") or event.get("event_time") <= datetime.now(timezone.utc):
                        await q.answer("Predictions for this match are closed.", show_alert=True)
                        return
                    cur.execute(
                        """
                        INSERT INTO live_sports_predictions(event_id,telegram_user_id,selected_option)
                        VALUES (%s,%s,%s)
                        ON CONFLICT(event_id,telegram_user_id) DO NOTHING
                        RETURNING id
                        """,
                        (event_id, uid, option),
                    )
                    inserted = cur.fetchone()
                conn.commit()
            if not inserted:
                await q.answer("Your prediction is already recorded for this match.", show_alert=True)
                return
            v83._touch_user(uid, f"live_prediction:{event_id}")
            with bot.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE engagement_log SET clicked_at=NOW() WHERE telegram_user_id=%s AND message_key=%s",
                        (uid, f"live_prediction:{event['provider_event_id']}"),
                    )
                conn.commit()
            selected = _option_label(event, option)
            score = _combined_weekly_score(uid)
            await q.answer(
                f"✅ Prediction recorded: {selected}. Result will be checked automatically. Weekly score: {int(score.get('score') or 0)}",
                show_alert=True,
            )
            return
        except Exception:
            bot.logger.exception("V86_LIVE_PREDICTION_ANSWER_FAILED")
            try:
                await q.answer("Could not save your prediction. Please try again.", show_alert=True)
            except Exception:
                pass
            return

    if data == "autopilot_toggle:live_predictions_enabled":
        if not bot.is_admin(q.from_user.id):
            await q.answer("Admin only", show_alert=True)
            return
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE engagement_engine_settings SET live_predictions_enabled=NOT live_predictions_enabled,updated_at=NOW() WHERE id=1")
                cur.execute("INSERT INTO intelligence_audit_log(actor_user_id,action,detail) VALUES (%s,'autopilot_toggle','live_predictions_enabled')", (q.from_user.id,))
            conn.commit()
        await q.answer("Updated")
        try:
            await q.edit_message_text(v83._autopilot_text(), parse_mode=bot.ParseMode.HTML, reply_markup=v83._autopilot_keyboard())
        except Exception:
            pass
        return

    if data == "autopilot_refresh_sports":
        if not bot.is_admin(q.from_user.id):
            await q.answer("Admin only", show_alert=True)
            return
        await q.answer("Sports refresh started")
        threading.Thread(target=_refresh_live_events_if_due, kwargs={"force": True}, daemon=True).start()
        return

    return await _previous_callback_handler(update, context)


try:
    _ensure_v86_schema()
    bot.logger.warning("V86_LIVE_SPORTS_SCHEMA ready=on provider=TheSportsDB")
except Exception:
    bot.logger.exception("V86_LIVE_SPORTS_SCHEMA_FAILED")

# Patch V83 globals used dynamically by its worker/callback functions.
v83._send_next_best_actions = _v86_send_next_best_actions
v83._weekly_score = _combined_weekly_score
v83._send_weekly_leaderboard = _v86_weekly_leaderboard
v83._autopilot_text = _v86_autopilot_text
v83._autopilot_keyboard = _v86_autopilot_keyboard
v83._worker_cycle = _v86_worker_cycle
bot.callback_handler = v86_callback_handler

bot.logger.warning(
    "V86_LIVE_SPORTS_AUTOPILOT active=on discovery=hourly sports=cricket+soccer "
    "live_prediction=on auto_settlement=on result_notify=on combined_leaderboard=on trivia_fallback=on"
)


if __name__ == "__main__":
    v63.apply_signup_cta()
    v85._enable_smart_reply_without_reset()
    threading.Thread(target=v83._worker_loop, name="betroxy-engagement-worker", daemon=True).start()
    bot.logger.warning("V86 polling handover delay=12s")
    time.sleep(12)
    bot.main()
