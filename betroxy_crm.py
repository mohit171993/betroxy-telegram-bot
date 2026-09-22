"""BETROXY-only operational CRM. No product, campaign or payment implementation.

The view reads existing records; only explicit CRM actions write state/audit.
DNC additionally applies the existing opt-out flags. No consent is ever inferred.
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Callable
from zoneinfo import ZoneInfo

VERSION = "2026-09-22-admin-parity-v1"
TEST_USERNAME = "mohit_97saxena"
DUBAI = ZoneInfo("Asia/Dubai")
STATUSES = {"new", "contacted", "no_answer", "interested", "converted", "dnc"}
QUEUES = {
    "all": "TRUE", "new": "status='new'", "unassigned": "assigned_to IS NULL",
    "mobile": "mobile_number<>''", "verified": "verified=TRUE",
    "unverified": "verified=FALSE", "followup": "status IN ('contacted','no_answer')",
    "due": "next_followup_at IS NOT NULL AND next_followup_at<=CURRENT_TIMESTAMP AND status NOT IN ('dnc','converted')",
    "interested": "status='interested'", "converted": "status='converted'", "dnc": "status='dnc'",
    "test": "is_test=TRUE",
}


def normalize_phone(value: object) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    return "+" + digits if re.fullmatch(r"[1-9]\d{7,14}", digits) else None


def own_contact(user_id: int, contact_user_id: object, phone: object) -> str | None:
    try:
        if int(contact_user_id) != int(user_id):
            return None
    except (TypeError, ValueError):
        return None
    return normalize_phone(phone)


def display_time(value: object) -> str:
    if not value:
        return "—"
    try:
        dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(DUBAI).strftime("%d %b %Y %H:%M Dubai")
    except (ValueError, TypeError):
        return "Unrecognised timestamp"


def followup_time(text: str, now: datetime | None = None) -> datetime | None:
    value = str(text).strip().lower()
    if value == "clear":
        return None
    now = now or datetime.now(timezone.utc)
    match = re.fullmatch(r"(\d{1,3})([hd])", value)
    if match:
        n = int(match[1])
        result = now + timedelta(**{"hours" if match[2] == "h" else "days": n})
    else:
        result = datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=DUBAI).astimezone(timezone.utc)
    if not now < result <= now + timedelta(days=366):
        raise ValueError("Choose a future time within one year, for example 2h or 2026-09-25 15:00 Dubai.")
    return result


def csv_cell(value: object) -> str:
    text = "" if value is None else str(value)
    # Excel formula injection protection also keeps international phones as text.
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else text


def phone_sql(column: str) -> str:
    expr = f"COALESCE({column},'')"
    for char in ("+", " ", "-", "(", ")"):
        expr = f"REPLACE({expr},'{char}','')"
    return expr


# Every registered identity source is included, even if not in intelligence_leads.
# This is a live projection: new arrivals need no scheduled backfill or copying.
VIEW_SQL = f"""
CREATE OR REPLACE VIEW betroxy_crm_leads_v1 AS
WITH identities AS (
 SELECT telegram_user_id AS uid,telegram_username AS username,first_name,last_name,
        first_seen_at AS first_at,last_seen_at AS last_at,
        CASE WHEN reachable_bot THEN 1 ELSE 0 END AS bot_record,0 AS dm_record
 FROM intelligence_leads
 UNION ALL SELECT telegram_user_id,telegram_username,first_name,last_name,joined_at,joined_at,1,0 FROM referrals
 UNION ALL SELECT customer_user_id,customer_username,customer_first_name,customer_last_name,first_message_at,last_message_at,0,1 FROM telegram_business_enquiries
 UNION ALL SELECT telegram_user_id,NULL,NULL,NULL,updated_at,updated_at,0,0 FROM user_contact_profiles
 UNION ALL SELECT telegram_user_id,NULL,NULL,NULL,verified_at,updated_at,0,0 FROM v110_mobile_verifications
 UNION ALL SELECT telegram_user_id,telegram_username,NULL,NULL,started_at,COALESCE(completed_at,started_at),1,0 FROM v110_quiz_entries
 UNION ALL SELECT telegram_user_id,telegram_username,NULL,NULL,started_at,COALESCE(completed_at,started_at),1,0 FROM mega_quiz_entries
 UNION ALL SELECT telegram_user_id,NULL,NULL,NULL,created_at,COALESCE(delivered_at,issued_at,created_at),0,0 FROM reward_awards
 UNION ALL SELECT telegram_user_id,NULL,NULL,NULL,updated_at,updated_at,0,0 FROM engagement_subscriptions
), grouped AS (
 SELECT uid,MAX(NULLIF(username,'')) AS username,MAX(NULLIF(first_name,'')) AS first_name,
        MAX(NULLIF(last_name,'')) AS last_name,MIN(first_at) AS first_at,MAX(last_at) AS last_at,
        MAX(bot_record) AS bot_record,MAX(dm_record) AS dm_record
 FROM identities WHERE uid IS NOT NULL AND uid>0 GROUP BY uid
)
SELECT g.uid AS telegram_user_id,
 COALESCE(NULLIF(l.telegram_username,''),NULLIF(r.telegram_username,''),g.username,'') AS username,
 COALESCE(NULLIF(l.first_name,''),NULLIF(r.first_name,''),g.first_name,'') AS first_name,
 COALESCE(NULLIF(l.last_name,''),NULLIF(r.last_name,''),g.last_name,'') AS last_name,
 g.first_at AS first_seen_at,g.last_at AS last_activity_at,
 g.bot_record,g.dm_record,
 CASE WHEN g.bot_record=1 AND g.dm_record=1 THEN 'bot + business'
      WHEN g.dm_record=1 THEN 'business'
      WHEN g.bot_record=1 THEN 'bot' ELSE 'historical record' END AS source,
 COALESCE(r.start_payload,'') AS start_payload,
 CASE WHEN p.mobile_removed_at IS NOT NULL THEN ''
      ELSE COALESCE(NULLIF(TRIM(p.mobile_number),''),NULLIF(TRIM(v.mobile_number),''),'') END AS mobile_number,
 CASE WHEN p.mobile_removed_at IS NULL AND {phone_sql('p.mobile_number')}<>''
       AND {phone_sql('p.mobile_number')}={phone_sql('v.mobile_number')}
       AND v.verified_via='telegram_contact' THEN TRUE ELSE FALSE END AS verified,
 v.verified_at,COALESCE(v.verified_via,'') AS verification_method,
 COALESCE(c.marketing_calls,FALSE) AS calls_consent,
 COALESCE(c.whatsapp_updates,FALSE) AS whatsapp_consent,
 c.consented_at,
 COALESCE(l.opt_out,FALSE) AS opted_out,
 CASE WHEN COALESCE(l.opt_out,FALSE) THEN 'dnc' ELSE COALESCE(s.status,'new') END AS status,
 COALESCE(s.status,'new') AS stored_status,
 s.assigned_to,COALESCE(s.assigned_name,'') AS assigned_name,
 s.next_followup_at,COALESCE(s.last_note,'') AS last_note,COALESCE(s.version,0) AS version,
 CASE WHEN CAST(g.uid AS TEXT)=(SELECT value FROM betroxy_crm_meta WHERE key='test_user_id') THEN TRUE ELSE FALSE END AS is_test
FROM grouped g
LEFT JOIN intelligence_leads l ON l.telegram_user_id=g.uid
LEFT JOIN referrals r ON r.telegram_user_id=g.uid
LEFT JOIN user_contact_profiles p ON p.telegram_user_id=g.uid
LEFT JOIN v110_mobile_verifications v ON v.telegram_user_id=g.uid
LEFT JOIN v110_lead_consents c ON c.telegram_user_id=g.uid
LEFT JOIN betroxy_crm_state s ON s.telegram_user_id=g.uid
"""


class CRM:
    def __init__(self, get_db: Callable, authorize: Callable[[int], bool], admin_id: int):
        self.get_db, self.authorize, self.admin_id = get_db, authorize, int(admin_id)

    def setup(self) -> dict:
        """One transaction; no existing source table or user record is rewritten."""
        with self.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""CREATE TABLE IF NOT EXISTS betroxy_crm_state (
                    telegram_user_id BIGINT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new','contacted','no_answer','interested','converted','dnc')),
                    assigned_to BIGINT,assigned_name TEXT,next_followup_at TIMESTAMPTZ,
                    last_note TEXT,version INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
                cur.execute("""CREATE TABLE IF NOT EXISTS betroxy_crm_audit (
                    event_key TEXT PRIMARY KEY,telegram_user_id BIGINT NOT NULL,actor_id BIGINT NOT NULL,
                    action TEXT NOT NULL,detail TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
                cur.execute("CREATE INDEX IF NOT EXISTS betroxy_crm_audit_uid ON betroxy_crm_audit(telegram_user_id,created_at)")
                cur.execute("CREATE INDEX IF NOT EXISTS betroxy_crm_due ON betroxy_crm_state(status,next_followup_at)")
                cur.execute("CREATE TABLE IF NOT EXISTS betroxy_crm_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)")
                cur.execute("""CREATE TABLE IF NOT EXISTS betroxy_crm_verification_tests (
                    telegram_user_id BIGINT PRIMARY KEY,phone_last_four TEXT NOT NULL,
                    verified_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
                cur.execute(VIEW_SQL)
                cur.execute("SELECT telegram_user_id FROM betroxy_crm_leads_v1 WHERE LOWER(username)=%s", (TEST_USERNAME,))
                matches = cur.fetchall()
                # Pin once to a numeric ID; a renamed/reassigned username never grants access.
                if len(matches) == 1:
                    cur.execute("INSERT INTO betroxy_crm_meta(key,value) VALUES('test_user_id',%s) ON CONFLICT(key) DO NOTHING", (str(matches[0]['telegram_user_id']),))
            conn.commit()
        return self.counts()

    def rows(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, args)
                return [dict(row) for row in cur.fetchall()]

    def lead(self, uid: int) -> dict | None:
        rows = self.rows("SELECT * FROM betroxy_crm_leads_v1 WHERE telegram_user_id=%s", (int(uid),))
        return rows[0] if rows else None

    def _where(self, queue: str, search: str = "") -> tuple[str, tuple]:
        if queue not in QUEUES:
            raise ValueError("Unknown work queue")
        where, args = QUEUES[queue], ()
        if search:
            # Literal substring search: %, _ and slashes are not SQL patterns.
            raw = str(search).strip().lower()[:100].lstrip('@')
            raw = raw.replace('!', '!!').replace('%', '!%').replace('_', '!_')
            term = '%' + raw + '%'
            where += " AND (LOWER(username) LIKE %s ESCAPE '!' OR LOWER(first_name || ' ' || last_name) LIKE %s ESCAPE '!' OR CAST(telegram_user_id AS TEXT) LIKE %s ESCAPE '!' OR mobile_number LIKE %s ESCAPE '!')"
            args = (term, term, term, term)
            if re.fullmatch(r'[+0-9 ()-]+', str(search).strip()):
                digits = re.sub(r'\D', '', str(search))
                if digits:
                    where = '(' + where + ') OR (' + QUEUES[queue] + ' AND ' + phone_sql('mobile_number') + " LIKE %s)"
                    args += ('%' + digits + '%',)
        return where, args

    def page(self, queue: str, index: int = 0, search: str = "") -> tuple[dict | None, int, int]:
        where, args = self._where(queue, search)
        order = "next_followup_at ASC,telegram_user_id" if queue == "due" else "first_seen_at DESC,telegram_user_id DESC"
        with self.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) AS n FROM betroxy_crm_leads_v1 WHERE {where}", args)
                total = int(cur.fetchone()['n'])
                index = min(max(0, int(index)), max(0, total - 1))
                cur.execute(f"SELECT * FROM betroxy_crm_leads_v1 WHERE {where} ORDER BY {order} LIMIT 1 OFFSET %s", args + (index,))
                row = cur.fetchone()
        return (dict(row) if row else None), index, total

    def counts(self) -> dict:
        columns = ["COUNT(*) AS total", "COALESCE(SUM(CASE WHEN is_test THEN 1 ELSE 0 END),0) AS test_accounts"]
        columns += [f"COALESCE(SUM(CASE WHEN {where} THEN 1 ELSE 0 END),0) AS {queue}" for queue, where in QUEUES.items() if queue != "all"]
        return self.rows("SELECT " + ','.join(columns) + " FROM betroxy_crm_leads_v1")[0]

    def attribution(self) -> list[dict]:
        return self.rows("""SELECT source,start_payload,COUNT(*) AS leads,
            SUM(CASE WHEN verified THEN 1 ELSE 0 END) AS verified,
            SUM(CASE WHEN status='converted' THEN 1 ELSE 0 END) AS converted
            FROM betroxy_crm_leads_v1 WHERE is_test=FALSE AND telegram_user_id<>%s
            GROUP BY source,start_payload ORDER BY COUNT(*) DESC LIMIT 20""", (self.admin_id,))

    def history(self, uid: int, page: int = 0) -> list[dict]:
        return self.rows("SELECT * FROM betroxy_crm_audit WHERE telegram_user_id=%s ORDER BY created_at DESC,event_key DESC LIMIT 10 OFFSET %s", (int(uid), max(0, int(page))*10))

    def mutate(self, uid: int, actor: int, action: str, value: object, version: int, event: str) -> bool:
        if not self.authorize(int(actor)):
            raise PermissionError("Admin access required")
        if action == "status":
            if value not in STATUSES:
                raise ValueError("Invalid CRM status")
            fields, values = "status=%s", (str(value),)
        elif action == "assign":
            fields, values = "assigned_to=%s,assigned_name=%s", (int(actor), str(value)[:100])
        elif action == "unassign":
            fields, values = "assigned_to=NULL,assigned_name=NULL", ()
        elif action == "note":
            note = str(value).strip()
            if not 1 <= len(note) <= 1000:
                raise ValueError("Notes must contain 1–1000 characters")
            fields, values = "last_note=%s", (note,)
        elif action == "followup":
            if value is not None and (not isinstance(value, datetime) or value.tzinfo is None
                                      or not datetime.now(timezone.utc) < value <= datetime.now(timezone.utc) + timedelta(days=366)):
                raise ValueError("Use a future, timezone-aware follow-up date within one year")
            fields, values = "next_followup_at=%s", (value,)
        else:
            raise ValueError("Unknown CRM action")
        if not self.lead(uid):
            raise ValueError("Lead not found")
        with self.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT event_key FROM betroxy_crm_audit WHERE event_key=%s", (str(event),))
                if cur.fetchone():
                    return False
                cur.execute("INSERT INTO betroxy_crm_state(telegram_user_id) VALUES(%s) ON CONFLICT(telegram_user_id) DO NOTHING", (int(uid),))
                cur.execute(f"UPDATE betroxy_crm_state SET {fields},version=version+1,updated_at=CURRENT_TIMESTAMP WHERE telegram_user_id=%s AND version=%s RETURNING version", values + (int(uid), int(version)))
                if not cur.fetchone():
                    return False
                cur.execute("INSERT INTO betroxy_crm_audit(event_key,telegram_user_id,actor_id,action,detail) VALUES(%s,%s,%s,%s,%s)", (str(event), int(uid), int(actor), action, json.dumps({'value': value}, ensure_ascii=False, default=str)))
                if action == "status" and value == "dnc":
                    cur.execute("""INSERT INTO intelligence_leads(telegram_user_id,opt_out,lifecycle_stage,last_event)
                        VALUES(%s,TRUE,'opted_out','crm_dnc') ON CONFLICT(telegram_user_id) DO UPDATE SET
                        opt_out=TRUE,lifecycle_stage='opted_out',last_event='crm_dnc'""", (int(uid),))
                    cur.execute("""UPDATE engagement_subscriptions SET master_enabled=FALSE,
                        sports_updates=FALSE,promotions=FALSE,quiz_rewards=FALSE,
                        last_preference_action_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
                        WHERE telegram_user_id=%s""", (int(uid),))
            conn.commit()
        return True

    def export_csv(self) -> bytes:
        # No 100/1000-row truncation. Stream the complete view in small batches.
        fields = ['telegram_user_id','username','first_name','last_name','mobile_number','verified',
                  'verification_method','verified_at','calls_consent','whatsapp_consent','consented_at',
                  'opted_out','status','assigned_to','assigned_name','next_followup_at','last_note',
                  'source','start_payload','first_seen_at','last_activity_at','is_test']
        output = io.StringIO(newline='')
        writer = csv.writer(output)
        writer.writerow(fields)
        with self.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM betroxy_crm_leads_v1 ORDER BY telegram_user_id")
                while True:
                    batch = cur.fetchmany(500)
                    if not batch:
                        break
                    for row in batch:
                        writer.writerow([csv_cell(row.get(key)) for key in fields])
        return ('\ufeff' + output.getvalue()).encode('utf-8')

    def reminders_paused(self) -> bool:
        rows = self.rows("SELECT value FROM betroxy_crm_meta WHERE key='private_reminders_paused'")
        return bool(rows and rows[0]['value'] == '1')

    def set_reminders_paused(self, paused: bool, actor: int, event: str) -> None:
        if not self.authorize(int(actor)):
            raise PermissionError("Admin access required")
        with self.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO betroxy_crm_audit(event_key,telegram_user_id,actor_id,action,detail) VALUES(%s,0,%s,'reminder_pause',%s) ON CONFLICT(event_key) DO NOTHING RETURNING event_key",
                            (str(event), int(actor), json.dumps({'paused': bool(paused)})))
                if not cur.fetchone():
                    return
                cur.execute("INSERT INTO betroxy_crm_meta(key,value) VALUES('private_reminders_paused',%s) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value", ('1' if paused else '0',))
            conn.commit()

    def marketing_blocked(self, uid: int) -> bool:
        return bool(self.rows("SELECT telegram_user_id FROM betroxy_crm_state WHERE telegram_user_id=%s AND status='dnc' UNION SELECT telegram_user_id FROM intelligence_leads WHERE telegram_user_id=%s AND opt_out=TRUE", (int(uid), int(uid))))

    def test_user_id(self) -> int | None:
        rows = self.rows("SELECT value FROM betroxy_crm_meta WHERE key='test_user_id'")
        return int(rows[0]['value']) if rows else None

    def save_own_contact(self, uid: int, contact_uid: object, mobile: str, event: str) -> str:
        normalized = own_contact(uid, contact_uid, mobile)
        if not normalized:
            raise ValueError("Only an own Telegram contact can verify a phone")
        with self.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT event_key FROM betroxy_crm_audit WHERE event_key=%s", (event,))
                if cur.fetchone():
                    return normalized
                # Keep E.164 exactly as Telegram provided it. Do not turn a
                # 10-digit international E.164 phone into an assumed +91 number.
                cur.execute("""INSERT INTO user_contact_profiles(
                    telegram_user_id,mobile_number,mobile_source,mobile_consent_at,mobile_removed_at,updated_at)
                    VALUES(%s,%s,'telegram_contact_self_service',CURRENT_TIMESTAMP,NULL,CURRENT_TIMESTAMP)
                    ON CONFLICT(telegram_user_id) DO UPDATE SET mobile_number=EXCLUDED.mobile_number,
                    mobile_source=EXCLUDED.mobile_source,mobile_consent_at=CURRENT_TIMESTAMP,
                    mobile_removed_at=NULL,updated_at=CURRENT_TIMESTAMP""", (int(uid), normalized))
                cur.execute("""INSERT INTO v110_mobile_verifications(
                    telegram_user_id,mobile_number,verified_via,verified_at,updated_at)
                    VALUES(%s,%s,'telegram_contact',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
                    ON CONFLICT(telegram_user_id) DO UPDATE SET mobile_number=EXCLUDED.mobile_number,
                    verified_via='telegram_contact',verified_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP""", (int(uid), normalized))
                cur.execute("INSERT INTO betroxy_crm_audit(event_key,telegram_user_id,actor_id,action,detail) VALUES(%s,%s,%s,'self_contact_verified',%s)",
                            (event, int(uid), int(uid), json.dumps({'phone_last_four':normalized[-4:]})))
            conn.commit()
        return normalized

    def record_verification_test(self, uid: int, mobile: str) -> None:
        if self.test_user_id() != int(uid):
            raise PermissionError("Only the pinned test account may run this test")
        normalized = normalize_phone(mobile)
        if not normalized:
            raise ValueError("Invalid mobile")
        with self.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""INSERT INTO betroxy_crm_verification_tests(telegram_user_id,phone_last_four)
                    VALUES(%s,%s) ON CONFLICT(telegram_user_id) DO UPDATE SET
                    phone_last_four=EXCLUDED.phone_last_four,verified_at=CURRENT_TIMESTAMP""", (int(uid), normalized[-4:]))
            conn.commit()


def lead_card(row: dict) -> str:
    uid = int(row['telegram_user_id'])
    esc = lambda value: escape(str(value or '—'))
    name = ' '.join(x for x in (row['first_name'], row['last_name']) if x) or 'Unnamed user'
    blocked = row['status'] == 'dnc' or row['opted_out']
    lines = [f"<b>{esc(row['status'].replace('_',' ').upper())}</b>" + (' · 🧪 TEST ACCOUNT' if row['is_test'] else ''),
             f"👤 {esc(name)} · {esc('@'+row['username'] if row['username'] else '')}",
             f"🆔 Telegram ID: <code>{uid}</code>",
             f"📱 Saved mobile: <code>{esc(row['mobile_number'])}</code>",
             '🔐 Verified by Telegram contact ✅' if row['verified'] else '🔐 Not currently verified',
             f"📥 Source: {esc(row['source'])}", f"🎯 Start payload: <code>{esc(row['start_payload'])}</code>",
             f"👨‍💼 Assigned: {esc(row['assigned_name'] or row['assigned_to'] or 'Unassigned')}",
             f"🕒 First record: {display_time(row['first_seen_at'])}",
             f"🕘 Last recorded activity: {display_time(row['last_activity_at'])}",
             f"📞 Call consent: {'recorded' if row['calls_consent'] else 'not recorded'} · WhatsApp: {'recorded' if row['whatsapp_consent'] else 'not recorded'}"]
    if row['verified']:
        lines.append('Verified: ' + display_time(row['verified_at']))
    if blocked:
        lines.append('🚫 DO NOT CONTACT. CRM status changes do not restore marketing consent.')
    if row['next_followup_at']:
        lines.append('⏰ Follow-up: ' + display_time(row['next_followup_at']))
    if row['last_note']:
        lines.append('📝 Note: ' + escape(row['last_note'][:1000]))
    return '\n'.join(lines)


def may_whatsapp(row: dict) -> bool:
    return bool(normalize_phone(row.get('mobile_number')) and row.get('whatsapp_consent')
                and row.get('status') != 'dnc' and not row.get('opted_out'))
