"""BETROXY-only additive CRM. Legacy tables are read-only inputs.

No bot imports, customer sends, voucher calls or verification resets. All CRM
writes stay in btx_crm_* tables. Importing this module performs no I/O.
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

TEST_USERNAME = "mohit_97saxena"
STATUSES = ("new", "contacted", "no_answer", "interested", "converted", "not_interested", "dnc")
QUEUES = ("new", "unassigned", "all", "mobile", "due", "followup", "interested", "converted", "not_interested", "dnc", "test")
SOURCES = {
    "intelligence_leads": "telegram_user_id telegram_username first_name last_name source lifecycle_stage first_seen_at last_seen_at opt_out reachable_bot",
    "referrals": "telegram_user_id telegram_username first_name last_name start_payload joined_at",
    "telegram_business_enquiries": "customer_user_id customer_username customer_first_name customer_last_name first_message_at last_message_at lead_stage",
    "user_contact_profiles": "telegram_user_id mobile_number mobile_removed_at mobile_source mobile_consent_at updated_at",
    "v110_mobile_verifications": "telegram_user_id mobile_number verified_via verified_at updated_at",
    "v110_lead_consents": "telegram_user_id marketing_calls whatsapp_updates consented_at",
    "v110_quiz_entries": "telegram_user_id telegram_username started_at completed_at",
    "mega_quiz_entries": "telegram_user_id telegram_username started_at completed_at",
    "reward_awards": "telegram_user_id created_at",
}


def stamp(value):
    if not value:
        return None
    try:
        d = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def phone(value):
    digits = re.sub(r"\D", "", str(value or ""))
    return "+" + digits if re.fullmatch(r"[1-9]\d{7,14}", digits) else ""


def own_contact(uid, contact_uid, number):
    return bool(uid and contact_uid and int(uid) == int(contact_uid) and phone(number))


def csv_cell(value):
    text = "" if value is None else str(value)
    # Includes whitespace-prefixed formula payloads and +international phones.
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")) else text


def parse_followup(text, now=None):
    result = datetime.strptime(text.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo("Asia/Dubai")).astimezone(timezone.utc)
    if result <= (now or datetime.now(timezone.utc)):
        raise ValueError("Choose a future date/time in Dubai time.")
    return result


def merge_records(sources, states=(), test_uid=None):
    """Unify real IDs, never guess phones or infer consent from phone storage."""
    people, profiles, verifications, consents = {}, {}, {}, {}
    for table, rows in sources.items():
        for raw in rows:
            row = dict(raw)
            uid = int(row.get("telegram_user_id") or row.get("customer_user_id") or 0)
            if uid <= 0:
                continue
            p = people.setdefault(uid, dict(uid=uid, username="", name="", sources=set(), campaign="", first_seen=None, last_seen=None, status="new", assigned_to=None, note="", next_followup=None, legacy_opt_out=False, calls=False, whatsapp=False, phone="", verified=False, verified_at=None, is_test=uid == test_uid))
            username = row.get("telegram_username") or row.get("customer_username")
            name = " ".join(str(x) for x in (row.get("first_name") or row.get("customer_first_name"), row.get("last_name") or row.get("customer_last_name")) if x)
            if username and not p["username"]:
                p["username"] = str(username).lstrip("@")
            if name and not p["name"]:
                p["name"] = name
            route = {"referrals": "officialbot", "telegram_business_enquiries": "business_dm", "v110_quiz_entries": "daily_quiz", "mega_quiz_entries": "weekly_quiz", "reward_awards": "reward_history"}.get(table)
            if route:
                p["sources"].add(route)
            if table == "intelligence_leads":
                p["sources"].add(str(row.get("source") or "unknown"))
                p["legacy_opt_out"] = bool(row.get("opt_out"))
            stage = str(row.get("lifecycle_stage") or row.get("lead_stage") or "").lower()
            if stage in STATUSES and p["status"] == "new":
                p["status"] = stage
            if stage in {"opted_out", "dnc"}:
                p["legacy_opt_out"] = True
            if row.get("start_payload") and not p["campaign"]:
                p["campaign"] = str(row["start_payload"])
            dates = [stamp(row.get(k)) for k in ("first_seen_at", "last_seen_at", "joined_at", "first_message_at", "last_message_at", "started_at", "completed_at", "created_at")]
            dates = [d for d in dates if d]
            if dates:
                p["first_seen"] = min([p["first_seen"]] + dates) if p["first_seen"] else min(dates)
                p["last_seen"] = max([p["last_seen"]] + dates) if p["last_seen"] else max(dates)
            if table == "user_contact_profiles":
                profiles[uid] = row
            elif table == "v110_mobile_verifications":
                verifications[uid] = row
            elif table == "v110_lead_consents":
                consents[uid] = row
    for uid, p in people.items():
        profile, verified, consent = profiles.get(uid, {}), verifications.get(uid, {}), consents.get(uid, {})
        # A removed contact must not be resurrected from an old verification row.
        p["phone"] = "" if profile.get("mobile_removed_at") else str(profile.get("mobile_number") or (verified.get("mobile_number") if not profile else "") or "")
        p["verified"] = bool(not profile.get("mobile_removed_at") and phone(profile.get("mobile_number")) and phone(profile.get("mobile_number")) == phone(verified.get("mobile_number")) and verified.get("verified_via") == "telegram_contact")
        p["verified_at"] = stamp(verified.get("verified_at")) if p["verified"] else None
        p["calls"], p["whatsapp"] = bool(consent.get("marketing_calls")), bool(consent.get("whatsapp_updates"))
    for raw in states:
        state = dict(raw)
        p = people.get(int(state["telegram_user_id"]))
        if not p:
            continue
        for source, dest in (("status", "status"), ("assigned_to", "assigned_to"), ("last_note", "note"), ("next_followup_at", "next_followup")):
            if source == "status" and not state.get(source):
                continue
            p[dest] = state.get(source)
    for p in people.values():
        if p["legacy_opt_out"]:
            p["status"] = "dnc"
        p["sources"] = ", ".join(sorted(p["sources"])) or "stored_record"
        p["next_followup"] = stamp(p["next_followup"])
    return people


def select_queue(people, queue, search="", now=None):
    if queue not in QUEUES:
        raise ValueError("Unknown queue")
    now = now or datetime.now(timezone.utc)
    def matches(p):
        s = p["status"]
        return {"all": True, "new": s == "new", "unassigned": p["assigned_to"] is None,
                "mobile": bool(p["phone"]), "due": bool(p["next_followup"] and p["next_followup"] <= now and s not in {"dnc", "converted", "not_interested"}),
                "followup": s in {"contacted", "no_answer"}, "interested": s == "interested", "converted": s == "converted",
                "not_interested": s == "not_interested", "dnc": s == "dnc", "test": bool(p["is_test"])}[queue]
    q = str(search).strip().lower().lstrip("@")
    rows = [p for p in people.values() if matches(p) and (not q or q in " ".join(str(p[k] or "") for k in ("uid", "username", "name", "phone")).lower())]
    floor = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(rows, key=lambda p: ((p["next_followup"] or now) if queue == "due" else -(p["first_seen"] or floor).timestamp(), p["uid"]))


class Store:
    def __init__(self, connect, authorize):
        self.connect, self.authorize, self.columns, self.test_uid = connect, authorize, {}, None

    def read_sources(self):
        result = {}
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute("SET LOCAL statement_timeout='15s'")
                for table, wanted in SOURCES.items():
                    cols = self.columns.get(table, set())
                    selected = [c for c in wanted.split() if c in cols]
                    if not selected:
                        continue
                    # Both table and column names come exclusively from SOURCES.
                    cur.execute(f"SELECT {','.join(selected)} FROM {table}")
                    result[table] = cur.fetchall()
        return result

    def inspect(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute("SELECT table_name,column_name FROM information_schema.columns WHERE table_schema=current_schema()")
                for row in cur.fetchall():
                    self.columns.setdefault(row["table_name"], set()).add(row["column_name"])
        for table, key in (("referrals", "telegram_user_id"), ("intelligence_leads", "telegram_user_id")):
            if key not in self.columns.get(table, set()):
                raise RuntimeError("Required BETROXY identity source is missing: " + table)
        sources = self.read_sources()
        ids = {p["uid"] for p in merge_records(sources).values() if p["username"].lower() == TEST_USERNAME}
        if len(ids) != 1:
            raise RuntimeError("Test-account identity is absent or ambiguous; pilot cannot start")
        self.test_uid = next(iter(ids))
        return sources

    def setup(self):
        self.inspect()
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL lock_timeout='5s'")
                cur.execute("SELECT pg_advisory_xact_lock(220926, 1)")
                cur.execute("""CREATE TABLE IF NOT EXISTS btx_crm_state(
                    telegram_user_id BIGINT PRIMARY KEY, status TEXT CHECK(status IN ('new','contacted','no_answer','interested','converted','not_interested','dnc')),
                    assigned_to BIGINT,last_note TEXT,next_followup_at TIMESTAMPTZ,updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
                cur.execute("""CREATE TABLE IF NOT EXISTS btx_crm_audit(
                    id BIGSERIAL PRIMARY KEY,telegram_user_id BIGINT NOT NULL,actor_user_id BIGINT NOT NULL,
                    action TEXT NOT NULL,detail TEXT NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
                cur.execute("CREATE INDEX IF NOT EXISTS btx_crm_audit_user ON btx_crm_audit(telegram_user_id,id DESC)")
                cur.execute("""CREATE TABLE IF NOT EXISTS btx_crm_test_identity(
                    slot INTEGER PRIMARY KEY CHECK(slot=1),telegram_user_id BIGINT UNIQUE NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
                cur.execute("""CREATE TABLE IF NOT EXISTS btx_crm_verification_tests(
                    telegram_user_id BIGINT PRIMARY KEY,mobile_number TEXT NOT NULL,verified_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
                cur.execute("INSERT INTO btx_crm_test_identity(slot,telegram_user_id) VALUES(1,%s) ON CONFLICT DO NOTHING", (self.test_uid,))
                cur.execute("SELECT telegram_user_id FROM btx_crm_test_identity WHERE slot=1")
                pinned = int(cur.fetchone()["telegram_user_id"])
                if pinned != self.test_uid:
                    raise RuntimeError("Pinned tester conflicts with the username lookup; manual review required")
            conn.commit()
        return self.snapshot()

    def snapshot(self):
        sources = self.read_sources()
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute("SELECT * FROM btx_crm_state")
                states = cur.fetchall()
        return merge_records(sources, states, self.test_uid)

    def change(self, uid, actor, action, value):
        uid, actor = int(uid), int(actor)
        if not self.authorize(actor):
            raise PermissionError("Admin access required")
        person = self.snapshot().get(uid)
        if not person:
            raise ValueError("Lead not found")
        field = {"status": "status", "assign": "assigned_to", "note": "last_note", "followup": "next_followup_at"}.get(action)
        if not field:
            raise ValueError("Unknown action")
        if action == "status":
            if value not in STATUSES or (person["legacy_opt_out"] and value != "dnc"):
                raise ValueError("Invalid status or existing STOP/DNC protection")
        elif action == "assign":
            value = int(value) if value else None
            if value is not None and not self.authorize(value):
                raise ValueError("Assignee must already be an authorized BETROXY admin")
        elif action == "note":
            value = str(value).strip()
            if not value or len(value) > 1000:
                raise ValueError("Note must contain 1–1000 characters")
        elif action == "followup":
            if person["status"] in {"dnc", "converted", "not_interested"}:
                raise ValueError("This lead is not eligible for follow-up")
            if not isinstance(value, datetime) or value.tzinfo is None or value <= datetime.now(timezone.utc):
                raise ValueError("Follow-up must be a future timezone-aware time")
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO btx_crm_state(telegram_user_id) VALUES(%s) ON CONFLICT DO NOTHING", (uid,))
                cur.execute("SELECT * FROM btx_crm_state WHERE telegram_user_id=%s FOR UPDATE", (uid,))
                old = cur.fetchone()
                if action == "followup" and old.get("status") in {"dnc", "converted", "not_interested"}:
                    raise ValueError("Lead no longer eligible for follow-up")
                if action == "assign" and old.get("assigned_to") not in {None, actor, value}:
                    raise ValueError("Lead already assigned; unassign explicitly before reassignment")
                extra = ",next_followup_at=NULL" if action == "status" and value in {"dnc", "converted", "not_interested"} else ""
                cur.execute(f"UPDATE btx_crm_state SET {field}=%s,updated_at=NOW(){extra} WHERE telegram_user_id=%s", (value, uid))
                cur.execute("INSERT INTO btx_crm_audit(telegram_user_id,actor_user_id,action,detail) VALUES(%s,%s,%s,%s)",
                            (uid, actor, action, json.dumps({"before": old.get(field), "after": value}, default=str, ensure_ascii=False)))
            conn.commit()

    def history(self, uid):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT actor_user_id,action,detail,created_at FROM btx_crm_audit WHERE telegram_user_id=%s ORDER BY id DESC LIMIT 12", (int(uid),))
                return cur.fetchall()

    def suppressed(self, uid):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT status FROM btx_crm_state WHERE telegram_user_id=%s", (int(uid),))
                row = cur.fetchone() or {}
                return row.get("status") == "dnc"

    def test_busy(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                for table in ('v110_quiz_sessions', 'mega_quiz_sessions'):
                    if {'telegram_user_id','current_question_id','question_sent_at'} <= self.columns.get(table,set()):
                        cur.execute(f"SELECT 1 FROM {table} WHERE telegram_user_id=%s AND current_question_id IS NOT NULL AND question_sent_at>=NOW()-INTERVAL '2 minutes'", (self.test_uid,))
                        if cur.fetchone():
                            return True
        return False

    def save_test(self, uid, contact_uid, number):
        if int(uid) != self.test_uid or not own_contact(uid, contact_uid, number):
            raise PermissionError("Only the pinned tester's own Telegram contact is accepted")
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""INSERT INTO btx_crm_verification_tests(telegram_user_id,mobile_number)
                    VALUES(%s,%s) ON CONFLICT(telegram_user_id) DO UPDATE SET mobile_number=EXCLUDED.mobile_number,verified_at=NOW()""", (int(uid), phone(number)))
                cur.execute("INSERT INTO btx_crm_audit(telegram_user_id,actor_user_id,action,detail) VALUES(%s,%s,'test_contact_verified','{\"pilot_only\":true}')", (int(uid), int(uid)))
            conn.commit()

    def test_state(self):
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT telegram_user_id,verified_at FROM btx_crm_verification_tests WHERE telegram_user_id=%s", (self.test_uid,))
                return cur.fetchone() or {}

    def export(self):
        fields = ("uid", "username", "name", "phone", "verified", "verified_at", "calls", "whatsapp", "status", "assigned_to", "sources", "campaign", "first_seen", "last_seen", "next_followup", "note", "is_test")
        out = io.StringIO(newline="")
        writer = csv.writer(out)
        writer.writerow(fields)
        for row in select_queue(self.snapshot(), "all"):
            writer.writerow([csv_cell(row.get(key)) for key in fields])
        return out.getvalue().encode("utf-8-sig")
