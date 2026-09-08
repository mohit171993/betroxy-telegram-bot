import asyncio
import html

import bot
import v57_reliable_banner_bootstrap as v57
import v40_apify_only_bootstrap as v40
import v41_apify_balance_reports_bootstrap as v41

# V58 - admin-only force recheck of ALL active Instagram creators.
# Normal Smart Check behavior remains unchanged: same-day full PASS rows are skipped.

_previous_callback_handler = bot.callback_handler
_previous_campaign_menu = bot.campaign_menu
_previous_verification_list_keyboard = bot.verification_list_keyboard


def _append_force_button(markup):
    rows = [list(r) for r in markup.inline_keyboard]
    # Avoid duplicates when menus are nested/wrapped.
    if any(
        getattr(btn, "callback_data", None) == "verify_force_all_prompt"
        for row in rows for btn in row
    ):
        return markup
    # Put the admin override near the Smart Check controls without altering existing actions.
    insert_at = 1 if rows else 0
    rows.insert(insert_at, [
        bot.InlineKeyboardButton(
            "🔁 Recheck All Links",
            callback_data="verify_force_all_prompt",
            api_kwargs={"style": "danger"},
        )
    ])
    return bot.InlineKeyboardMarkup(rows)


def v58_campaign_menu():
    return _append_force_button(_previous_campaign_menu())


def v58_verification_list_keyboard(rows, day=1, page=0, per_page=8):
    return _append_force_button(
        _previous_verification_list_keyboard(rows, day=day, page=page, per_page=per_page)
    )


def _run_apify_force_all():
    """Reuse the proven V40 checker while bypassing only its PASS-skip filter."""
    original_all_verified = v40._all_verified
    v40._all_verified = lambda _v: False
    try:
        return v40.run_apify_smart_check_current_days()
    finally:
        v40._all_verified = original_all_verified


async def v58_callback_handler(update, context):
    q = update.callback_query
    data = q.data if q else ""

    if data == "verify_force_all_prompt":
        await q.answer()
        if not bot.is_admin(q.from_user.id):
            return
        await q.message.reply_text(
            "⚠️ <b>Recheck ALL active links?</b>\n\n"
            "This bypasses the normal same-day PASS skip and sends every active Instagram creator through Smart Check again.\n\n"
            "This can use more Apify credits than a normal Smart Check.",
            parse_mode=bot.ParseMode.HTML,
            reply_markup=bot.InlineKeyboardMarkup([
                [bot.InlineKeyboardButton(
                    "✅ Yes — Recheck All",
                    callback_data="verify_force_all_confirm",
                    api_kwargs={"style": "danger"},
                )],
                [bot.InlineKeyboardButton("❌ Cancel", callback_data="verify_home")],
            ]),
        )
        return

    if data == "verify_force_all_confirm":
        await q.answer()
        if not bot.is_admin(q.from_user.id):
            return
        if not bot.APIFY_TOKEN:
            await q.message.reply_text(
                "❌ <b>Apify is not connected.</b>",
                parse_mode=bot.ParseMode.HTML,
                reply_markup=bot.campaign_menu(),
            )
            return

        # Preserve V41's fail-closed balance guard.
        try:
            before = await asyncio.to_thread(v41.apify_account_allowance)
        except Exception as exc:
            bot.logger.exception("V58_FORCE_ALL_BALANCE_PRECHECK_FAILED")
            await q.message.reply_text(
                "🛑 <b>Recheck All blocked — balance could not be verified</b>\n\n"
                f"<code>{html.escape(str(exc)[:1000])}</code>\n\n"
                "No Apify actor was launched.",
                parse_mode=bot.ParseMode.HTML,
                reply_markup=bot.campaign_menu(),
            )
            return

        if before["remaining"] <= 0.0:
            await q.message.reply_text(
                "🛑 <b>Recheck All blocked — no Apify allowance remaining</b>\n\n"
                f"Remaining: <b>{v41._money(before['remaining'])}</b>",
                parse_mode=bot.ParseMode.HTML,
                reply_markup=bot.campaign_menu(),
            )
            return

        # V45 has already wrapped this begin/finish pair for persistent history.
        if not v40._begin_apify_run():
            await q.message.reply_text(
                "⏳ <b>Smart Check already running</b>\n\n"
                "A second paid run was not started.",
                parse_mode=bot.ParseMode.HTML,
                reply_markup=bot.campaign_menu(),
            )
            return

        await q.message.reply_text(
            "🔁 <b>Full Recheck started</b>\n\n"
            "All active Instagram creators will be checked again.\n"
            "Normal PASS-skip protection is bypassed for this run only.",
            parse_mode=bot.ParseMode.HTML,
            reply_markup=bot.campaign_menu(),
        )

        try:
            result = await asyncio.to_thread(_run_apify_force_all)
            warning = bool(result.get("warnings"))
            await asyncio.sleep(1.5)
            try:
                after = await asyncio.to_thread(v41.apify_account_allowance)
            except Exception:
                after = None

            cost_line = "Usage after run: unavailable"
            if after:
                delta = max(0.0, after["used"] - before["used"])
                cost_line = (
                    f"Recorded usage increase: <b>{v41._money(delta)}</b>\n"
                    f"Remaining allowance: <b>{v41._money(after['remaining'])}</b>"
                )

            summary = (
                f"Force-all Apify complete: total={result['total']}, checked={result['checked']}, "
                f"profile_records={result['profile_records']}, story_records={result['story_records']}, "
                f"PASS={result['pass_count']}, FIX={result['issue_count']}, REVIEW={result['manual_count']}"
            )
            v40._finish_apify_run(summary, warning=warning)

            warning_text = ""
            if warning:
                warning_text = "\n\n⚠️ " + html.escape(" | ".join(result["warnings"])[:1000])

            await q.message.reply_text(
                "✅ <b>Full Recheck complete</b>\n\n"
                f"Active Instagram creators: <b>{result['total']}</b>\n"
                f"Checked now: <b>{result['checked']}</b>\n"
                "Skipped because of previous PASS: <b>0</b>\n"
                f"Profile records: <b>{result['profile_records']}</b>\n"
                f"Story records: <b>{result['story_records']}</b>\n\n"
                f"PASS: <b>{result['pass_count']}</b>\n"
                f"ACTION REQUIRED: <b>{result['issue_count']}</b>\n"
                f"MANUAL REVIEW: <b>{result['manual_count']}</b>\n\n"
                + cost_line + warning_text,
                parse_mode=bot.ParseMode.HTML,
                reply_markup=bot.campaign_menu(),
            )
        except Exception as exc:
            bot.logger.exception("V58_FORCE_ALL_FAILED")
            v40._finish_apify_run(
                f"Force-all failed: {type(exc).__name__}: {exc}",
                warning=True,
            )
            await q.message.reply_text(
                "❌ <b>Full Recheck failed</b>\n\n"
                f"<code>{html.escape(str(exc)[:1400])}</code>",
                parse_mode=bot.ParseMode.HTML,
                reply_markup=bot.campaign_menu(),
            )
        return

    return await _previous_callback_handler(update, context)


bot.campaign_menu = v58_campaign_menu
bot.verification_list_keyboard = v58_verification_list_keyboard
bot.callback_handler = v58_callback_handler
bot.logger.warning("V58_FORCE_RECHECK_ALL active=on normal_skip_unchanged=on")


if __name__ == "__main__":
    bot.main()
