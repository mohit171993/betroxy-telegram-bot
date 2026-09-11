"""Admin-only approval flow for today's BETROXY Daily Quiz rewards.

This adds a visible Daily Quiz Rewards entry inside the existing Reward Center.
Rewards are strictly locked until the daily quiz has closed and the 21:05 IST
final-result step has completed. Nothing is issued from simply opening the screen:
the admin must explicitly approve the eligible payout after finalisation.

Eligible payout is dynamic:
- 0 winners -> no payout required
- 1 winner  -> ₹500
- 2 winners -> ₹800
- 3 winners -> ₹1,000

After the public final-result announcement, the primary admin receives one
private Telegram approval alert. Duplicate protection prevents repeat alerts.
"""
import html
from datetime import datetime, timezone

import bot
import daily_quiz_schedule as schedule

v97 = schedule.v97
v89 = v97.v89

_previous_callback = None
_original_reward_center_keyboard = v97._reward_center_keyboard
_original_announce_if_due = None

PRIZES = (500, 300, 200)
MEDALS = ("🥇", "🥈", "🥉")


def _today_snapshot():
    campaign = schedule._today_campaign_windowed(test_mode=False)
    rows = schedule._final_rows(campaign["id"])
    return campaign, rows


def _period_key(campaign):
    return f"daily_quiz:{campaign['campaign_date']}"


def _eligible_total(rows):
    return sum(PRIZES[: min(3, len(rows))])


def _finalization_state(campaign):
    """Return whether rewards are allowed to be approved for this campaign.

    Hard gate 1: current time must be at/after today's 21:05 IST result time.
    Hard gate 2: when public result announcements are enabled, the final result
    must actually have been marked delivered before approval is exposed.
    """
    _, result_utc = schedule._day_bounds(campaign.get("campaign_date"))
    now_utc = datetime.now(timezone.utc)
    time_ready = now_utc >= result_utc
    announced = schedule._result_already_sent(campaign["id"])
    if not time_ready:
        return False, "Results are provisional until 21:05 IST."
    if schedule.RESULT_CHANNEL_ENABLED and not announced:
        return False, "Waiting for the public final-result announcement to complete."
    return True, "Final results announced. Rewards may now be approved."


def _award_for_rank(campaign, rank):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM reward_awards WHERE reward_type='daily_quiz' AND period_key=%s AND rank=%s LIMIT 1",
                (_period_key(campaign), int(rank)),
            )
            return cur.fetchone()


def _stage_today(campaign, rows):
    ready, _ = _finalization_state(campaign)
    if not ready:
        return []
    staged = []
    for rank, amount in ((1, 500), (2, 300), (3, 200)):
        if len(rows) < rank:
            continue
        award = schedule._ensure_award(campaign, rows[rank - 1], rank, amount)
        if award:
            staged.append(award)
    return staged


def _winner_name(row):
    return str(row.get("telegram_username") or f"Player {str(row.get('telegram_user_id') or '')[-4:]}")


def _status_label(award, finalized=True):
    if not finalized:
        return "Provisional — locked until final result"
    if not award:
        return "Not staged"
    status = str(award.get("status") or "queued")
    return {
        "queued": "Ready for approval",
        "waiting_mobile": "Waiting for mobile",
        "budget_hold": "Budget hold",
        "balance_hold": "Balance hold",
        "provider_hold": "Provider hold",
        "needs_config": "Needs configuration",
        "provider_unknown": "Provider status unknown",
        "provider_failed": "Provider failed",
        "issuing": "Issuing",
        "issued": "Issued",
        "delivery_pending": "Delivery pending",
        "delivered": "Delivered ✅",
    }.get(status, status.replace("_", " ").title())


def _screen_text(campaign, rows):
    finalized, gate_message = _finalization_state(campaign)
    total = _eligible_total(rows)
    lines = [
        "🏆 <b>DAILY QUIZ REWARDS</b>",
        "",
        f"Date: <b>{html.escape(str(campaign.get('campaign_date')))}</b>",
        "Maximum daily prize pool: <b>₹1,000</b>",
        f"Eligible payout today: <b>₹{total}</b>",
        f"Finalisation: <b>{'OPEN FOR APPROVAL ✅' if finalized else 'LOCKED 🔒'}</b>",
        f"{html.escape(gate_message)}",
        "",
    ]
    for i in range(3):
        rank = i + 1
        if len(rows) >= rank:
            row = rows[i]
            award = _award_for_rank(campaign, rank) if finalized else None
            lines.append(
                f"{MEDALS[i]} <b>#{rank} {html.escape(_winner_name(row))}</b> — "
                f"{int(row.get('correct_count') or 0)}/7 — <b>₹{PRIZES[i]}</b>\n"
                f"    Status: <b>{html.escape(_status_label(award, finalized=finalized))}</b>"
            )
        else:
            lines.append(f"{MEDALS[i]} <b>#{rank}</b> — No eligible winner — ₹{PRIZES[i]} not issued")
    lines += [""]
    if finalized and total > 0:
        lines += [
            f"Tap <b>Approve & Issue ₹{total}</b> after checking the final eligible winner{'s' if len(rows) != 1 else ''}.",
            "Duplicate protection and existing GiftPort balance/mobile checks remain active.",
        ]
    elif finalized:
        lines += [
            "No eligible winners qualified today, so no reward payout is required.",
            "No approval action is needed.",
        ]
    else:
        lines += [
            "The leaderboard shown here is provisional. Rankings can still change before close.",
            "The approval button will appear only after the 21:05 IST final result is announced.",
        ]
    return "\n".join(lines)


def _screen_keyboard(campaign, rows):
    finalized, _ = _finalization_state(campaign)
    total = _eligible_total(rows)
    ready = finalized and total > 0
    buttons = []
    if ready:
        buttons.append([
            bot.InlineKeyboardButton(
                f"✅ Approve & Issue ₹{total}",
                callback_data=f"dq_rewards_approve:{int(campaign['id'])}",
            )
        ])
    buttons += [
        [bot.InlineKeyboardButton("🔄 Refresh Winners", callback_data="dq_rewards_today")],
        [bot.InlineKeyboardButton("⬅️ Reward Center", callback_data="v89_reward_center")],
    ]
    return bot.InlineKeyboardMarkup(buttons)


def _patched_reward_center_keyboard():
    original = _original_reward_center_keyboard()
    rows = [list(r) for r in original.inline_keyboard]
    insert_at = 0
    for i, row in enumerate(rows):
        if any("Reward Program" in str(getattr(b, "text", "")) for b in row):
            insert_at = i
            break
    rows.insert(insert_at, [bot.InlineKeyboardButton("🏆 Daily Quiz Rewards", callback_data="dq_rewards_today")])
    return bot.InlineKeyboardMarkup(rows)


def _issue_approved_awards(campaign, rows):
    finalized, reason = _finalization_state(campaign)
    if not finalized:
        raise RuntimeError(reason)
    awards = _stage_today(campaign, rows)
    results = []
    for award in awards:
        rank = int(award.get("rank") or 0)
        amount = int(award.get("amount") or 0)
        status = str(award.get("status") or "queued")
        attempts = int(award.get("issue_attempts") or 0)
        if status in {"delivered", "issued", "delivery_pending", "issuing"} or attempts > 0:
            results.append((rank, amount, status, False))
            continue
        try:
            ok = bool(v97._issue_award(award))
            fresh = _award_for_rank(campaign, rank) or award
            results.append((rank, amount, str(fresh.get("status") or status), ok))
        except Exception:
            bot.logger.exception("DAILY_QUIZ_ADMIN_ISSUE_FAILED rank=%s award=%s", rank, award.get("id"))
            fresh = _award_for_rank(campaign, rank) or award
            results.append((rank, amount, str(fresh.get("status") or "error"), False))
    return results


def _admin_alert_rows(campaign, rows):
    total = _eligible_total(rows)
    buttons = []
    if total > 0:
        buttons.append([{
            "text": f"✅ Approve & Issue ₹{total}",
            "callback_data": f"dq_rewards_approve:{int(campaign['id'])}",
        }])
    buttons.append([{"text": "🔍 Review Winners", "callback_data": "dq_rewards_today"}])
    return buttons


def _admin_alert_text(campaign, rows):
    total = _eligible_total(rows)
    lines = [
        "🏆 <b>DAILY QUIZ REWARDS — FINAL</b>",
        "",
        f"Date: <b>{html.escape(str(campaign.get('campaign_date')))}</b>",
    ]
    if rows:
        lines += [
            f"Eligible winners: <b>{min(3, len(rows))}</b>",
            f"Total payout awaiting approval: <b>₹{total}</b>",
            "",
        ]
        for i, row in enumerate(rows[:3]):
            lines.append(
                f"{MEDALS[i]} <b>{html.escape(_winner_name(row))}</b> — "
                f"{int(row.get('correct_count') or 0)}/7 — <b>₹{PRIZES[i]}</b>"
            )
        lines += ["", "Tap below to review and approve the eligible payout."]
    else:
        lines += [
            "",
            "No eligible winners qualified today.",
            "Payout required: <b>₹0</b>",
            "No approval action is needed.",
        ]
    return "\n".join(lines)


def _send_admin_ready_alert(campaign, rows):
    ready, _ = _finalization_state(campaign)
    if not ready:
        return False
    admin_id = int(bot.ADMIN_ID)
    delivery_type = "daily_quiz_admin_reward_ready"
    if schedule.v110._delivery_exists(campaign["id"], str(admin_id), delivery_type):
        return True
    try:
        ok, data = schedule._send_text(
            admin_id,
            _admin_alert_text(campaign, rows),
            _admin_alert_rows(campaign, rows),
        )
    except Exception:
        bot.logger.exception("DAILY_QUIZ_ADMIN_READY_ALERT_FAILED campaign=%s", campaign["id"])
        return False
    if ok:
        payload = data if isinstance(data, dict) else {}
        mid = ((payload.get("result") or {}).get("message_id"))
        schedule.v110._mark_delivery(campaign["id"], str(admin_id), delivery_type, mid)
    bot.logger.warning(
        "DAILY_QUIZ_ADMIN_READY_ALERT sent=%s campaign=%s winners=%s total=%s duplicate_guard=on",
        ok, campaign["id"], min(3, len(rows)), _eligible_total(rows),
    )
    return bool(ok)


def install():
    global _previous_callback, _original_announce_if_due
    _previous_callback = bot.callback_handler

    v97._reward_center_keyboard = _patched_reward_center_keyboard
    v89._reward_center_keyboard = _patched_reward_center_keyboard

    # Wrap the schedule worker's final-result check. The original function posts
    # the public result and marks it delivered; after that succeeds (or if it was
    # already delivered on a previous cycle), send one admin-only approval alert.
    _original_announce_if_due = schedule._announce_if_due

    def _announce_and_alert():
        result = _original_announce_if_due()
        try:
            campaign, rows = _today_snapshot()
            if _finalization_state(campaign)[0]:
                _stage_today(campaign, rows)
                _send_admin_ready_alert(campaign, rows)
        except Exception:
            bot.logger.exception("DAILY_QUIZ_ADMIN_POST_RESULT_ALERT_FAILED")
        return result

    schedule._announce_if_due = _announce_and_alert

    async def daily_quiz_reward_callback(update, context):
        q = getattr(update, "callback_query", None)
        data = str(getattr(q, "data", "") or "") if q else ""
        if not q or not data.startswith("dq_rewards_"):
            return await _previous_callback(update, context)
        if not bot.is_admin(q.from_user.id):
            await q.answer("Admin only", show_alert=True)
            return

        if data == "dq_rewards_today":
            await q.answer()
            campaign, rows = _today_snapshot()
            if _finalization_state(campaign)[0]:
                _stage_today(campaign, rows)
            await q.message.reply_text(
                _screen_text(campaign, rows),
                parse_mode=bot.ParseMode.HTML,
                reply_markup=_screen_keyboard(campaign, rows),
            )
            return

        if data.startswith("dq_rewards_approve:"):
            try:
                campaign_id = int(data.split(":", 1)[1])
            except Exception:
                await q.answer("Invalid campaign", show_alert=True)
                return
            campaign = schedule.v110._campaign(campaign_id)
            if not campaign:
                await q.answer("Campaign not found", show_alert=True)
                return
            finalized, reason = _finalization_state(campaign)
            if not finalized:
                await q.answer(reason, show_alert=True)
                return
            rows = schedule._final_rows(campaign_id)
            total = _eligible_total(rows)
            if total <= 0:
                await q.answer("No eligible winners. No payout is required.", show_alert=True)
                return
            await q.answer(f"Issuing approved rewards: ₹{total}…")
            results = _issue_approved_awards(campaign, rows)
            lines = [
                "✅ <b>DAILY QUIZ REWARD APPROVAL PROCESSED</b>",
                "",
                f"Approved total: <b>₹{total}</b>",
                "",
            ]
            for rank, amount, status, _ in sorted(results):
                lines.append(f"#{rank} ₹{amount}: <b>{html.escape(_status_label({'status': status}))}</b>")
            lines += ["", "The action is idempotent: already-issued awards are not purchased again."]
            await q.message.reply_text(
                "\n".join(lines),
                parse_mode=bot.ParseMode.HTML,
                reply_markup=_screen_keyboard(campaign, rows),
            )
            bot.logger.warning(
                "DAILY_QUIZ_ADMIN_APPROVAL admin=%s campaign=%s winners=%s total=%s results=%s",
                q.from_user.id, campaign_id, min(3, len(rows)), total,
                [(r, a, s) for r, a, s, _ in results],
            )
            return

        return await _previous_callback(update, context)

    bot.callback_handler = daily_quiz_reward_callback
    bot.logger.warning(
        "DAILY_QUIZ_ADMIN_REWARDS active=on admin_only=on manual_approval=on dynamic_payout=0/500/800/1000 "
        "admin_ready_alert=on duplicate_guard=on approval_gate=21:05_IST+final_result_announced"
    )
    return daily_quiz_reward_callback
