import threading
import time

import cloud_checker_worker as checker
import cloud_checker_live as live


# V38 browser-health patch
# - Keep 3 parallel workers.
# - Stagger worker startup to avoid three simultaneous Instagram hits.
# - Retry profile loading once.
# - Make profile-ready detection more tolerant of Instagram UI variations.
# - Log the real profile failure reason so None results are diagnosable.


def robust_wait_profile(page, username, timeout_s=22):
    end = time.time() + timeout_s
    username_l = (username or "").lower()
    last_reason = "waiting"

    while time.time() < end:
        try:
            body = (page.locator("body").inner_text(timeout=3000) or "").lower()
        except Exception:
            body = ""

        if "log in" in body and "sign up" in body:
            return "logged_out"
        if "sorry, this page isn't available" in body or "page isn't available" in body:
            return "not_found"
        if "please wait a few minutes" in body or "try again later" in body:
            return "rate_limited"
        if "checkpoint" in (page.url or "").lower() or "challenge" in (page.url or "").lower():
            return "challenge"

        classic = sum(1 for s in ("followers", "following", "posts") if s in body)
        username_seen = bool(username_l and username_l in body)

        header_ready = False
        try:
            header = page.locator("header")
            if header.count() and header.first.is_visible():
                text = (header.first.inner_text(timeout=2000) or "").strip()
                header_ready = bool(text)
        except Exception:
            pass

        # Instagram markup changes often. Do not require the old exact
        # followers/following/posts trio when the profile header is visibly loaded.
        if (username_seen and classic >= 1) or (username_seen and header_ready) or (classic >= 2 and header_ready):
            return "ready"

        last_reason = f"username_seen={username_seen},classic={classic},header={header_ready}"
        page.wait_for_timeout(900)

    return f"timeout:{last_reason}"


def robust_check_profile(page, username, assigned_url):
    profile_url = f"https://www.instagram.com/{username}/"
    failures = []

    for attempt in (1, 2):
        try:
            page.goto(profile_url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3000 if attempt == 1 else 4500)
            state = robust_wait_profile(page, username)
        except Exception as exc:
            state = f"navigation_error:{type(exc).__name__}:{exc}"

        if state == "logged_out":
            checker.log(f"PROFILE_FAIL @{username} attempt={attempt} reason=logged_out")
            return {"ok": False, "detail": "Instagram session logged out"}
        if state == "not_found":
            return {
                "ok": True,
                "bio_status": "missing",
                "only_status": "issue",
                "links": [],
                "detail": "Profile unavailable/not found",
            }
        if state in {"rate_limited", "challenge"}:
            checker.log(f"PROFILE_FAIL @{username} attempt={attempt} reason={state}")
            return {"ok": False, "detail": f"Instagram profile blocked ({state})"}

        if state == "ready":
            links = checker.collect_external_links(page)
            target = checker.norm_url(assigned_url)
            normalized = {checker.norm_url(x) for x in links if checker.norm_url(x)}
            try:
                body = (
                    page.locator("body").inner_text(timeout=4000) or ""
                ).lower().replace("https://", "").replace("http://", "").replace("www.", "")
            except Exception:
                body = ""
            found = target in normalized or target.replace("www.", "") in body
            checker.log(
                f"PROFILE_OK @{username} attempt={attempt} assigned_found={found} external={len(normalized)}"
            )
            return {
                "ok": True,
                "bio_status": "verified" if found else "missing",
                "only_status": "verified" if found and normalized <= {target} else ("issue" if normalized or found else "pending"),
                "links": links,
                "detail": f"Profile inspected on attempt {attempt}; assigned bio link={'found' if found else 'not found'}; external destinations={len(normalized)}",
            }

        failures.append(str(state))
        checker.log(f"PROFILE_RETRY @{username} attempt={attempt} reason={state}")
        if attempt == 1:
            try:
                page.goto("about:blank", wait_until="domcontentloaded", timeout=5000)
            except Exception:
                pass
            page.wait_for_timeout(3500)

    reason = " | ".join(failures)[:700]
    checker.log(f"PROFILE_FAIL @{username} attempts=2 reason={reason}")
    return {"ok": False, "detail": f"Profile failed after 2 attempts: {reason}"}


checker.wait_profile = robust_wait_profile
checker.check_profile = robust_check_profile


_original_browser_worker = live._browser_worker


def staggered_browser_worker(worker_id, *args, **kwargs):
    delay = max(0, int(worker_id) - 1) * 3
    if delay:
        checker.log(f"CLOUD_CHECKER_WORKER_STAGGER worker={worker_id} delay={delay}s")
        time.sleep(delay)
    return _original_browser_worker(worker_id, *args, **kwargs)


live._browser_worker = staggered_browser_worker
checker.log("CLOUD_CHECKER_V38_PROFILE_HEALTH_PATCH_ACTIVE workers=3 retries=2 stagger=3s")


if __name__ == "__main__":
    live.init_session_table()
    threading.Thread(target=live.worker_loop, daemon=True).start()
    live.app.run(host="0.0.0.0", port=live.PORT, debug=False, use_reloader=False)
