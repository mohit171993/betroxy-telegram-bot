"""Additive admin diagnostics + safe recovery for failed Daily Quiz rewards.

This module does not modify the locked reward implementation. It wraps the
existing admin reward install hook so an admin can:
- see the exact persisted GiftPort/provider failure reason;
- reconcile a failed reward using the existing provider order id (read-only);
- only after a separate explicit admin action, retry the failed purchase using
  that same deterministic order id, with a status check immediately beforehand.

No reward is retried during startup/deploy.
"""
import html

_TERMINAL_SUCCESS = {"delivered", "issued", "delivery_pending", "issuing"}
_installed = False


def prepare(admin_rewards, bot):
    global _installed
    if _installed:
        return

    original_install = admin_rewards.install
    original_screen_text = admin_rewards._screen_text
    original_screen_keyboard = admin_rewards._screen_keyboard
    v97 = admin_rewards.v97

    def _provider_detail(award):
        if not award:
            return ""
        status = str(award.get("status") or "")
        if status not in {
            "provider_failed", "provider_unknown", "provider_hold",
            "balance_hold", "needs_config", "waiting_mobile",
        }:
            return ""
        detail = str(
            award.get("error_detail")
            or award.get("provider_message")
            or "No provider detail was stored."
        ).strip()
        order_id = str(award.get("provider_order_id") or "").strip()
        attempts = int(award.get("issue_attempts") or 0)
        lines = [
            f"    Provider detail: <code>{html.escape(detail[:700])}</code>",
            f"    Attempts: <b>{attempts}</b>",
        ]
        if order_id:
            lines.append(f"    Order: <code>{html.escape(order_id[:120])}</code>")
        return "\n".join(lines)

    def _screen_text_with_provider_detail(campaign, rows):
        text = original_screen_text(campaign, rows)
        finalized, _ = admin_rewards._finalization_state(campaign)
        if not finalized:
            return text

        details = []
        for rank in range(1, min(3, len(rows)) + 1):
            award = admin_rewards._award_for_rank(campaign, rank)
            extra = _provider_detail(award)
            if extra:
                details.append(f"<b>#{rank} provider diagnostic</b>\n{extra}")
        if details:
            text += (
                "\n\n🧾 <b>Provider Diagnostics</b>\n"
                + "\n\n".join(details)
                + "\n\nA failed reward is never repurchased automatically."
            )
        return text

    def _screen_keyboard_with_recovery(campaign, rows):
        base = original_screen_keyboard(campaign, rows)
        buttons = [list(r) for r in base.inline_keyboard]
        recovery = []
        finalized, _ = admin_rewards._finalization_state(campaign)
        if finalized:
            for rank in range(1, min(3, len(rows)) + 1):
                award = admin_rewards._award_for_rank(campaign, rank)
                if not award:
                    continue
                status = str(award.get("status") or "")
                amount = int(award.get("amount") or admin_rewards.PRIZES[rank - 1])
                if status == "provider_failed":
                    recovery.append([
                        bot.InlineKeyboardButton(
                            f"🔎 Check Failed #{rank} ₹{amount}",
                            callback_data=f"dq_reward_reconcile:{int(campaign['id'])}:{rank}",
                        )
                    ])
        if recovery:
            # Put diagnostics before Refresh/Reward Center.
            insert_at = max(0, len(buttons) - 2)
            for row in reversed(recovery):
                buttons.insert(insert_at, row)
        return bot.InlineKeyboardMarkup(buttons)

    admin_rewards._screen_text = _screen_text_with_provider_detail
    admin_rewards._screen_keyboard = _screen_keyboard_with_recovery

    async def _safe_answer(q, text=None, show_alert=False):
        try:
            await q.answer(text=text, show_alert=show_alert)
        except Exception as exc:
            bot.logger.warning(
                "DAILY_REWARD_PROVIDER_RECOVERY_CALLBACK_ACK_SKIPPED reason=%s",
                type(exc).__name__,
            )

    def _award_for(campaign_id, rank):
        campaign = admin_rewards.schedule.v110._campaign(int(campaign_id))
        if not campaign:
            return None, None
        award = admin_rewards._award_for_rank(campaign, int(rank))
        return campaign, award

    def _has_voucher(data):
        data = data or {}
        return bool(data.get("card_no") or data.get("redeem_code") or data.get("voucher_url"))

    def _store_status_success(award, data):
        # Reuse the existing provider adapter's storage and the already-installed
        # reward-code display/delivery patch.
        v97._store_provider_success(award, data)
        delivered = bool(v97._deliver_award(int(award["id"])))
        fresh = admin_rewards._award_for_rank(
            admin_rewards.schedule.v110._campaign(int(award["campaign_id"]))
            if award.get("campaign_id") else {},
            int(award.get("rank") or 0),
        ) if False else None
        return delivered

    def _status_check(award):
        order_id = str(award.get("provider_order_id") or "").strip()
        if not order_id:
            return False, {
                "status": "failure",
                "message": "Missing provider order id; retry blocked.",
                "local_block": True,
            }
        return v97._giftport_post("status", {"order_id": order_id})

    def _retry_allowed_from_status(ok, data):
        if data.get("network_error"):
            return False
        if ok and not _has_voucher(data):
            return False
        if ok and _has_voucher(data):
            return False

        # GiftPort's status endpoint sometimes returns only {"status":"failed"}
        # with no separate message. Treat an explicit terminal provider status as
        # authoritative; previously we checked only message text, which wrongly
        # blocked a confirmed failed order from reaching the admin retry step.
        provider_status = str(data.get("status") or "").strip().lower()
        terminal_statuses = {
            "failed", "failure", "rejected", "declined",
            "not_found", "not found", "cancelled", "canceled",
        }
        if provider_status in terminal_statuses:
            return True

        message = str(data.get("message") or "").lower()
        terminal_words = (
            "not found", "no order", "failed", "failure", "rejected",
            "declined", "invalid order", "does not exist",
        )
        return any(word in message for word in terminal_words)

    def install_with_recovery():
        base_handler = original_install()

        async def daily_quiz_reward_callback(update, context):
            q = getattr(update, "callback_query", None)
            data = str(getattr(q, "data", "") or "") if q else ""
            if not q or not (
                data.startswith("dq_reward_reconcile:")
                or data.startswith("dq_reward_retry:")
            ):
                return await base_handler(update, context)

            if not bot.is_admin(q.from_user.id):
                await _safe_answer(q, "Admin only", show_alert=True)
                return

            parts = data.split(":")
            if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
                await _safe_answer(q, "Invalid recovery request", show_alert=True)
                return

            campaign_id = int(parts[1])
            rank = int(parts[2])
            campaign, award = _award_for(campaign_id, rank)
            if not campaign or not award:
                await _safe_answer(q, "Award not found", show_alert=True)
                return

            status = str(award.get("status") or "")
            amount = int(award.get("amount") or 0)
            order_id = str(award.get("provider_order_id") or "").strip()
            if status in _TERMINAL_SUCCESS:
                await _safe_answer(q, "This reward is already issued/delivered.", show_alert=True)
                return
            if status != "provider_failed":
                await _safe_answer(
                    q,
                    f"Recovery is only available for Provider Failed rewards. Current: {status}",
                    show_alert=True,
                )
                return

            await _safe_answer(q, "Checking GiftPort order status…")
            ok, provider_data = _status_check(award)
            provider_message = str(
                provider_data.get("message")
                or provider_data.get("status")
                or "No provider message"
            ).strip()

            if ok and _has_voucher(provider_data):
                v97._store_provider_success(award, provider_data)
                delivered = bool(v97._deliver_award(int(award["id"])))
                await q.message.reply_text(
                    "✅ <b>Provider reconciliation found the voucher.</b>\n\n"
                    f"Rank: <b>#{rank}</b> · Amount: <b>₹{amount}</b>\n"
                    f"Order: <code>{html.escape(order_id)}</code>\n"
                    f"Delivery: <b>{'Delivered ✅' if delivered else 'Issued; delivery pending'}</b>\n\n"
                    "No second purchase was made.",
                    parse_mode=bot.ParseMode.HTML,
                )
                bot.logger.warning(
                    "DAILY_REWARD_PROVIDER_RECONCILED admin=%s campaign=%s rank=%s award=%s amount=%s order=%s delivered=%s repurchase=off",
                    q.from_user.id, campaign_id, rank, award["id"], amount, order_id, delivered,
                )
                return

            if provider_data.get("network_error"):
                await q.message.reply_text(
                    "⚠️ <b>Provider status could not be confirmed.</b>\n\n"
                    f"GiftPort: <code>{html.escape(provider_message[:700])}</code>\n\n"
                    "For safety, retry is blocked while provider status is unknown.",
                    parse_mode=bot.ParseMode.HTML,
                )
                return

            if ok and not _has_voucher(provider_data):
                await q.message.reply_text(
                    "⏳ <b>Provider responded, but no voucher is available yet.</b>\n\n"
                    f"GiftPort: <code>{html.escape(provider_message[:700])}</code>\n\n"
                    "No purchase was retried. Check again later.",
                    parse_mode=bot.ParseMode.HTML,
                )
                return

            retry_allowed = _retry_allowed_from_status(ok, provider_data)
            kb = None
            if retry_allowed:
                kb = bot.InlineKeyboardMarkup([[
                    bot.InlineKeyboardButton(
                        f"♻️ Retry #{rank} ₹{amount} — Same Order ID",
                        callback_data=f"dq_reward_retry:{campaign_id}:{rank}",
                    )
                ]])
            await q.message.reply_text(
                "❌ <b>GiftPort confirms this order is not successful.</b>\n\n"
                f"Rank: <b>#{rank}</b> · Amount: <b>₹{amount}</b>\n"
                f"Order: <code>{html.escape(order_id)}</code>\n"
                f"GiftPort: <code>{html.escape(provider_message[:700])}</code>\n\n"
                + (
                    "You may explicitly retry using the <b>same deterministic order ID</b>. "
                    "The bot will check status again immediately before purchase."
                    if retry_allowed
                    else
                    "Retry remains blocked because the provider response is not clearly terminal."
                ),
                parse_mode=bot.ParseMode.HTML,
                reply_markup=kb,
            )
            if data.startswith("dq_reward_reconcile:"):
                bot.logger.warning(
                    "DAILY_REWARD_PROVIDER_CHECK admin=%s campaign=%s rank=%s award=%s amount=%s order=%s provider_ok=%s retry_allowed=%s message=%s",
                    q.from_user.id, campaign_id, rank, award["id"], amount, order_id,
                    ok, retry_allowed, provider_message[:300],
                )
                return

            # dq_reward_retry: a second explicit admin action.
            # Re-check status immediately before any purchase. The block above has
            # already performed that check during this callback.
            if not retry_allowed:
                return

            mobile = v97._giftport_mobile(int(award["telegram_user_id"]))
            if not mobile:
                v97._award_update(
                    award["id"], "waiting_mobile",
                    error_detail="User mobile not available for provider retry",
                )
                await q.message.reply_text(
                    "📱 Retry stopped: winner mobile is not currently available.",
                    parse_mode=bot.ParseMode.HTML,
                )
                return

            # Same provider order ID: never create a new independent purchase key.
            v97._mark_issue_attempt(int(award["id"]), order_id)
            buy_ok, buy_data = v97._giftport_post(
                "buy",
                {
                    "order_id": order_id,
                    "operator_code": str(award.get("brand_code") or "GPAPGV").upper(),
                    "amount": amount,
                    "mobile": mobile,
                    "recipient_name": v97._recipient_name(int(award["telegram_user_id"])),
                },
            )
            if buy_ok and _has_voucher(buy_data):
                fresh_award = dict(award)
                fresh_award["provider_order_id"] = order_id
                v97._store_provider_success(fresh_award, buy_data)
                delivered = bool(v97._deliver_award(int(award["id"])))
                await q.message.reply_text(
                    "✅ <b>Failed reward recovered.</b>\n\n"
                    f"Rank: <b>#{rank}</b> · Amount: <b>₹{amount}</b>\n"
                    f"Order: <code>{html.escape(order_id)}</code>\n"
                    f"Delivery: <b>{'Delivered ✅' if delivered else 'Issued; delivery pending'}</b>",
                    parse_mode=bot.ParseMode.HTML,
                )
                bot.logger.warning(
                    "DAILY_REWARD_PROVIDER_RETRY_SUCCESS admin=%s campaign=%s rank=%s award=%s amount=%s order=%s delivered=%s",
                    q.from_user.id, campaign_id, rank, award["id"], amount, order_id, delivered,
                )
                return

            retry_message = str(buy_data.get("message") or "GiftPort purchase failed")
            if buy_data.get("network_error"):
                new_status = "provider_unknown"
            elif "insufficient" in retry_message.lower() and "balance" in retry_message.lower():
                new_status = "balance_hold"
            else:
                new_status = "provider_failed"
            v97._award_update(
                award["id"], new_status,
                provider_order_id=order_id,
                error_detail=retry_message[:1000],
            )
            await q.message.reply_text(
                "⚠️ <b>Retry did not complete.</b>\n\n"
                f"Current state: <b>{html.escape(new_status.replace('_', ' ').title())}</b>\n"
                f"GiftPort: <code>{html.escape(retry_message[:700])}</code>\n\n"
                "No further automatic purchase will be attempted.",
                parse_mode=bot.ParseMode.HTML,
            )
            bot.logger.warning(
                "DAILY_REWARD_PROVIDER_RETRY_FAILED admin=%s campaign=%s rank=%s award=%s amount=%s order=%s state=%s message=%s",
                q.from_user.id, campaign_id, rank, award["id"], amount, order_id,
                new_status, retry_message[:300],
            )

        daily_quiz_reward_callback.__name__ = "daily_quiz_reward_callback"
        bot.callback_handler = daily_quiz_reward_callback
        bot.logger.warning(
            "DAILY_REWARD_PROVIDER_RECOVERY active=on exact_failure_reason=on "
            "provider_status_reconcile=on retry=two_step_admin_only same_order_id=on "
            "startup_auto_retry=off locked_rewards_unchanged=on"
        )
        return daily_quiz_reward_callback

    admin_rewards.install = install_with_recovery
    _installed = True
