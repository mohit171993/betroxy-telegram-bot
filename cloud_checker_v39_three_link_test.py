import threading

import cloud_checker_v38_bootstrap as v38
import cloud_checker_worker as checker
import cloud_checker_live as live

# Controlled browser-only validation. No Apify handoff is allowed from this test.
TEST_USERNAMES = {
    "mumbai_indians_brand_status",
    "cricxcratee",
    "ultra_ro45",
}

_original_api_get = checker.api_get
_original_api_post = checker.api_post


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


if __name__ == "__main__":
    live.init_session_table()
    threading.Thread(target=live.worker_loop, daemon=True).start()
    live.app.run(host="0.0.0.0", port=live.PORT, debug=False, use_reloader=False)
