import time
import traceback

import requests
from playwright.sync_api import sync_playwright

import cloud_checker_worker as base

CDP_URL = "http://127.0.0.1:9222"
TEST_CREATORS = {
    "mumbai_indians_brand_status",
    "cricxcratee",
    "ultra_ro45",
}


def wait_for_cdp(timeout=300):
    end = time.time() + timeout
    while time.time() < end:
        try:
            r = requests.get(CDP_URL + "/json/version", timeout=2)
            if r.ok:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def has_session_cookie(context):
    try:
        cookies = context.cookies("https://www.instagram.com/")
        return any(c.get("name") == "sessionid" and c.get("value") for c in cookies)
    except Exception:
        return False


def logged_in(context):
    # The login gate only starts this checker after Chrome has persisted a
    # sessionid cookie. Reconfirm it here, then make one normal navigation to
    # catch a server-side invalidation/challenge before checking campaign pages.
    if not has_session_cookie(context):
        return False

    page = context.new_page()
    try:
        page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2500)
        url = (page.url or "").lower()
        body = ""
        try:
            body = (page.locator("body").inner_text(timeout=3000) or "").lower()
        except Exception:
            pass

        blocked_markers = (
            "/accounts/login",
            "/auth_platform/",
            "/challenge/",
        )
        if any(x in url for x in blocked_markers):
            return False
        if "try another device to continue" in body:
            return False
        if "the login information you entered is incorrect" in body:
            return False
        return has_session_cookie(context)
    finally:
        page.close()


def run_safe_batch(context):
    targets = base.api_get("/api/verifier/targets").get("targets") or []
    targets = [
        x for x in targets
        if x.get("source_type") == "instagram"
        and (x.get("username") or "").lower() in TEST_CREATORS
    ]
    base.log(
        "GUI_SHARED_TEST targets=" + str(len(targets)) +
        " usernames=" + str([x.get("username") for x in targets]) +
        " apify_handoff=blocked"
    )

    if not logged_in(context):
        base.log("GUI_SHARED_TEST_BLOCKED instagram_login_not_confirmed")
        return 0, 0

    uploaded = 0
    unresolved = 0
    for idx, t in enumerate(targets, 1):
        username = t["username"]
        assigned = t["assigned_url"]
        base.log(f"GUI_SHARED_TEST {idx}/{len(targets)} @{username}")
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

        page = context.new_page()
        try:
            profile = base.check_profile(page, username, assigned)
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
            page.close()

        page = context.new_page()
        try:
            story = base.check_story(page, username, assigned)
            if story.get("ok"):
                payload["story_status"] = story.get("story_status")
                payload["story_link_status"] = story.get("story_link_status")
                payload["story_count"] = story.get("story_count") or 0
            details.append(story.get("detail") or "")
        except Exception as exc:
            details.append(f"Story error: {type(exc).__name__}: {exc}")
        finally:
            page.close()

        payload["detail"] = " ".join(x for x in details if x)[:3900]
        base.api_post("/api/verifier/result", payload)
        uploaded += 1
        if any(payload.get(k) is None for k in (
            "bio_status", "only_status", "story_status", "story_link_status"
        )):
            unresolved += 1
        base.log(
            f"GUI_SHARED_TEST uploaded @{username} "
            f"bio={payload['bio_status']} only={payload['only_status']} "
            f"story={payload['story_status']} story_link={payload['story_link_status']}"
        )

    return uploaded, unresolved


def attached_loop(p):
    if not wait_for_cdp():
        base.log("GUI_SHARED_CHECKER_WAIT cdp_not_ready retrying")
        return
    browser = p.chromium.connect_over_cdp(CDP_URL)
    if not browser.contexts:
        base.log("GUI_SHARED_CHECKER_WAIT no_browser_context retrying")
        return
    context = browser.contexts[0]
    base.log("GUI_SHARED_CHECKER_ATTACHED authenticated_profile_context=shared")

    while browser.is_connected():
        try:
            command = base.api_get("/api/verifier/command")
            if command.get("command") == "run":
                token = str(command.get("run_token") or "")
                base.log(f"GUI_SHARED_TEST_RUN token={token[:8]}")
                try:
                    uploaded, unresolved = run_safe_batch(context)
                    base.log(
                        f"GUI_SHARED_TEST_FINISHED uploaded={uploaded} unresolved={unresolved} "
                        "complete_suppressed=yes apify_handoff=blocked"
                    )
                except Exception as exc:
                    traceback.print_exc()
                    base.log(f"GUI_SHARED_TEST_ERROR {type(exc).__name__}: {exc}")
                # Test mode intentionally never completes the main run, so no
                # unresolved creator can be handed to Apify.
                time.sleep(30)
        except Exception as exc:
            base.log(f"GUI_SHARED_CHECKER_POLL_ERROR {type(exc).__name__}: {exc}")
            if not browser.is_connected():
                break
        time.sleep(base.POLL_SECONDS)


def main():
    base.log("GUI_SHARED_CHECKER_START cdp=9222 three_link_test=yes apify_handoff=blocked")
    with sync_playwright() as p:
        while True:
            try:
                attached_loop(p)
            except Exception as exc:
                base.log(f"GUI_SHARED_CHECKER_RECONNECT {type(exc).__name__}: {exc}")
            time.sleep(3)


if __name__ == "__main__":
    main()
