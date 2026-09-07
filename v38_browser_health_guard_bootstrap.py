import bot
import v27_runner
import v36_duplicate_guard_bootstrap  # loads current production checker patches without starting bot.main()


# V38 safety guard:
# If the browser batch itself is clearly unhealthy (most creators return all
# four fields unresolved in the current run), do NOT spend money on Apify.
# Reset only verifier_control to idle so the admin can fix/retry the browser.

_original_start_fallback = v27_runner._start_fallback_if_browser_batch_complete


def _current_browser_health():
    control = bot.get_local_verifier_control() or {}
    requested_at = control.get("requested_at")
    if not requested_at:
        return 0, 0

    total = 0
    all_unresolved = 0
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

    for cl in links:
        day = bot.current_campaign_day(cl)
        v = bot.get_verification_row(cl["id"], day) or {}
        checked_at = v.get("auto_checked_at")
        mode = str(v.get("checker_mode") or "").lower()
        if not checked_at or checked_at < requested_at or mode != "local_browser":
            continue
        total += 1
        vals = [
            v.get("bio_status"),
            v.get("only_our_link_status"),
            v.get("story_status"),
            v.get("story_link_status"),
        ]
        if all(v27_runner._needs_fallback(x) for x in vals):
            all_unresolved += 1

    return total, all_unresolved


def guarded_start_fallback_if_browser_batch_complete():
    try:
        if not v27_runner._all_browser_results_received_for_current_run():
            return

        total, all_unresolved = _current_browser_health()
        threshold = max(3, (total * 3 + 4) // 5)  # about 60%, rounded up
        if total >= 3 and all_unresolved >= threshold:
            with bot.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE verifier_control
                        SET status='idle',
                            result_summary=%s
                        WHERE id=1 AND status IN ('requested','running')
                        """,
                        (
                            f"Browser health guard: {all_unresolved}/{total} creators returned all fields unresolved; Apify fallback blocked",
                        ),
                    )
                conn.commit()
            bot.logger.error(
                "SMART_CHECK_BROWSER_HEALTH_GUARD blocked Apify: all_unresolved=%s total=%s",
                all_unresolved,
                total,
            )
            return
    except Exception:
        bot.logger.exception("SMART_CHECK_BROWSER_HEALTH_GUARD check failed")

    return _original_start_fallback()


v27_runner._start_fallback_if_browser_batch_complete = guarded_start_fallback_if_browser_batch_complete
bot.logger.warning("SMART_CHECK_BROWSER_HEALTH_GUARD_ACTIVE")


if __name__ == "__main__":
    bot.main()
