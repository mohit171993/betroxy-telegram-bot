import html
import os
import threading
import time
import traceback

import requests
from playwright.sync_api import sync_playwright

import cloud_checker_worker as checker
from cloud_checker_app import app, init_session_table, load_session, PORT

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = os.getenv("ADMIN_ID", "").strip()


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


def progress_text(total, done, username="", stage="Starting", payload=None, elapsed=0, counts=None):
    payload = payload or {}
    counts = counts or {"pass": 0, "fix": 0, "review": 0}
    name = f"@{html.escape(str(username))}" if username else "—"
    return (
        "⚙️ <b>BETROXY Cloud Checker</b>\n\n"
        f"Progress: <b>{done}/{total}</b>  <code>{progress_bar(done, total)}</code>\n"
        f"Current: <b>{name}</b>\n"
        f"Stage: <b>{html.escape(stage)}</b>\n"
        f"Elapsed: <b>{int(elapsed)} sec</b>\n\n"
        f"Bio: <b>{status_word(payload.get('bio_status'))}</b>\n"
        f"Extra: <b>{status_word(payload.get('only_status'), 'only')}</b>\n"
        f"Story: <b>{status_word(payload.get('story_status'), 'story')}</b>\n"
        f"Story Link: <b>{status_word(payload.get('story_link_status'))}</b>\n\n"
        f"🟢 PASS: <b>{counts.get('pass', 0)}</b>   "
        f"🔴 FIX: <b>{counts.get('fix', 0)}</b>   "
        f"🟡 REVIEW: <b>{counts.get('review', 0)}</b>"
    )


def run_batch_live(browser):
    targets = checker.api_get("/api/verifier/targets").get("targets") or []
    targets = [x for x in targets if x.get("source_type") == "instagram"]
    total = len(targets)
    checker.log(f"CLOUD_CHECKER targets={total} usernames={[x.get('username') for x in targets]}")

    started = time.time()
    counts = {"pass": 0, "fix": 0, "review": 0}
    message_id = tg_send(progress_text(total, 0, stage="Starting cloud browser", elapsed=0, counts=counts))

    context = browser.new_context(viewport={"width": 1280, "height": 900})
    if checker.IG_SESSIONID:
        context.add_cookies([{
            "name": "sessionid",
            "value": checker.IG_SESSIONID,
            "domain": ".instagram.com",
            "path": "/",
            "httpOnly": True,
            "secure": True,
            "sameSite": "Lax",
        }])

    uploaded = 0
    unresolved = 0
    try:
        for idx, t in enumerate(targets, 1):
            username = t["username"]
            assigned = t["assigned_url"]
            checker.log(f"CLOUD_CHECKER {idx}/{total} @{username}")
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

            tg_edit(
                message_id,
                progress_text(total, idx - 1, username, "Checking profile", payload, time.time() - started, counts),
            )

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

            tg_edit(
                message_id,
                progress_text(total, idx - 1, username, "Checking Story (max 2 attempts)", payload, time.time() - started, counts),
            )

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
            uploaded += 1

            result = final_result(payload)
            if "PASS" in result:
                counts["pass"] += 1
            elif "FIX" in result:
                counts["fix"] += 1
            else:
                counts["review"] += 1
                unresolved += 1

            checker.log(
                f"CLOUD_CHECKER uploaded @{username} "
                f"bio={payload['bio_status']} only={payload['only_status']} "
                f"story={payload['story_status']} story_link={payload['story_link_status']}"
            )

            tg_edit(
                message_id,
                progress_text(total, idx, username, f"Completed creator — {result}", payload, time.time() - started, counts),
            )
    finally:
        context.close()

    return uploaded, unresolved, message_id, counts, total, started


def worker_loop():
    checker.log("CLOUD_CHECKER_LIVE_WORKER_START")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
        )
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
                    message_id = None
                    started = time.time()
                    counts = {"pass": 0, "fix": 0, "review": 0}
                    total = 0
                    try:
                        uploaded, unresolved, message_id, counts, total, started = run_batch_live(browser)
                        summary = f"cloud checker uploaded={uploaded}, unresolved_creators={unresolved}"
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
