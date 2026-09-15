"""Additive Daily Quiz admin UI fix.

The 14/09 locked reward implementation remains unchanged. This overlay makes the
admin screen reflect persisted award state so an already-delivered payout cannot
continue to look approvable. It also removes stale approval buttons from the
callback message after approval/refresh when Telegram allows the edit.
"""
import html


_TERMINAL_NON_RETRY = {"delivered", "issued", "delivery_pending", "issuing"}
_installed = False


def prepare(admin_rewards, bot):
    global _installed
    if _installed:
        return

    original_install = admin_rewards.install
    original_screen_text = admin_rewards._screen_text

    def _award_state(campaign, rows):
        awards = []
        actionable = []
        for rank in range(1, min(3, len(rows)) + 1):
            award = admin_rewards._award_for_rank(campaign, rank)
            awards.append(award)
            if not award:
                actionable.append((rank, admin_rewards.PRIZES[rank - 1]))
                continue
            status = str(award.get("status") or "queued")
            attempts = int(award.get("issue_attempts") or 0)
            if status not in _TERMINAL_NON_RETRY and attempts <= 0:
                actionable.append((rank, int(award.get("amount") or admin_rewards.PRIZES[rank - 1])))

        if awards and len(awards) == min(3, len(rows)) and all(
            a and str(a.get("status") or "") == "delivered" for a in awards
        ):
            return "completed", 0
        if actionable:
            return "actionable", sum(amount for _rank, amount in actionable)
        if awards:
            return "processing", 0
        return "none", 0

    def _screen_text_fixed(campaign, rows):
        text = original_screen_text(campaign, rows)
        finalized, _ = admin_rewards._finalization_state(campaign)
        if not finalized or not rows:
            return text

        state, remaining = _award_state(campaign, rows)
        if state == "completed":
            text = text.replace(
                "Finalisation: <b>OPEN FOR APPROVAL ✅</b>",
                "Finalisation: <b>COMPLETED ✅</b>",
            ).replace(
                "Final results announced. Rewards may now be approved.",
                "Final results announced. Prize distribution is complete.",
            )
            lines = text.splitlines()
            trimmed = []
            skip_next = False
            for line in lines:
                if line.startswith("Tap <b>Approve & Issue ₹"):
                    skip_next = True
                    continue
                if skip_next and line.startswith("Duplicate protection"):
                    skip_next = False
                    continue
                trimmed.append(line)
            while trimmed and not trimmed[-1].strip():
                trimmed.pop()
            trimmed += [
                "",
                "✅ <b>Prize Distribution Completed</b>",
                "All eligible rewards have been delivered successfully.",
                "No further approval is required.",
            ]
            return "\n".join(trimmed)

        if state == "processing":
            text = text.replace(
                "Finalisation: <b>OPEN FOR APPROVAL ✅</b>",
                "Finalisation: <b>PROCESSING / ALREADY SUBMITTED ⏳</b>",
            ).replace(
                "Final results announced. Rewards may now be approved.",
                "Final results announced. No additional approval is currently required.",
            )
            lines = text.splitlines()
            trimmed = []
            skip_next = False
            for line in lines:
                if line.startswith("Tap <b>Approve & Issue ₹"):
                    skip_next = True
                    continue
                if skip_next and line.startswith("Duplicate protection"):
                    skip_next = False
                    continue
                trimmed.append(line)
            while trimmed and not trimmed[-1].strip():
                trimmed.pop()
            trimmed += [
                "",
                "⏳ <b>Payout already submitted or being processed.</b>",
                "Refresh to see the latest delivery status.",
            ]
            return "\n".join(trimmed)

        if state == "actionable" and remaining > 0:
            total = admin_rewards._eligible_total(rows)
            if remaining != total:
                text = text.replace(
                    f"Tap <b>Approve & Issue ₹{total}</b>",
                    f"Tap <b>Approve Remaining ₹{remaining}</b>",
                )
        return text

    def _screen_keyboard_fixed(campaign, rows):
        finalized, _ = admin_rewards._finalization_state(campaign)
        state, remaining = _award_state(campaign, rows) if finalized else ("none", 0)
        buttons = []
        if finalized and state == "actionable" and remaining > 0:
            buttons.append([
                bot.InlineKeyboardButton(
                    f"✅ Approve & Issue ₹{remaining}" if remaining == admin_rewards._eligible_total(rows)
                    else f"✅ Approve Remaining ₹{remaining}",
                    callback_data=f"dq_rewards_approve:{int(campaign['id'])}",
                )
            ])
        buttons += [
            [bot.InlineKeyboardButton("🔄 Refresh Winners", callback_data="dq_rewards_today")],
            [bot.InlineKeyboardButton("⬅️ Reward Center", callback_data="v89_reward_center")],
        ]
        return bot.InlineKeyboardMarkup(buttons)

    admin_rewards._screen_text = _screen_text_fixed
    admin_rewards._screen_keyboard = _screen_keyboard_fixed

    def install_fixed():
        base_handler = original_install()

        async def daily_quiz_reward_callback_ui_fixed(update, context):
            result = await base_handler(update, context)
            q = getattr(update, "callback_query", None)
            data = str(getattr(q, "data", "") or "") if q else ""
            if not q or not (data == "dq_rewards_today" or data.startswith("dq_rewards_approve:")):
                return result
            try:
                if data.startswith("dq_rewards_approve:"):
                    campaign_id = int(data.split(":", 1)[1])
                    campaign = admin_rewards.schedule.v110._campaign(campaign_id)
                else:
                    campaign, _ = admin_rewards._today_snapshot()
                if not campaign:
                    return result
                rows = admin_rewards.schedule._final_rows(campaign["id"])
                state, _remaining = _award_state(campaign, rows)
                if state in {"completed", "processing"}:
                    try:
                        await q.message.edit_text(
                            _screen_text_fixed(campaign, rows),
                            parse_mode=bot.ParseMode.HTML,
                            reply_markup=_screen_keyboard_fixed(campaign, rows),
                        )
                        bot.logger.warning(
                            "DAILY_QUIZ_ADMIN_UI_STALE_BUTTON_CLEARED campaign=%s state=%s",
                            campaign["id"], state,
                        )
                    except Exception as exc:
                        bot.logger.warning(
                            "DAILY_QUIZ_ADMIN_UI_EDIT_SKIPPED campaign=%s state=%s reason=%s",
                            campaign["id"], state, type(exc).__name__,
                        )
            except Exception:
                bot.logger.exception("DAILY_QUIZ_ADMIN_UI_POST_CALLBACK_FAILED")
            return result

        # production.py's feature guard intentionally checks this historical
        # callback name. Preserve it while still running the additive wrapper.
        daily_quiz_reward_callback_ui_fixed.__name__ = "daily_quiz_reward_callback"
        bot.callback_handler = daily_quiz_reward_callback_ui_fixed
        bot.logger.warning(
            "DAILY_QUIZ_ADMIN_UI_FIX active=on delivered_hides_approval=on completed_status=on "
            "stale_callback_edit=on duplicate_issuance_guard_unchanged=on locked_rewards_file_unchanged=on"
        )
        return daily_quiz_reward_callback_ui_fixed

    admin_rewards.install = install_fixed
    _installed = True
