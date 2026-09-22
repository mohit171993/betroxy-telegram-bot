import json
import os
import hmac
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg
from psycopg.rows import dict_row

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
SECRET = os.getenv("REPORT_METRICS_SECRET", "").strip()


def scalar(cur, sql, params=()):
    try:
        cur.execute(sql, params)
        row = cur.fetchone()
        if not row:
            return 0
        if isinstance(row, dict):
            return int(next(iter(row.values())) or 0)
        return int(row[0] or 0)
    except Exception:
        return 0


def table_exists(cur, name):
    cur.execute(
        "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_schema=current_schema() AND table_name=%s)",
        (name,),
    )
    row = cur.fetchone()
    return bool(next(iter(row.values())) if isinstance(row, dict) else row[0])


def metrics():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute("SET LOCAL statement_timeout='10s'")

            parts = []
            recent_parts = []
            if table_exists(cur, "intelligence_leads"):
                parts.append("SELECT telegram_user_id AS uid FROM intelligence_leads WHERE telegram_user_id IS NOT NULL")
                recent_parts.append("SELECT telegram_user_id AS uid FROM intelligence_leads WHERE telegram_user_id IS NOT NULL AND first_seen_at >= %s")
            if table_exists(cur, "referrals"):
                parts.append("SELECT telegram_user_id AS uid FROM referrals WHERE telegram_user_id IS NOT NULL")
                recent_parts.append("SELECT telegram_user_id AS uid FROM referrals WHERE telegram_user_id IS NOT NULL AND joined_at >= %s")
            if table_exists(cur, "telegram_business_enquiries"):
                parts.append("SELECT customer_user_id AS uid FROM telegram_business_enquiries WHERE customer_user_id IS NOT NULL")
                recent_parts.append("SELECT customer_user_id AS uid FROM telegram_business_enquiries WHERE customer_user_id IS NOT NULL AND first_message_at >= %s")

            leads = 0
            leads_24h = 0
            if parts:
                leads = scalar(cur, "SELECT COUNT(DISTINCT uid) FROM (" + " UNION ALL ".join(parts) + ") q")
            if recent_parts:
                sql = "SELECT COUNT(DISTINCT uid) FROM (" + " UNION ALL ".join(recent_parts) + ") q"
                try:
                    cur.execute(sql, tuple([cutoff] * len(recent_parts)))
                    row = cur.fetchone()
                    leads_24h = int(next(iter(row.values())) if isinstance(row, dict) else row[0])
                except Exception:
                    leads_24h = 0

            verified = 0
            verified_24h = 0
            if table_exists(cur, "v110_mobile_verifications"):
                verified = scalar(
                    cur,
                    "SELECT COUNT(DISTINCT telegram_user_id) FROM v110_mobile_verifications WHERE verified_via='telegram_contact'"
                )
                verified_24h = scalar(
                    cur,
                    "SELECT COUNT(DISTINCT telegram_user_id) FROM v110_mobile_verifications WHERE verified_via='telegram_contact' AND verified_at >= %s",
                    (cutoff,),
                )

            bot_users = leads

            return {
                "bot_users": bot_users,
                "leads": leads,
                "leads_24h": leads_24h,
                "registration_clicks": 0,
                "registration_clicks_24h": 0,
                "completed_registrations": None,
                "verified": verified,
                "verified_24h": verified_24h,
                "registration_note": "Completed external-site registrations are not available unless the destination sends a conversion event back.",
            }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?", 1)[0] == "/health":
            raw = b"ok"
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return

        if self.path.split("?", 1)[0] != "/report-metrics":
            self.send_response(404)
            self.end_headers()
            return

        supplied = self.headers.get("X-Report-Key", "")
        if not SECRET or not hmac.compare_digest(SECRET, supplied):
            self.send_response(403)
            self.end_headers()
            return

        try:
            raw = json.dumps(metrics(), separators=(",", ":")).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except Exception:
            raw = b'{"error":"metrics_unavailable"}'
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    def log_message(self, format, *args):
        return


if __name__ == "__main__":
    if not DATABASE_URL:
        raise SystemExit("DATABASE_URL missing")
    port = int(os.getenv("PORT", "8080"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
