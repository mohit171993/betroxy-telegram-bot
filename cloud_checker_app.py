import os
import threading
import time
import traceback

import psycopg
from flask import Flask, request, Response
from playwright.sync_api import sync_playwright

import cloud_checker_worker as checker

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
LOGIN_SETUP_TOKEN = os.getenv("LOGIN_SETUP_TOKEN", "").strip()
PORT = int(os.getenv("PORT", "8080"))

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing")
if not LOGIN_SETUP_TOKEN:
    raise RuntimeError("LOGIN_SETUP_TOKEN is missing")

app = Flask(__name__)


def db_conn():
    return psycopg.connect(DATABASE_URL)


def init_session_table():
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS instagram_checker_session (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    sessionid TEXT NOT NULL,
                    updated_at TIMESTAMPTZ DEFAULT NOW(),
                    CHECK (id = 1)
                )
                """
            )
        conn.commit()


def save_session(sessionid):
    sessionid = (sessionid or "").strip()
    if not sessionid:
        raise ValueError("Empty sessionid")
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO instagram_checker_session (id, sessionid, updated_at)
                VALUES (1, %s, NOW())
                ON CONFLICT (id) DO UPDATE
                SET sessionid=EXCLUDED.sessionid, updated_at=NOW()
                """,
                (sessionid,),
            )
        conn.commit()


def load_session():
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT sessionid FROM instagram_checker_session WHERE id=1")
            row = cur.fetchone()
    return row[0] if row else ""


def verify_session(sessionid):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        context.add_cookies([{
            "name": "sessionid",
            "value": sessionid,
            "domain": ".instagram.com",
            "path": "/",
            "httpOnly": True,
            "secure": True,
            "sameSite": "Lax",
        }])
        page = context.new_page()
        page.goto("https://www.instagram.com/accounts/edit/", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(4500)
        current_url = (page.url or "").lower()
        try:
            body = (page.locator("body").inner_text(timeout=4000) or "").lower()
        except Exception:
            body = ""
        cookies = context.cookies("https://www.instagram.com/")
        browser.close()

    still_has_session = any(c.get("name") == "sessionid" and c.get("value") for c in cookies)
    redirected_to_login = "/accounts/login" in current_url
    login_page_text = "log in" in body and "sign up" in body
    challenge = "/challenge" in current_url or "security code" in body or "confirm it's you" in body
    return still_has_session and not redirected_to_login and not login_page_text and not challenge


def html_page(message="", ok=False):
    status = "✅ " + message if ok and message else ("⚠️ " + message if message else "")
    return f"""<!doctype html>
<html><head><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>BETROXY Instagram Cloud Checker Setup</title>
<style>
body{{font-family:Arial,sans-serif;background:#0c1512;color:#eef8f1;margin:0;padding:24px}}
.card{{max-width:680px;margin:30px auto;background:#13231d;border:1px solid #2e5143;border-radius:16px;padding:24px}}
h1{{margin-top:0}} input{{width:100%;box-sizing:border-box;padding:12px;margin:7px 0 14px;border-radius:9px;border:1px solid #456b5a;background:#0c1512;color:white}}
button{{padding:12px 18px;border:0;border-radius:9px;background:#43d17a;color:#06110a;font-weight:700;cursor:pointer}}
.small{{font-size:13px;color:#b7c9bf;line-height:1.55}} .status{{padding:12px;border-radius:9px;background:#0f1d18;margin-bottom:16px}}
code{{background:#0c1512;padding:2px 5px;border-radius:4px}} a{{color:#76e6a2}}
</style></head><body><div class='card'>
<h1>BETROXY Instagram Cloud Checker</h1>
{f"<div class='status'>{status}</div>" if status else ""}
<p><b>One-time setup:</b> log into the spare Instagram account normally in Chrome, then paste its <code>sessionid</code> cookie below. This avoids Instagram blocking automated login forms.</p>
<form method='post' action='/session'>
<input type='hidden' name='token' value='{LOGIN_SETUP_TOKEN}'>
<label>Instagram sessionid</label><input name='sessionid' type='password' autocomplete='off' required>
<button type='submit'>Verify and save session</button>
</form>
<p class='small'>How to get it in Chrome: open instagram.com while logged in → press F12 → Application → Cookies → https://www.instagram.com → find <code>sessionid</code> → copy only its Value. Do not send that value in chat; paste it directly into this protected page.</p>
<p class='small'>The session is verified against Instagram and then stored in the project database so it survives Railway redeployments.</p>
</div></body></html>"""


def authorized(req):
    return (req.args.get("token") or req.form.get("token") or "") == LOGIN_SETUP_TOKEN


@app.get("/")
def root():
    if not authorized(request):
        return Response("Not found", status=404)
    existing = bool(load_session())
    return html_page("A saved Instagram session already exists." if existing else "Cloud checker is waiting for Instagram session setup.", existing)


@app.post("/session")
def session_route():
    if not authorized(request):
        return Response("Not found", status=404)
    sessionid = (request.form.get("sessionid") or "").strip()
    try:
        if not verify_session(sessionid):
            return html_page("That sessionid was not accepted as a logged-in Instagram session. Make sure you copied only the sessionid Value from a currently logged-in Instagram tab.", False)
        save_session(sessionid)
        return html_page("Instagram session verified and saved. Cloud checker is ready.", True)
    except Exception as exc:
        traceback.print_exc()
        return html_page(f"Session verification failed: {type(exc).__name__}: {exc}", False)


@app.get("/health")
def health():
    return {"ok": True, "instagram_session_ready": bool(load_session())}


def worker_loop():
    checker.log("CLOUD_CHECKER_APP_WORKER_START")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"])
        while True:
            try:
                sessionid = load_session()
                if not sessionid:
                    checker.log("CLOUD_CHECKER_WAITING_FOR_LOGIN")
                    time.sleep(checker.POLL_SECONDS)
                    continue
                checker.IG_SESSIONID = sessionid
                command = checker.api_get("/api/verifier/command")
                if command.get("command") == "run":
                    token = command.get("run_token")
                    checker.log(f"CLOUD_CHECKER_RUN token={str(token)[:8]}")
                    try:
                        uploaded, unresolved = checker.run_batch(browser)
                        summary = f"cloud checker uploaded={uploaded}, unresolved_creators={unresolved}"
                    except Exception as exc:
                        traceback.print_exc()
                        summary = f"cloud checker failed: {type(exc).__name__}: {exc}"
                    try:
                        checker.api_post("/api/verifier/complete", {"run_token": token, "summary": summary})
                        checker.log(f"CLOUD_CHECKER_COMPLETE {summary}")
                    except Exception as exc:
                        checker.log(f"CLOUD_CHECKER_COMPLETE_ERROR {type(exc).__name__}: {exc}")
            except Exception as exc:
                checker.log(f"CLOUD_CHECKER_POLL_ERROR {type(exc).__name__}: {exc}")
            time.sleep(checker.POLL_SECONDS)


if __name__ == "__main__":
    init_session_table()
    threading.Thread(target=worker_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
