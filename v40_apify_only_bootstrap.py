import asyncio
import html
import logging
from datetime import datetime, timezone, timedelta

import bot

# Keep request logs from exposing sensitive Telegram URLs/tokens.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram.request").setLevel(logging.WARNING)

APIFY_ONLY_RUNNING = "apify_only_running"
STALE_AFTER = timedelta(minutes=20)
_original_callback_handler = bot.callback_handler
_original_report_text = bot.automatic_verification_report_text


def _all_verified(v):
    return all(
        (v.get(k) or "pending") == "verified"
        for k in (
            "bio_status",
            "only_our_link_status",
            "story_status",
            "story_link_status",
        )
    )


def _active_instagram_rows():
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
            return cur.fetchall()


def _begin_apify_run():
    now = datetime.now(timezone.utc)
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM verifier_control WHERE id=1 FOR UPDATE")
            control = cur.fetchone() or {}
            state = str(control.get("status") or "").lower()
            requested_at = control.get("requested_at")
            if requested_at and requested_at.tzinfo is None:
                requested_at = requested_at.replace(tzinfo=timezone.utc)
            if (
                state == APIFY_ONLY_RUNNING
                and requested_at
                and now - requested_at <= STALE_AFTER
            ):
                conn.commit()
                return False

            cur.execute(
                """
                UPDATE verifier_control
                SET run_token=NULL,
                    requested_at=NOW(),
                    claimed_at=NOW(),
                    completed_at=NULL,
                    status=%s,
                    result_summary='Manual Apify-only Smart Check running'
                WHERE id=1
                """,
                (APIFY_ONLY_RUNNING,),
            )
        conn.commit()
    return True


def _finish_apify_run(summary, warning=False):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE verifier_control
                SET status=%s,
                    completed_at=NOW(),
                    result_summary=%s
                WHERE id=1
                """,
                ("completed_with_warning" if warning else "completed", summary[:3900]),
            )
        conn.commit()


def _status_counts_current_days(rows):
    counts = {"PASS": 0, "ACTION REQUIRED": 0, "MANUAL REVIEW": 0}
    for row in rows:
        day = bot.current_campaign_day(row)
        v = bot.get_verification_row(row["id"], day) or {}
        pseudo = {
            "bio_status": v.get("bio_status") or "pending",
            "only_our_link_status": v.get("only_our_link_status") or "pending",
            "story_status": v.get("story_status") or "pending",
            "story_link_status": v.get("story_link_status") or "pending",
        }
        counts[bot.verification_final_result(pseudo)] += 1
    return counts


def run_apify_smart_check_current_days():
    if not bot.APIFY_TOKEN:
        raise RuntimeError("APIFY_TOKEN is missing in Railway Variables.")

    rows = _active_instagram_rows()
    eligible = []
    skipped_pass = 0

    # Preserve the same-day cost rule: creators already fully PASS for their
    # current campaign day are not sent to Apify again.
    for row in rows:
        day = bot.current_campaign_day(row)
        v = bot.get_verification_row(row["id"], day) or {}
        if _all_verified(v):
            skipped_pass += 1
            continue
        eligible.append((row, day, v))

    if not eligible:
        counts = _status_counts_current_days(rows)
        return {
            "total": len(rows),
            "checked": 0,
            "skipped_pass": skipped_pass,
            "profile_records": 0,
            "story_records": 0,
            "pass_count": counts["PASS"],
            "issue_count": counts["ACTION REQUIRED"],
            "manual_count": counts["MANUAL REVIEW"],
            "warnings": [],
        }

    usernames = sorted({
        str(row["instagram_username"]).strip().lstrip("@")
        for row, _day, _v in eligible
    })

    profile_items = []
    story_items = []
    warnings = []

    try:
        profile_items = bot._apify_actor_sync(
            bot.APIFY_PROFILE_ACTOR_ID,
            {"usernames": usernames},
        )
    except Exception as exc:
        warnings.append(f"Profile actor: {type(exc).__name__}: {exc}")
        bot.logger.exception("APIFY_ONLY profile actor failed")

    try:
        story_items = bot._apify_actor_sync(
            bot.APIFY_STORY_ACTOR_ID,
            {"usernames": usernames},
        )
    except Exception as exc:
        warnings.append(f"Story actor: {type(exc).__name__}: {exc}")
        bot.logger.exception("APIFY_ONLY story actor failed")

    pmap = {}
    for item in profile_items:
        if isinstance(item, dict):
            u = bot._profile_username(item)
            if u:
                pmap[u] = item

    smap = {}
    for item in story_items:
        if isinstance(item, dict):
            u = bot._story_username(item)
            if u:
                smap.setdefault(u, []).append(item)

    for row, day, old_v in eligible:
        username = str(row["instagram_username"]).strip().lstrip("@")
        key = username.lower()
        assigned = f"{bot.PUBLIC_BASE_URL}/{row['slug']}"

        profile = pmap.get(key)
        stories = smap.get(key, [])
        bio_status = only_status = None
        story_status = story_link_status = None
        bio_links = []
        details = []

        if profile is not None:
            bio_links = bot._profile_links(profile)
            normalized = {bot._apify_norm(x) for x in bio_links if bot._apify_norm(x)}
            target = bot._apify_norm(assigned)
            bio_status = "verified" if target in normalized else "missing"
            only_status = "verified" if normalized == {target} else "issue"
            details.append("Apify profile checked")
        else:
            details.append("Apify profile record unavailable")

        if stories:
            story_status = "verified"
            links = []
            for s in stories:
                links.extend(bot._story_links(s))
            story_link_status = (
                "verified"
                if any(bot._apify_norm(x) == bot._apify_norm(assigned) for x in links)
                else "issue"
            )
            details.append(f"Apify stories checked={len(stories)}")
        elif profile is not None and not warnings:
            story_status = "missing"
            story_link_status = "missing"
            details.append("Apify returned no active Story")
        else:
            details.append("Apify Story result unavailable")

        auto_status = "checked" if any(
            x is not None
            for x in (bio_status, only_status, story_status, story_link_status)
        ) else "unknown"

        bot.save_auto_verification_result(
            row["id"],
            day,
            bio_status=bio_status,
            only_status=only_status,
            story_status=story_status,
            story_link_status=story_link_status,
            auto_status=auto_status,
            detail=" | ".join(details),
            bio_links=bio_links,
            story_count=len(stories),
            checker_mode="apify_only",
        )

    counts = _status_counts_current_days(rows)
    return {
        "total": len(rows),
        "checked": len(eligible),
        "skipped_pass": skipped_pass,
        "profile_records": len(profile_items),
        "story_records": len(story_items),
        "pass_count": counts["PASS"],
        "issue_count": counts["ACTION REQUIRED"],
        "manual_count": counts["MANUAL REVIEW"],
        "warnings": warnings,
    }


def apify_only_campaign_menu():
    return bot.InlineKeyboardMarkup(
        [
            [
                bot.InlineKeyboardButton("☁️ Run Smart Check", callback_data="verify_request_local_run"),
                bot.InlineKeyboardButton("🟡 Auto Report", callback_data="verify_auto_report:1"),
            ],
            [
                bot.InlineKeyboardButton("✅ Verification Center", callback_data="verify_home"),
                bot.InlineKeyboardButton("📄 Compliance PDF", callback_data="verify_pdf:1"),
            ],
            [
                bot.InlineKeyboardButton("📊 Campaign Report", callback_data="campaign_report"),
                bot.InlineKeyboardButton("📅 Today", callback_data="campaign_today"),
            ],
            [
                bot.InlineKeyboardButton("🏆 Top Pages", callback_data="campaign_top"),
                bot.InlineKeyboardButton("🔗 Creator Links", callback_data="campaign_links"),
            ],
            [
                bot.InlineKeyboardButton("➕ Add Creator", callback_data="campaign_add_single"),
                bot.InlineKeyboardButton("📚 Bulk Create", callback_data="campaign_add_bulk"),
            ],
            [
                bot.InlineKeyboardButton("✅ Sync Final Links", callback_data="campaign_sync_final"),
                bot.InlineKeyboardButton("📥 Export CSV", callback_data="campaign_export"),
            ],
            [
                bot.InlineKeyboardButton("🔴 Disable Link", callback_data="campaign_disable_by_link"),
                bot.InlineKeyboardButton("🗑 Delete Link", callback_data="campaign_delete_by_link"),
            ],
            [
                bot.InlineKeyboardButton("☁️ Apify Status", callback_data="verify_hybrid_status"),
                bot.InlineKeyboardButton("🎨 Landing Design", callback_data="theme_home"),
            ],
            [
                bot.InlineKeyboardButton("🔄 Refresh Dashboard", callback_data="campaign_home"),
                bot.InlineKeyboardButton("⬅️ Admin Panel", callback_data="admin_home"),
            ],
        ]
    )


def apify_only_verification_list_keyboard(rows, day=1, page=0, per_page=8):
    total_pages = max(1, (len(rows) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    subset = rows[page * per_page:(page + 1) * per_page]
    buttons = []

    for r in subset:
        bio = bot.VERIFY_STATUS_LABEL.get(r["bio_status"], "⏳")
        only = bot.VERIFY_STATUS_LABEL.get(r["only_our_link_status"], "⏳")
        story = bot.VERIFY_STATUS_LABEL.get(r["story_status"], "⏳")
        name = str(r["instagram_username"])[:22]
        buttons.append([
            bot.InlineKeyboardButton(
                f"{bio}{only}{story} @{name}",
                callback_data=f"verify_creator:{r['campaign_link_id']}:{day}",
            )
        ])

    nav = []
    if page > 0:
        nav.append(bot.InlineKeyboardButton("⬅️", callback_data=f"verify_page:{day}:{page-1}"))
    nav.append(bot.InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="campaign_noop"))
    if page < total_pages - 1:
        nav.append(bot.InlineKeyboardButton("➡️", callback_data=f"verify_page:{day}:{page+1}"))
    buttons.append(nav)

    day_buttons = []
    for d in range(1, 8):
        day_buttons.append(bot.InlineKeyboardButton(str(d), callback_data=f"verify_day:{d}"))
        if len(day_buttons) == 4:
            buttons.append(day_buttons)
            day_buttons = []
    if day_buttons:
        buttons.append(day_buttons)

    buttons.append([
        bot.InlineKeyboardButton("☁️ Run Smart Check", callback_data="verify_request_local_run"),
        bot.InlineKeyboardButton("📊 Auto Report", callback_data=f"verify_auto_report:{day}"),
    ])
    buttons.append([
        bot.InlineKeyboardButton("☁️ Apify Status", callback_data="verify_hybrid_status"),
        bot.InlineKeyboardButton("📄 Compliance PDF", callback_data=f"verify_pdf:{day}"),
    ])
    buttons.append([
        bot.InlineKeyboardButton("⬅️ Campaign Tracker", callback_data="campaign_home"),
    ])
    return bot.InlineKeyboardMarkup(buttons)


def apify_only_report_text(rows, day):
    text = _original_report_text(rows, day)
    return text.replace(
        "REVIEW = browser + fallback could not confirm everything",
        "REVIEW = Apify could not confirm everything",
    )


async def apify_only_callback_handler(update, context):
    q = update.callback_query
    data = q.data if q else ""

    if data == "verify_request_local_run":
        await q.answer()
        if not bot.is_admin(q.from_user.id):
            return
        if not bot.APIFY_TOKEN:
            await q.message.reply_text(
                "❌ <b>Apify is not connected.</b>\n\n"
                "APIFY_TOKEN is missing in Railway Variables.",
                parse_mode=bot.ParseMode.HTML,
                reply_markup=bot.campaign_menu(),
            )
            return
        if not _begin_apify_run():
            await q.message.reply_text(
                "⏳ <b>Apify Smart Check already running</b>\n\n"
                "A second paid run was not started.",
                parse_mode=bot.ParseMode.HTML,
                reply_markup=bot.campaign_menu(),
            )
            return

        await q.message.reply_text(
            "☁️ <b>Apify-only Smart Check started</b>\n\n"
            "Browser checker is no longer used.\n"
            "Only active Instagram creators that are not already full PASS for their current campaign day are checked.\n"
            "There is no automatic Apify schedule; usage occurs only when you press this button.",
            parse_mode=bot.ParseMode.HTML,
            reply_markup=bot.campaign_menu(),
        )

        try:
            result = await asyncio.to_thread(run_apify_smart_check_current_days)
            warning = bool(result.get("warnings"))
            summary = (
                f"Apify-only complete: total={result['total']}, checked={result['checked']}, "
                f"same-day PASS skipped={result['skipped_pass']}, "
                f"profile_records={result['profile_records']}, story_records={result['story_records']}, "
                f"PASS={result['pass_count']}, FIX={result['issue_count']}, REVIEW={result['manual_count']}"
            )
            _finish_apify_run(summary, warning=warning)

            warning_text = ""
            if warning:
                warning_text = "\n\n⚠️ " + html.escape(" | ".join(result["warnings"])[:1200])

            await q.message.reply_text(
                "✅ <b>Apify Smart Check complete</b>\n\n"
                f"Active Instagram creators: <b>{result['total']}</b>\n"
                f"Checked now: <b>{result['checked']}</b>\n"
                f"Same-day full PASS skipped: <b>{result['skipped_pass']}</b>\n"
                f"Profile records: <b>{result['profile_records']}</b>\n"
                f"Story records: <b>{result['story_records']}</b>\n\n"
                f"PASS: <b>{result['pass_count']}</b>\n"
                f"ACTION REQUIRED: <b>{result['issue_count']}</b>\n"
                f"MANUAL REVIEW: <b>{result['manual_count']}</b>"
                + warning_text,
                parse_mode=bot.ParseMode.HTML,
                reply_markup=bot.campaign_menu(),
            )
        except Exception as exc:
            bot.logger.exception("APIFY_ONLY Smart Check failed")
            _finish_apify_run(
                f"Apify-only failed: {type(exc).__name__}: {exc}",
                warning=True,
            )
            await q.message.reply_text(
                "❌ <b>Apify Smart Check failed</b>\n\n"
                f"<code>{html.escape(str(exc)[:1400])}</code>",
                parse_mode=bot.ParseMode.HTML,
                reply_markup=bot.campaign_menu(),
            )
        return

    if data == "verify_hybrid_status":
        await q.answer()
        control = bot.get_local_verifier_control() or {}
        state = str(control.get("status") or "idle")
        last = control.get("completed_at")
        last_text = last.strftime("%d %b %Y %H:%M UTC") if last else "Never"
        summary = html.escape(str(control.get("result_summary") or "No Apify-only run completed yet"))
        await q.message.reply_text(
            "☁️ <b>BETROXY Apify Checker</b>\n\n"
            f"Status: <b>{html.escape(state)}</b>\n"
            f"Apify token: <b>{'✅ Configured' if bot.APIFY_TOKEN else '❌ Missing'}</b>\n"
            f"Last completed: <b>{last_text}</b>\n"
            "Mode: <b>Apify only — manual</b>\n"
            "Browser checker: <b>OFF</b>\n"
            "Automatic schedule: <b>OFF</b>\n\n"
            f"Last summary: {summary}",
            parse_mode=bot.ParseMode.HTML,
            reply_markup=bot.campaign_menu(),
        )
        return

    return await _original_callback_handler(update, context)


# Clear any old browser-run control state. This does not alter campaign results,
# Chrome profiles, saved login data, or the cloud-checker volume.
def _reset_old_checker_control():
    try:
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE verifier_control
                    SET run_token=NULL,
                        claimed_at=NULL,
                        completed_at=NOW(),
                        status='idle',
                        result_summary='Apify-only mode active; browser checker disconnected'
                    WHERE id=1
                    """
                )
            conn.commit()
    except Exception:
        bot.logger.exception("APIFY_ONLY could not reset old verifier control")


bot.campaign_menu = apify_only_campaign_menu
bot.verification_list_keyboard = apify_only_verification_list_keyboard
bot.automatic_verification_report_text = apify_only_report_text
bot.callback_handler = apify_only_callback_handler
_reset_old_checker_control()
bot.logger.warning("APIFY_ONLY_SMART_CHECK_ACTIVE browser_checker=off schedule=off same_day_pass_skip=on")


if __name__ == "__main__":
    bot.main()
