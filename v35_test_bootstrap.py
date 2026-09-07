print("V35_EXACT_BOOTSTRAP_ENTER", flush=True)

from datetime import datetime, timezone, timedelta
import logging

import bot

# Security: python-telegram-bot uses httpx internally. At INFO level httpx logs
# the full Telegram Bot API request URL, which contains the bot token.
# Keep normal application logs, but suppress request-level logs from HTTP clients.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram.request").setLevel(logging.WARNING)

import patch_runner  # applies existing bulk + checker patches without starting bot.main()
import v27_runner

# Exact usernames actually returned to the Windows checker in the current test run.
ACTIVE_TEST_USERNAMES = set()


def _norm_username(value):
    return str(value or "").strip().lstrip("@").lower()


# Wrap the already-patched /api/verifier/targets endpoint and remember exactly
# which creators Windows was given. Completion/fallback must use this exact set.
_current_targets = bot.tracker_api.view_functions.get("verifier_targets")
if _current_targets and not getattr(_current_targets, "_exact_target_capture", False):
    def _capture_exact_targets(*args, **kwargs):
        response = _current_targets(*args, **kwargs)
        try:
            data = response.get_json() or {}
            targets = data.get("targets") or []
            ACTIVE_TEST_USERNAMES.clear()
            ACTIVE_TEST_USERNAMES.update(
                _norm_username(t.get("username"))
                for t in targets
                if _norm_username(t.get("username"))
            )
            bot.logger.warning(
                "V35_EXACT_TEST_TARGETS captured=%s",
                sorted(ACTIVE_TEST_USERNAMES),
            )
        except Exception:
            bot.logger.exception("V35 exact target capture failed")
        return response

    _capture_exact_targets._exact_target_capture = True
    bot.tracker_api.view_functions["verifier_targets"] = _capture_exact_targets


def _exact_test_batch_complete():
    control = bot.get_local_verifier_control() or {}
    state = str(control.get("status") or "")
    if state not in {"requested", "running"}:
        return False

    names = set(ACTIVE_TEST_USERNAMES)
    if not names:
        bot.logger.warning("V35 exact completion: no captured targets yet")
        return False

    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM campaign_links
                WHERE is_active=TRUE
                  AND LOWER(COALESCE(source_type,'instagram'))='instagram'
                  AND LOWER(instagram_username) = ANY(%s)
                ORDER BY id
                """,
                (list(names),),
            )
            links = cur.fetchall()

    found = {_norm_username(x.get("instagram_username")) for x in links}
    if found != names:
        bot.logger.warning(
            "V35 exact completion mismatch captured=%s db=%s",
            sorted(names), sorted(found),
        )
        return False

    fresh_after = datetime.now(timezone.utc) - timedelta(minutes=10)
    details = []
    for cl in links:
        day = bot.current_campaign_day(cl)
        v = bot.get_verification_row(cl["id"], day) or {}
        checked_at = v.get("auto_checked_at")
        mode = str(v.get("checker_mode") or "").lower()
        is_fresh = bool(checked_at and checked_at >= fresh_after and mode == "local_browser")
        details.append((_norm_username(cl.get("instagram_username")), str(checked_at), mode, is_fresh))
        if not is_fresh:
            bot.logger.warning("V35 exact batch waiting details=%s", details)
            return False

    bot.logger.warning("V35_EXACT_BROWSER_BATCH_COMPLETE details=%s", details)
    return True


v27_runner._all_browser_results_received_for_current_run = _exact_test_batch_complete

_previous_pending_selector = bot._hybrid_pending_targets


def _exact_pending_targets():
    names = set(ACTIVE_TEST_USERNAMES)
    rows = _previous_pending_selector()
    if not names:
        return []
    exact = [x for x in rows if _norm_username(x[0].get("instagram_username")) in names]
    bot.logger.warning(
        "V35_EXACT_APIFY_TARGETS creators=%s",
        [_norm_username(x[0].get("instagram_username")) for x in exact],
    )
    return exact


bot._hybrid_pending_targets = _exact_pending_targets
bot.logger.warning("V35_EXACT_HANDOFF_PATCH_ACTIVE")
print("V35_EXACT_HANDOFF_PATCH_ACTIVE", flush=True)

if __name__ == "__main__":
    bot.main()
