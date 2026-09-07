import json
import os
import time
import traceback
from urllib.parse import urlparse, parse_qs, unquote

import requests
from playwright.sync_api import sync_playwright

BASE_URL = os.getenv("CHECKER_BASE_URL", "https://www.batraxy.com").rstrip("/")
TRACKER_API_SECRET = os.getenv("TRACKER_API_SECRET", "").strip()
IG_SESSIONID = os.getenv("IG_SESSIONID", "").strip()
POLL_SECONDS = max(5, int(os.getenv("CHECKER_POLL_SECONDS", "8")))
MAX_STORY_FRAMES = max(5, int(os.getenv("MAX_STORY_FRAMES", "40")))

if not TRACKER_API_SECRET:
    raise RuntimeError("TRACKER_API_SECRET is missing")


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def headers():
    return {"X-Tracker-Secret": TRACKER_API_SECRET}


def api_get(path):
    r = requests.get(BASE_URL + path, headers=headers(), timeout=30)
    r.raise_for_status()
    return r.json()


def api_post(path, payload):
    r = requests.post(
        BASE_URL + path,
        headers={**headers(), "Content-Type": "application/json"},
        json=payload,
        timeout=45,
    )
    r.raise_for_status()
    return r.json()


def norm_url(value):
    value = (value or "").strip()
    if not value:
        return ""
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    p = urlparse(value)
    host = (p.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = (p.path or "").rstrip("/")
    return (host + path).lower()


def unwrap_instagram_redirect(url):
    try:
        p = urlparse(url or "")
        if "l.instagram.com" in (p.netloc or "").lower():
            q = parse_qs(p.query)
            v = (q.get("u") or [""])[0]
            if v:
                return unquote(v)
    except Exception:
        pass
    return url or ""


def collect_external_links(page):
    try:
        hrefs = page.locator("a[href]").evaluate_all("els => els.map(e => e.href || '')")
    except Exception:
        return []
    out, seen = [], set()
    for raw in hrefs:
        url = unwrap_instagram_redirect(raw)
        try:
            host = (urlparse(url).netloc or "").lower().removeprefix("www.")
        except Exception:
            continue
        if not host or host.endswith("instagram.com") or host.endswith("facebook.com") or host.endswith("threads.net"):
            continue
        n = norm_url(url)
        if n and n not in seen:
            seen.add(n)
            out.append(url)
    return out


def page_logged_out(page):
    try:
        body = (page.locator("body").inner_text(timeout=3000) or "").lower()
    except Exception:
        body = ""
    return "log in" in body and "sign up" in body


def wait_profile(page, username, timeout_s=18):
    end = time.time() + timeout_s
    while time.time() < end:
        if page_logged_out(page):
            return "logged_out"
        try:
            body = (page.locator("body").inner_text(timeout=2500) or "").lower()
        except Exception:
            body = ""
        if "sorry, this page isn't available" in body or "page isn't available" in body:
            return "not_found"
        signals = ["followers", "following", "posts", username.lower()]
        if sum(1 for s in signals if s in body) >= 2:
            return "ready"
        page.wait_for_timeout(1000)
    return "timeout"


def check_profile(page, username, assigned_url):
    page.goto(f"https://www.instagram.com/{username}/", wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(2500)
    state = wait_profile(page, username)
    if state == "logged_out":
        return {"ok": False, "detail": "Instagram session logged out"}
    if state == "not_found":
        return {"ok": True, "bio_status": "missing", "only_status": "issue", "links": [], "detail": "Profile unavailable/not found"}
    if state != "ready":
        return {"ok": False, "detail": f"Profile did not finish loading ({state})"}

    links = collect_external_links(page)
    target = norm_url(assigned_url)
    normalized = {norm_url(x) for x in links if norm_url(x)}
    try:
        body = (page.locator("body").inner_text(timeout=4000) or "").lower().replace("https://", "").replace("http://", "").replace("www.", "")
    except Exception:
        body = ""
    found = target in normalized or target.replace("www.", "") in body
    return {
        "ok": True,
        "bio_status": "verified" if found else "missing",
        "only_status": "verified" if found and normalized <= {target} else ("issue" if normalized or found else "pending"),
        "links": links,
        "detail": f"Profile inspected; assigned bio link={'found' if found else 'not found'}; external destinations={len(normalized)}",
    }


def story_has_media(page):
    try:
        if page.locator("video").count() > 0:
            return True
    except Exception:
        pass
    try:
        return bool(page.locator("img").evaluate_all("imgs => imgs.some(i => { const r=i.getBoundingClientRect(); return r.width>=250 && r.height>=300; })"))
    except Exception:
        return False


def click_story_ring(page):
    try:
        imgs = page.locator("header img")
        for i in range(min(imgs.count(), 10)):
            img = imgs.nth(i)
            if not img.is_visible():
                continue
            box = img.bounding_box()
            if not box or not (36 <= box["width"] <= 190 and 36 <= box["height"] <= 190):
                continue
            clickable = img.locator("xpath=ancestor::*[self::button or @role='button' or self::a][1]")
            (clickable.first if clickable.count() else img).click(timeout=5000)
            page.wait_for_timeout(1200)
            return True
    except Exception:
        pass
    return False


def click_view_story(page):
    for label in ("View story", "View Story"):
        try:
            loc = page.get_by_text(label, exact=False)
            if loc.count() and loc.first.is_visible():
                loc.first.click(timeout=4000)
                page.wait_for_timeout(1000)
                return True
        except Exception:
            pass
    return False


def advance_story(page):
    for label in ("Next", "Next story"):
        try:
            loc = page.get_by_role("button", name=label, exact=True)
            if loc.count() and loc.first.is_visible():
                loc.first.click(timeout=3000)
                return True
        except Exception:
            pass
    try:
        page.keyboard.press("ArrowRight")
        return True
    except Exception:
        return False


def inspect_story(page, assigned_url):
    target = norm_url(assigned_url)
    links = collect_external_links(page)
    try:
        body = (page.locator("body").inner_text(timeout=2500) or "").lower().replace("https://", "").replace("http://", "").replace("www.", "")
    except Exception:
        body = ""
    return target.replace("www.", "") in body or any(norm_url(x) == target for x in links)


def check_story(page, username, assigned_url):
    profile_url = f"https://www.instagram.com/{username}/"
    details = []
    for attempt in (1, 2):
        page.goto(profile_url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2500)
        if wait_profile(page, username) != "ready":
            details.append(f"attempt {attempt}: profile not ready")
            continue
        if not click_story_ring(page):
            details.append(f"attempt {attempt}: story ring not found")
            continue
        for _ in range(3):
            if click_view_story(page):
                break
            page.wait_for_timeout(500)
        loaded = False
        for _ in range(12):
            page.wait_for_timeout(700)
            if story_has_media(page):
                loaded = True
                break
            click_view_story(page)
        if not loaded:
            details.append(f"attempt {attempt}: blank/unloaded story")
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            page.wait_for_timeout(5000)
            continue

        frames = 0
        found = False
        for _ in range(MAX_STORY_FRAMES):
            if not story_has_media(page):
                break
            frames += 1
            found = found or inspect_story(page, assigned_url)
            if not advance_story(page):
                break
            page.wait_for_timeout(1200)
        if frames:
            return {
                "ok": True,
                "story_status": "verified",
                "story_link_status": "verified" if found else "issue",
                "story_count": frames,
                "detail": f"Story sweep frames={frames}; assigned link={'found' if found else 'not found'}",
            }
    return {"ok": False, "detail": "; ".join(details) or "Story unavailable"}


def run_batch(browser):
    targets = api_get("/api/verifier/targets").get("targets") or []
    targets = [x for x in targets if x.get("source_type") == "instagram"]
    log(f"CLOUD_CHECKER targets={len(targets)} usernames={[x.get('username') for x in targets]}")

    context = browser.new_context(viewport={"width": 1280, "height": 900})
    if IG_SESSIONID:
        context.add_cookies([{
            "name": "sessionid",
            "value": IG_SESSIONID,
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
            log(f"CLOUD_CHECKER {idx}/{len(targets)} @{username}")
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

            p = context.new_page()
            try:
                profile = check_profile(p, username, assigned)
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

            p = context.new_page()
            try:
                story = check_story(p, username, assigned)
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
            api_post("/api/verifier/result", payload)
            uploaded += 1
            if any(payload.get(k) is None for k in ("bio_status", "only_status", "story_status", "story_link_status")):
                unresolved += 1
            log(f"CLOUD_CHECKER uploaded @{username} bio={payload['bio_status']} only={payload['only_status']} story={payload['story_status']} story_link={payload['story_link_status']}")
    finally:
        context.close()

    return uploaded, unresolved


def main():
    log("CLOUD_CHECKER_WORKER_START")
    if not IG_SESSIONID:
        log("CLOUD_CHECKER_LOGIN_REQUIRED IG_SESSIONID is not configured")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"])
        while True:
            try:
                command = api_get("/api/verifier/command")
                if command.get("command") == "run":
                    token = command.get("run_token")
                    log(f"CLOUD_CHECKER_RUN token={str(token)[:8]}")
                    try:
                        uploaded, unresolved = run_batch(browser)
                        summary = f"cloud checker uploaded={uploaded}, unresolved_creators={unresolved}"
                    except Exception as exc:
                        traceback.print_exc()
                        summary = f"cloud checker failed: {type(exc).__name__}: {exc}"
                    try:
                        api_post("/api/verifier/complete", {"run_token": token, "summary": summary})
                        log(f"CLOUD_CHECKER_COMPLETE {summary}")
                    except Exception as exc:
                        log(f"CLOUD_CHECKER_COMPLETE_ERROR {type(exc).__name__}: {exc}")
            except Exception as exc:
                log(f"CLOUD_CHECKER_POLL_ERROR {type(exc).__name__}: {exc}")
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
