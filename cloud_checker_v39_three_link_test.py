import os
import threading
import time
from datetime import datetime, timezone

from flask import Response, jsonify, redirect, request
from playwright.sync_api import sync_playwright

import cloud_checker_v38_bootstrap as v38
import cloud_checker_worker as checker
import cloud_checker_live as live
from cloud_checker_app import authorized

# Controlled browser-only validation. No Apify handoff is allowed from this test.
TEST_USERNAMES = {
    "mumbai_indians_brand_status",
    "cricxcratee",
    "ultra_ro45",
}

_original_api_get = checker.api_get
_original_api_post = checker.api_post
_original_check_profile = checker.check_profile

DEBUG_SCREEN = "/tmp/betroxy_instagram_debug.png"
_debug_lock = threading.Lock()
_debug_state = {
    "running": False,
    "status": "Ready. Press Run visual login check.",
    "url": "",
    "title": "",
    "updated_at": "",
    "login_result": "unknown",
}


def _stamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _update_state(**kwargs):
    with _debug_lock:
        _debug_state.update(kwargs)
        _debug_state["updated_at"] = _stamp()


def _capture(page, status):
    url = ""
    title = ""
    try:
        url = page.url or ""
    except Exception:
        pass
    try:
        title = page.title() or ""
    except Exception:
        pass
    try:
        page.screenshot(path=DEBUG_SCREEN, full_page=False)
    except Exception as exc:
        checker.log(f"DEBUG_SCREENSHOT_ERROR {type(exc).__name__}: {exc}")
    _update_state(status=status, url=url, title=title)


def _visual_login_check():
    _update_state(running=True, status="Starting Chromium browser...", login_result="checking")
    browser = None
    try:
        sessionid = live.load_session()
        if not sessionid:
            _update_state(running=False, status="No saved Instagram session found.", login_result="missing")
            return

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
            )
            context = browser.new_context(viewport={"width": 1365, "height": 900})
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

            _update_state(status="Browser opened. Opening Instagram home...")
            page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(1800)
            _capture(page, "Instagram home opened.")
            page.wait_for_timeout(2500)
            _capture(page, "Instagram home after page load.")

            page.goto("https://www.instagram.com/accounts/edit/", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2200)
            _capture(page, "Opening Instagram account settings to verify login...")
            page.wait_for_timeout(3000)
            _capture(page, "Instagram account settings loaded.")

            current_url = (page.url or "").lower()
            try:
                body = (page.locator("body").inner_text(timeout=5000) or "").lower()
            except Exception:
                body = ""

            redirected_to_login = "/accounts/login" in current_url
            login_page_text = "log in" in body and "sign up" in body
            challenge = "/challenge" in current_url or "security code" in body or "confirm it's you" in body

            if redirected_to_login or login_page_text:
                _capture(page, "NOT LOGGED IN — Instagram redirected to login.")
                _update_state(login_result="logged_out")
                return
            if challenge:
                _capture(page, "Instagram session is blocked by a challenge/security check.")
                _update_state(login_result="challenge")
                return

            _update_state(login_result="logged_in")
            _capture(page, "LOGIN OK. Opening test creator profile...")

            page.goto("https://www.instagram.com/cricxcratee/", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2200)
            _capture(page, "Test profile opened — first render.")
            page.wait_for_timeout(5000)
            _capture(page, "Test profile after 5 seconds.")
            page.wait_for_timeout(5000)
            _capture(page, "Test profile final render. Screenshot retained for inspection.")

            browser.close()
            browser = None
    except Exception as exc:
        checker.log(f"VISUAL_LOGIN_CHECK_ERROR {type(exc).__name__}: {exc}")
        _update_state(status=f"Visual check failed: {type(exc).__name__}: {exc}", login_result="error")
    finally:
        try:
            if browser:
                browser.close()
        except Exception:
            pass
        _update_state(running=False)


def debug_check_profile(page, username, assigned_url):
    _capture(page, f"Smart Check starting profile @{username}")
    try:
        result = _original_check_profile(page, username, assigned_url)
        return result
    finally:
        _capture(page, f"Smart Check finished profile @{username}; latest browser screen retained")


checker.check_profile = debug_check_profile


@live.app.get("/debug")
def visual_debug_page():
    if not authorized(request):
        return Response("Not found", status=404)
    token = request.args.get("token", "")
    return f"""<!doctype html>
<html><head><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>BETROXY Instagram Browser Monitor</title>
<style>
body{{font-family:Arial,sans-serif;background:#0b1017;color:#eef3f8;margin:0;padding:20px}}
.wrap{{max-width:1250px;margin:auto}}
.card{{background:#121a24;border:1px solid #26364a;border-radius:15px;padding:18px;margin-bottom:14px}}
h1{{margin:0 0 8px;font-size:24px}} .muted{{color:#9fb0c3;font-size:13px}}
.status{{font-size:16px;font-weight:700;margin:8px 0}} .ok{{color:#61d88d}}
button{{background:#5bd68a;color:#07120b;border:0;border-radius:9px;padding:11px 16px;font-weight:700;cursor:pointer}}
img{{width:100%;max-height:720px;object-fit:contain;background:#05080c;border-radius:10px;border:1px solid #26364a}}
code{{word-break:break-all;color:#cce0f4}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:10px}} @media(max-width:750px){{.grid{{grid-template-columns:1fr}}}}
</style></head><body><div class='wrap'>
<div class='card'><h1>BETROXY Instagram Browser Monitor</h1>
<div class='muted'>Protected visual monitor. It never displays the saved session cookie.</div>
<form method='post' action='/debug/run'><input type='hidden' name='token' value='{token}'><button type='submit'>Run visual login check</button></form>
<div id='status' class='status'>Loading...</div>
<div class='grid'><div><b>Login:</b> <span id='login'>-</span></div><div><b>Updated:</b> <span id='updated'>-</span></div></div>
<div><b>Browser URL:</b> <code id='url'>-</code></div><div><b>Page title:</b> <span id='title'>-</span></div>
</div>
<div class='card'><img id='screen' alt='Latest cloud browser screenshot'><div class='muted'>The screenshot refreshes every 2 seconds while the cloud browser moves through Instagram.</div></div>
</div><script>
const token={token!r};
async function refresh(){{
  try{{
    const r=await fetch('/debug/state?token='+encodeURIComponent(token),{{cache:'no-store'}}); const s=await r.json();
    document.getElementById('status').textContent=s.status+(s.running?' (running)':'');
    document.getElementById('login').textContent=s.login_result;
    document.getElementById('updated').textContent=s.updated_at||'-';
    document.getElementById('url').textContent=s.url||'-';
    document.getElementById('title').textContent=s.title||'-';
    document.getElementById('screen').src='/debug/screen.png?token='+encodeURIComponent(token)+'&t='+Date.now();
  }}catch(e){{document.getElementById('status').textContent='Monitor refresh error: '+e;}}
}}
refresh(); setInterval(refresh,2000);
</script></body></html>"""


@live.app.get("/debug/state")
def visual_debug_state():
    if not authorized(request):
        return Response("Not found", status=404)
    with _debug_lock:
        return jsonify(dict(_debug_state))


@live.app.get("/debug/screen.png")
def visual_debug_screen():
    if not authorized(request):
        return Response("Not found", status=404)
    if not os.path.exists(DEBUG_SCREEN):
        return Response("No screenshot yet", status=404)
    with open(DEBUG_SCREEN, "rb") as f:
        data = f.read()
    return Response(data, mimetype="image/png", headers={"Cache-Control": "no-store, max-age=0"})


@live.app.post("/debug/run")
def visual_debug_run():
    if not authorized(request):
        return Response("Not found", status=404)
    with _debug_lock:
        already_running = bool(_debug_state.get("running"))
    if not already_running:
        threading.Thread(target=_visual_login_check, daemon=True).start()
    token = request.form.get("token", "")
    return redirect("/debug?token=" + token)


def test_api_get(path):
    data = _original_api_get(path)
    if path == "/api/verifier/targets" and isinstance(data, dict):
        targets = data.get("targets") or []
        picked = [
            t for t in targets
            if str(t.get("username") or "").strip().lstrip("@").lower() in TEST_USERNAMES
        ]
        data = dict(data)
        data["targets"] = picked
        checker.log(
            "THREE_LINK_TEST_TARGETS "
            + str([t.get("username") for t in picked])
        )
    return data


def test_api_post(path, payload):
    if path == "/api/verifier/complete":
        # Intentionally do not notify the main bot that the browser batch is complete.
        # This prevents any Apify fallback during this controlled 3-link validation.
        checker.log("THREE_LINK_TEST_COMPLETE_SUPPRESSED apify_handoff=blocked")
        return {"ok": True, "test_mode": True, "apify_handoff": "blocked"}
    return _original_api_post(path, payload)


checker.api_get = test_api_get
checker.api_post = test_api_post
checker.log("THREE_LINK_BROWSER_ONLY_TEST_ACTIVE creators=3 apify_handoff=blocked")
checker.log("VISUAL_INSTAGRAM_DEBUG_MONITOR_ACTIVE path=/debug")


if __name__ == "__main__":
    live.init_session_table()
    threading.Thread(target=live.worker_loop, daemon=True).start()
    live.app.run(host="0.0.0.0", port=live.PORT, debug=False, use_reloader=False)
