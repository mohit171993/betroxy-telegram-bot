import inspect
import asyncio
import bot

# V27 smart-checker patch
# Clear browser results remain final. Only unresolved/unclear fields are sent to Apify.
FINAL_STATUSES = {
    "verified", "missing", "issue", "pass", "passed", "ok", "present",
    "found", "fix", "fixed", "fail", "failed", "no", "yes"
}
UNRESOLVED_STATUSES = {
    "", "pending", "unknown", "unclear", "timeout", "timed_out", "error",
    "blocked", "not_loaded", "not-load", "notloaded", "no_result", "unresolved",
    "checking", "queued", "waiting", "none", "null"
}


def _needs_fallback(value):
    if value is None:
        return True
    s = str(value).strip().lower()
    if s in FINAL_STATUSES:
        return False
    if s in UNRESOLVED_STATUSES:
        return True
    return True


def _v27_hybrid_pending_targets():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM campaign_links
                WHERE is_active=TRUE
                  AND LOWER(COALESCE(source_type,'instagram'))='instagram'
                ORDER BY id
                """
            )
            links = cur.fetchall()

    out = []
    for cl in links:
        day = bot.current_campaign_day(cl)
        v = bot.get_verification_row(cl["id"], day) or {}
        pending = {
            "bio": _needs_fallback(v.get("bio_status")),
            "only": _needs_fallback(v.get("only_our_link_status")),
            "story": _needs_fallback(v.get("story_status")),
            "story_link": _needs_fallback(v.get("story_link_status")),
        }
        if any(pending.values()):
            out.append((cl, day, v, pending))
    return out


bot._hybrid_pending_targets = _v27_hybrid_pending_targets


def _all_browser_results_received_for_current_run():
    """Return True once every active Instagram creator posted a result after this run was requested."""
    control = bot.get_local_verifier_control() or {}
    requested_at = control.get("requested_at")
    state = str(control.get("status") or "")
    # Older/current Windows V35 listeners fetch /targets directly and may never
    # claim /api/verifier/command, so the server can legitimately remain 'requested'.
    if not requested_at or state not in {"requested", "running"}:
        return False

    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM campaign_links
                WHERE is_active=TRUE
                  AND LOWER(COALESCE(source_type,'instagram'))='instagram'
                ORDER BY id
                """
            )
            links = cur.fetchall()

    if not links:
        return True

    for cl in links:
        day = bot.current_campaign_day(cl)
        v = bot.get_verification_row(cl["id"], day) or {}
        checked_at = v.get("auto_checked_at")
        mode = str(v.get("checker_mode") or "").lower()
        if not checked_at or checked_at < requested_at or mode != "local_browser":
            return False
    return True


def _start_fallback_if_browser_batch_complete():
    """Atomically start Apify fallback after the final browser result arrives."""
    try:
        if not _all_browser_results_received_for_current_run():
            return

        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE verifier_control
                    SET status='apify_fallback',
                        result_summary=COALESCE(result_summary,'') || ' | Browser batch complete (server detected)'
                    WHERE id=1 AND status IN ('requested','running')
                    RETURNING run_token
                    """
                )
                row = cur.fetchone()
            conn.commit()

        if not row or not row.get("run_token"):
            return

        token = str(row["run_token"])
        summary = "Browser batch complete (server detected)"
        bot.logger.warning("SMART_CHECKER_AUTO_COMPLETE token=%s; starting Apify fallback", token[:8])
        bot.Thread(
            target=bot._finish_hybrid_run_in_background,
            args=(token, summary),
            daemon=True,
        ).start()
    except Exception:
        bot.logger.exception("SMART_CHECKER_AUTO_COMPLETE failed")


def _patch_verifier_result_endpoint():
    endpoint = "verifier_result"
    original = bot.tracker_api.view_functions.get(endpoint)
    if not original or getattr(original, "_v27_auto_complete_wrapped", False):
        return

    def wrapped_verifier_result(*args, **kwargs):
        response = original(*args, **kwargs)
        try:
            status_code = 200
            if isinstance(response, tuple) and len(response) >= 2:
                status_code = int(response[1])
            elif hasattr(response, "status_code"):
                status_code = int(response.status_code)
            if 200 <= status_code < 300:
                _start_fallback_if_browser_batch_complete()
        except Exception:
            bot.logger.exception("SMART_CHECKER result wrapper failed")
        return response

    wrapped_verifier_result._v27_auto_complete_wrapped = True
    bot.tracker_api.view_functions[endpoint] = wrapped_verifier_result
    bot.logger.warning("SMART_CHECKER_SERVER_AUTO_COMPLETE_PATCH_V2_ACTIVE")


def _compact_progress():
    try:
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM campaign_links
                    WHERE is_active=TRUE
                      AND LOWER(COALESCE(source_type,'instagram'))='instagram'
                    ORDER BY id
                    """
                )
                links = cur.fetchall()

        lines = []
        completed = 0
        for cl in links:
            day = bot.current_campaign_day(cl)
            v = bot.get_verification_row(cl["id"], day) or {}
            vals = [
                v.get("bio_status"),
                v.get("only_our_link_status"),
                v.get("story_status"),
                v.get("story_link_status"),
            ]
            unresolved = sum(1 for x in vals if _needs_fallback(x))
            if unresolved == 0:
                icon = "✅"
                label = "clear"
                completed += 1
            else:
                mode = str(v.get("checker_mode") or "").lower()
                auto = str(v.get("auto_check_status") or "").lower()
                if "apify" in mode or "apify" in auto:
                    icon = "☁️"
                    label = f"Apify ({unresolved} left)"
                elif auto in {"running", "checking"}:
                    icon = "🔵"
                    label = f"checking ({unresolved} left)"
                else:
                    icon = "⏳"
                    label = f"unresolved ({unresolved})"
            handle = str(cl.get("instagram_username") or cl.get("slug") or cl.get("id"))
            if not handle.startswith("@"):
                handle = "@" + handle
            lines.append(f"{icon} {handle} — {label}")

        total = len(links)
        head = f"\n\n<b>Live progress:</b> {completed}/{total} clear"
        if not lines:
            return head + "\nNo active Instagram links."
        body = "\n".join(lines[:35])
        if len(lines) > 35:
            body += f"\n… +{len(lines)-35} more"
        return head + "\n" + body
    except Exception as exc:
        bot.logger.warning("V27 progress rendering failed: %s", exc)
        return ""


def _patch_status_renderers():
    for name, fn in list(vars(bot).items()):
        if not callable(fn) or getattr(fn, "__module__", None) != "bot":
            continue
        try:
            src = inspect.getsource(fn)
        except Exception:
            continue
        if "BETROXY Smart Checker" not in src:
            continue
        if inspect.iscoroutinefunction(fn):
            async def async_wrapper(*args, __fn=fn, **kwargs):
                result = await __fn(*args, **kwargs)
                if isinstance(result, str):
                    return result + _compact_progress()
                return result
            setattr(bot, name, async_wrapper)
        else:
            def sync_wrapper(*args, __fn=fn, **kwargs):
                result = __fn(*args, **kwargs)
                if isinstance(result, str):
                    return result + _compact_progress()
                return result
            setattr(bot, name, sync_wrapper)
        bot.logger.info("V27 patched Smart Checker status renderer: %s", name)


_patch_verifier_result_endpoint()
_patch_status_renderers()
bot.logger.info("BETROXY checker V27 patch active")

if __name__ == "__main__":
    bot.main()
