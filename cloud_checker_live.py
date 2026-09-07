import html
import os
import queue
import threading
import time
import traceback

import requests
from playwright.sync_api import sync_playwright

import cloud_checker_worker as checker
from cloud_checker_app import app, init_session_table, load_session, PORT

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = os.getenv("ADMIN_ID", "").strip()
WORKER_COUNT = 3


def tg_send(text):
    if not BOT_TOKEN or not ADMIN_ID:
        return None
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": ADMIN_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        return (data.get("result") or {}).get("message_id")
    except Exception as exc:
        checker.log(f"TG_PROGRESS_SEND_ERROR {type(exc).__name__}: {exc}")
        return None


def tg_edit(message_id, text):
    if not BOT_TOKEN or not ADMIN_ID or not message_id:
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText",
            json={
                "chat_id": ADMIN_ID,
                "message_id": message_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=20,
        )
        if not r.ok and "message is not modified" not in (r.text or "").lower():
            r.raise_for_status()
    except Exception as exc:
        checker.log(f"TG_PROGRESS_EDIT_ERROR {type(exc).__name__}: {exc}")


def progress_bar(done, total, width=12):
    total = max(1, int(total or 1))
    done = max(0, min(int(done or 0), total))
    filled = round(width * done / total)
    return "█" * filled + "░" * (width - filled)


def status_word(value, kind="generic"):
    if kind == "story":
        return {
            "verified": "✅ LIVE",
            "missing": "❌ MISSING",
            "issue": "⚠️ ISSUE",
            "pending": "⏳ PENDING",
            None: "⏳ CHECKING",
        }.get(value, "⏳ CHECKING")
    if kind == "only":
        return {
            "verified": "✅ NONE",
            "missing": "❌ MISSING",
            "issue": "⚠️ EXTRA",
            "pending": "⏳ PENDING",
            None: "⏳ CHECKING",
        }.get(value, "⏳ CHECKING")
    return {
        "verified": "✅ OK",
        "missing": "❌ MISSING",
        "issue": "⚠️ ISSUE",
        "pending": "⏳ PENDING",
        None: "⏳ CHECKING",
    }.get(value, "⏳ CHECKING")


def final_result(payload):
    vals = [
        payload.get("bio_status"),
        payload.get("only_status"),
        payload.get("story_status"),
        payload.get("story_link_status"),
    ]
    if any(v in {"missing", "issue"} for v in vals):
        return "🔴 FIX"
    if any(v in {None, "pending"} for v in vals):
        return "🟡 REVIEW"
    return "🟢 PASS"


def _worker_line(worker_id, info):
    info = info or {}
    username = info.get("username")
    if not username:
        return f"Worker {worker_id}: <b>Waiting</b>"
    stage = html.escape(str(info.get("stage") or "Checking"))
    return f"Worker {worker_id}: <b>@{html.escape(str(username))}</b> — {stage}"


def progress_text(total, state, started):
    with state["lock"]:
        done = state["done"]
        counts = dict(state["counts"])
        workers = {k: dict(v) for k, v in state["workers"].items()}
    lines = [
        "⚙️ <b>BETROXY Cloud Checker — 3 Workers</b>",
        "",
        f"Progress: <b>{done}/{total}</b>  <code>{progress_bar(done, total)}</code>",
        f"Elapsed: <b>{int(time.time() - started)} sec</b>",
        "",
        _worker_line(1, workers.get(1)),
        _worker_line(2, workers.get(2)),
        _worker_line(3, workers.get(3)),
        "",
        f"🟢 PASS: <b>{counts.get('pass', 0)}</b>   🔴 FIX: <b>{counts.get('fix', 0)}</b>   🟡 REVIEW: <b>{counts.get('review', 0)}</b>",
    ]
    return "\n".join(lines)


def _maybe_update_progress(message_id, total, state, started, force=False):
    if not message_id:
        return
    with state["edit_lock"]:
        now = time.time()
        if not force and now - state.get("last_edit", 0) < 0.9:
            return
        state["last_edit"] = now
        tg_edit(message_id, progress_text(total, state, started))


def _new_context(browser, sessionid):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    if sessionid:
        context.add_cookies([{
            "name": "sessionid",
            "value": sessionid,
            "domain": ".instagram.com",
            "path": "/",
            "httpOnly": True,
            "secure": True,
            "sameSite": "Lax",
        }])
    return context


def _check_one(context, t, worker_id, total, state, started, message_id):
    username = t["username"]
    assigned = t["assigned_url"]
    payload = {
        "campaign_link_id": t["campaign_link_id"],
        "campaign_day": t["campaign_day"],
        "auto_status": "checked",
        "bio_status": None,
        "only_status": None,
        "story_status": None,
        "story_link_status": None,
        "detected_bio_links": [],
        "story_count": 0,
    }
    details = []

    with state["lock"]:
        state["workers"][worker_id] = {"username": username, "stage": "Profile"}
    _maybe_update_progress(message_id, total, state, started)

    p = context.new_page()
    try:
        profile = checker.check_profile(p, username, assigned)
        if profile.get("ok"):
            payload["bio_status"] = profile.get("bio_status")
            payload["only_status"] = profile.get("only_status")
            payload["detected_bio_links"] = profile.get("links") or []
        else:
            payload["auto_status"] = "unknown"
        details.append(profile.get("detail") or "")
    except Exception as exc:
        payload["auto_status"] = "unknown"
        details.append(f"Profile error: {type(exc).__name__}: {exc}")
    finally:
        p.close()

    with state["lock"]:
        state["workers"][worker_id] = {"username": username, "stage": "Story (max 2 attempts)"}
    _maybe_update_progress(message_id, total, state, started)

    p = context.new_page()
    try:
        story = checker.check_story(p, username, assigned)
        if story.get("ok"):
            payload["story_status"] = story.get("story_status")
            payload["story_link_status"] = story.get("story_link_status")
            payload["story_count"] = story.get("story_count") or 0
        details.append(story.get("detail") or "")
    except Exception as exc:
        details.append(f"Story error: {type(exc).__name__}: {exc}")
    finally:
        p.close()

    payload["detail"] = " ".join(x for x in details if x)[:3900]
    checker.api_post("/api/verifier/result", payload)

    vals = [
        payload.get("bio_status"), payload.get("only_status"),
        payload.get("story_status"), payload.get("story_link_status")
    ]
    unresolved = any(v in {None, "pending"} for v in vals)
    result = final_result(payload)

    with state["lock"]:
        state["done"] += 1
        state["uploaded"] += 1
        if unresolved:
            state["unresolved"] += 1
        if "PASS" in result:
            state["counts"]["pass"] += 1
        elif "FIX" in result:
            state["counts"]["fix"] += 1
        else:
            state["counts"]["review"] += 1
        state["workers"][worker_id] = {"username": username, "stage": f"Done — {result}"}
        done = state["done"]

    checker.log(
        f"CLOUD_CHECKER worker={worker_id} done={done}/{total} @{username} "
        f"bio={payload['bio_status']} only={payload['only_status']} "
        f"story={payload['story_status']} story_link={payload['story_link_status']}"
    )
    _maybe_update_progress(message_id, total, state, started, force=True)


def _browser_worker(worker_id, work_q, total, state, started, message_id, sessionid):
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
            )
            context = _new_context(browser, sessionid)
            try:
                while True:
                    try:
                        t = work_q.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        _check_one(context, t, worker_id, total, state, started, message_id)
                    except Exception as exc:
                        checker.log(f"CLOUD_CHECKER_WORKER_ERROR worker={worker_id} {type(exc).__name__}: {exc}")
                        traceback.print_exc()
                    finally:
                        work_q.task_done()
            finally:
                context.close()
                browser.close()
    finally:
        with state["lock"]:
            state["workers"][worker_id] = {"username": "", "stage": "Finished"}
        _maybe_update_progress(message_id, total, state, started, force=True)


def run_batch_parallel(sessionid):
    targets = checker.api_get("/api/verifier/targets").get("targets") or []
    targets = [x for x in targets if x.get("source_type") == "instagram"]
    total = len(targets)
    checker.log(f"CLOUD_CHECKER_3_WORKERS targets={total} usernames={[x.get('username') for x in targets]}")

    started = time.time()
    state = {
        "lock": threading.Lock(),
        "edit_lock": threading.Lock(),
        "last_edit": 0.0,
        "done": 0,
        "uploaded": 0,
        "unresolved": 0,
        "counts": {"pass": 0, "fix": 0, "review": 0},
        "workers": {
            1: {"username": "", "stage": "Waiting"},
            2: {"username": "", "stage": "Waiting"},
            3: {"username": "", "stage": "Waiting"},
        },
    }
    message_id = tg_send(progress_text(total, state, started))

    if not targets:
        return 0, 0, message_id, state["counts"], total, started

    work_q = queue.Queue()
    for t in targets:
        work_q.put(t)

    threads = []
    for worker_id in range(1, WORKER_COUNT + 1):
        th = threading.Thread(
            target=_browser_worker,
            args=(worker_id, work_q, total, state, started, message_id, sessionid),
            daemon=True,
        )
        th.start()
        threads.append(th)

    for th in threads:
        th.join()

    with state["lock"]:
        uploaded = state["uploaded"]
        unresolved = state["unresolved"]
        counts = dict(state["counts"])
    _maybe_update_progress(message_id, total, state, started, force=True)
    return uploaded, unresolved, message_id, counts, total, started


def worker_loop():
    checker.log("CLOUD_CHECKER_LIVE_WORKER_START workers=3 shared_session=yes")
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
                checker.log(f"CLOUD_CHECKER_RUN token={str(token)[:8]} workers=3")
                message_id = None
                started = time.time()
                counts = {"pass": 0, "fix": 0, "review": 0}
                total = 0
                try:
                    uploaded, unresolved, message_id, counts, total, started = run_batch_parallel(sessionid)
                    summary = f"cloud checker uploaded={uploaded}, unresolved_creators={unresolved}, workers=3"
                except Exception as exc:
                    traceback.print_exc()
                    summary = f"cloud checker failed: {type(exc).__name__}: {exc}"
                    if message_id:
                        tg_edit(
                            message_id,
                            "❌ <b>BETROXY Cloud Checker failed</b>\n\n"
                            f"{html.escape(type(exc).__name__)}: {html.escape(str(exc)[:800])}",
                        )

                try:
                    checker.api_post("/api/verifier/complete", {"run_token": token, "summary": summary})
                    checker.log(f"CLOUD_CHECKER_COMPLETE {summary}")
                    if message_id:
                        tg_edit(
                            message_id,
                            "✅ <b>BETROXY Cloud Browser Stage Complete</b>\n\n"
                            f"Workers: <b>3 parallel</b>\n"
                            f"Checked: <b>{total}/{total}</b>  <code>{progress_bar(total, total)}</code>\n"
                            f"Time: <b>{int(time.time() - started)} sec</b>\n\n"
                            f"🟢 PASS: <b>{counts.get('pass', 0)}</b>\n"
                            f"🔴 FIX: <b>{counts.get('fix', 0)}</b>\n"
                            f"🟡 REVIEW: <b>{counts.get('review', 0)}</b>\n\n"
                            "☁️ Any unresolved fields now go to <b>Apify fallback</b>.\n"
                            "Open <b>Campaign Tracker → Auto Report</b> for the merged final result.",
                        )
                except Exception as exc:
                    checker.log(f"CLOUD_CHECKER_COMPLETE_ERROR {type(exc).__name__}: {exc}")
        except Exception as exc:
            checker.log(f"CLOUD_CHECKER_POLL_ERROR {type(exc).__name__}: {exc}")
        time.sleep(checker.POLL_SECONDS)


if __name__ == "__main__":
    init_session_table()
    threading.Thread(target=worker_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
