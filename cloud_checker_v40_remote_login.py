import os
import queue
import threading
import time
from datetime import datetime, timezone

from flask import Response, jsonify, redirect, request
from playwright.sync_api import sync_playwright

import cloud_checker_v39_three_link_test as v39
import cloud_checker_live as live
from cloud_checker_app import authorized

# Direct cloud-browser login console.
# The user never has to copy/paste an Instagram session cookie.
# A dedicated Playwright browser is controlled from a protected web page.
# After Instagram accepts the login, the resulting authenticated cookie is saved
# automatically for the checker.

REMOTE_SCREEN = "/tmp/betroxy_remote_instagram.png"
_cmd_q = queue.Queue()
_state_lock = threading.Lock()
_state = {
    "ready": False,
    "status": "Starting remote cloud browser...",
    "url": "",
    "title": "",
    "updated_at": "",
    "login": "unknown",
}


def _stamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _set_state(**kwargs):
    with _state_lock:
        _state.update(kwargs)
        _state["updated_at"] = _stamp()


def _snapshot(page, status=None):
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
        page.screenshot(path=REMOTE_SCREEN, full_page=False)
    except Exception:
        pass
    data = {"url": url, "title": title}
    if status:
        data["status"] = status
    _set_state(**data)


def _detect_and_save_login(context, page):
    try:
        url = (page.url or "").lower()
        body = ""
        try:
            body = (page.locator("body").inner_text(timeout=1500) or "").lower()
        except Exception:
            pass
        if "/challenge" in url or "confirm it's you" in body or "security code" in body:
            _set_state(login="challenge")
            return
        if "/accounts/login" in url:
            _set_state(login="logged_out")
            return
        cookies = context.cookies("https://www.instagram.com/")
        sess = next((c.get("value") for c in cookies if c.get("name") == "sessionid" and c.get("value")), "")
        if sess:
            # Internal persistence only; user never handles the cookie manually.
            live.save_session(sess)
            _set_state(login="logged_in", status="LOGIN OK — cloud Instagram login saved automatically.")
    except Exception:
        pass


def _remote_browser_loop():
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
            )
            context = browser.new_context(viewport={"width": 1365, "height": 900})
            page = context.new_page()
            page.goto("https://www.instagram.com/accounts/login/", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2500)
            _snapshot(page, "Cloud browser ready. Log into Instagram directly on this screen.")
            _set_state(ready=True)

            last_shot = 0.0
            while True:
                try:
                    cmd = _cmd_q.get(timeout=0.5)
                    action = cmd.get("action")
                    if action == "click":
                        page.mouse.click(float(cmd["x"]), float(cmd["y"]))
                        page.wait_for_timeout(350)
                    elif action == "type":
                        text = str(cmd.get("text") or "")
                        if text:
                            page.keyboard.type(text, delay=35)
                        page.wait_for_timeout(250)
                    elif action == "key":
                        page.keyboard.press(str(cmd.get("key") or "Enter"))
                        page.wait_for_timeout(500)
                    elif action == "goto":
                        page.goto(str(cmd.get("url") or "https://www.instagram.com/"), wait_until="domcontentloaded", timeout=45000)
                        page.wait_for_timeout(1800)
                    elif action == "reload":
                        page.reload(wait_until="domcontentloaded", timeout=45000)
                        page.wait_for_timeout(1200)
                    elif action == "login":
                        username = str(cmd.get("username") or "")
                        password = str(cmd.get("password") or "")
                        page.goto("https://www.instagram.com/accounts/login/", wait_until="domcontentloaded", timeout=45000)
                        page.wait_for_timeout(1500)
                        u = page.locator("input[name='username']")
                        pw = page.locator("input[name='password']")
                        u.fill(username)
                        pw.fill(password)
                        page.locator("button[type='submit']").click()
                        page.wait_for_timeout(3500)
                        _set_state(status="Login submitted. Complete any Instagram verification shown below.")
                    _detect_and_save_login(context, page)
                    _snapshot(page)
                except queue.Empty:
                    pass
                except Exception as exc:
                    _set_state(status=f"Browser action error: {type(exc).__name__}: {exc}")

                if time.time() - last_shot > 1.5:
                    _detect_and_save_login(context, page)
                    _snapshot(page)
                    last_shot = time.time()
    except Exception as exc:
        _set_state(ready=False, status=f"Remote browser failed: {type(exc).__name__}: {exc}")


threading.Thread(target=_remote_browser_loop, daemon=True).start()


@live.app.get("/remote")
def remote_page():
    if not authorized(request):
        return Response("Not found", status=404)
    token = request.args.get("token", "")
    return f"""<!doctype html>
<html><head><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>BETROXY Cloud Instagram Login</title>
<style>
body{{font-family:Arial,sans-serif;background:#0b1017;color:#eef3f8;margin:0;padding:16px}}
.wrap{{max-width:1400px;margin:auto}}
.card{{background:#121a24;border:1px solid #26364a;border-radius:14px;padding:14px;margin-bottom:12px}}
h1{{font-size:22px;margin:0 0 8px}} .muted{{color:#9fb0c3;font-size:13px;line-height:1.45}}
.row{{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}}
input{{background:#081019;color:white;border:1px solid #31445b;border-radius:8px;padding:10px;min-width:210px}}
button{{background:#5bd68a;color:#07120b;border:0;border-radius:8px;padding:10px 13px;font-weight:700;cursor:pointer}}
button.alt{{background:#d7e2ee;color:#101820}}
img{{width:100%;max-height:76vh;object-fit:contain;background:#05080c;border-radius:8px;border:1px solid #26364a;cursor:crosshair}}
code{{word-break:break-all;color:#cce0f4}}
</style></head><body><div class='wrap'>
<div class='card'><h1>BETROXY — Direct Instagram Cloud Login</h1>
<div class='muted'>This controls the actual cloud Chromium browser. No session cookie copy/paste is required. Your password is used only for the login action and is not stored by this page.</div>
<div id='status' style='font-weight:700;margin-top:8px'>Loading...</div>
<div><b>Login:</b> <span id='login'>-</span> &nbsp; <b>URL:</b> <code id='url'>-</code></div>
<div class='row'>
<form id='loginForm' onsubmit='doLogin(event)'><input id='user' autocomplete='username' placeholder='Instagram username'><input id='pass' type='password' autocomplete='current-password' placeholder='Instagram password'><button>Login directly</button></form>
</div>
<div class='row'><input id='typeText' placeholder='Type into selected field'><button class='alt' onclick='sendType()'>Type</button><button class='alt' onclick="sendKey('Enter')">Enter</button><button class='alt' onclick="sendKey('Tab')">Tab</button><button class='alt' onclick="sendKey('Escape')">Esc</button><button class='alt' onclick='reloadPage()'>Reload</button></div>
<div class='muted'>You can also click directly on the Instagram screenshot below. Use this for verification/challenge screens.</div>
</div>
<div class='card'><img id='screen' alt='Cloud Instagram browser'></div>
</div><script>
const token={token!r};
async function post(path,obj){{obj.token=token; await fetch(path,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(obj)}}); setTimeout(refresh,300);}}
async function refresh(){{try{{const r=await fetch('/remote/state?token='+encodeURIComponent(token),{{cache:'no-store'}});const s=await r.json();document.getElementById('status').textContent=s.status||'-';document.getElementById('login').textContent=s.login||'-';document.getElementById('url').textContent=s.url||'-';document.getElementById('screen').src='/remote/screen.png?token='+encodeURIComponent(token)+'&t='+Date.now();}}catch(e){{document.getElementById('status').textContent='Refresh error: '+e;}}}}
async function doLogin(e){{e.preventDefault();const u=document.getElementById('user').value;const p=document.getElementById('pass').value;await post('/remote/action',{{action:'login',username:u,password:p}});document.getElementById('pass').value='';}}
function sendType(){{const e=document.getElementById('typeText');post('/remote/action',{{action:'type',text:e.value}});e.value='';}}
function sendKey(k){{post('/remote/action',{{action:'key',key:k}});}}
function reloadPage(){{post('/remote/action',{{action:'reload'}});}}
document.getElementById('screen').addEventListener('click',function(ev){{const r=this.getBoundingClientRect();const x=(ev.clientX-r.left)*1365/r.width;const y=(ev.clientY-r.top)*900/r.height;post('/remote/action',{{action:'click',x:x,y:y}});}});
refresh();setInterval(refresh,1800);
</script></body></html>"""


@live.app.get("/remote/state")
def remote_state():
    if not authorized(request):
        return Response("Not found", status=404)
    with _state_lock:
        return jsonify(dict(_state))


@live.app.get("/remote/screen.png")
def remote_screen():
    if not authorized(request):
        return Response("Not found", status=404)
    if not os.path.exists(REMOTE_SCREEN):
        return Response("No screenshot yet", status=404)
    with open(REMOTE_SCREEN, "rb") as f:
        return Response(f.read(), mimetype="image/png", headers={"Cache-Control":"no-store, max-age=0"})


@live.app.post("/remote/action")
def remote_action():
    data = request.get_json(silent=True) or {}
    if (data.get("token") or "") != os.getenv("LOGIN_SETUP_TOKEN", ""):
        return Response("Not found", status=404)
    action = str(data.get("action") or "")
    if action not in {"click", "type", "key", "goto", "reload", "login"}:
        return jsonify({"ok": False, "error": "unsupported action"}), 400
    # Never log or persist username/password from this request.
    _cmd_q.put(dict(data))
    return jsonify({"ok": True})


v39.checker.log("DIRECT_CLOUD_INSTAGRAM_REMOTE_LOGIN_ACTIVE path=/remote")


if __name__ == "__main__":
    live.init_session_table()
    threading.Thread(target=live.worker_loop, daemon=True).start()
    live.app.run(host="0.0.0.0", port=live.PORT, debug=False, use_reloader=False)
