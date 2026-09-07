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
    # Unknown/custom non-final browser states are treated as unresolved.
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


# Replace the original selector used by run_apify_fallback_for_pending().
bot._hybrid_pending_targets = _v27_hybrid_pending_targets


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
        # Keep Telegram message safely under the limit.
        body = "\n".join(lines[:35])
        if len(lines) > 35:
            body += f"\n… +{len(lines)-35} more"
        return head + "\n" + body
    except Exception as exc:
        bot.logger.warning("V27 progress rendering failed: %s", exc)
        return ""


def _patch_status_renderers():
    # Locate the existing Smart Checker status function without depending on its name.
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


_patch_status_renderers()
bot.logger.info("BETROXY checker V27 patch active")

if __name__ == "__main__":
    bot.main()
