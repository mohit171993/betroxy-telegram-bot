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

    def _split_rows(award_id):
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM reward_award_parts WHERE award_id=%s ORDER BY part_no",
                    (int(award_id),),
                )
                return cur.fetchall()

    def _ensure_fallback_parts(award, operator_code, plan):
        award_id = int(award["id"])
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                for part_no, part_amount in enumerate(plan, 1):
                    order_id = f"BTRX_{award_id}_P{part_no}"
                    cur.execute(
                        """
                        INSERT INTO reward_award_parts(
                            award_id,part_no,amount,operator_code,provider_order_id,status
                        ) VALUES (%s,%s,%s,%s,%s,'queued')
                        ON CONFLICT(award_id,part_no) DO NOTHING
                        """,
                        (award_id, part_no, int(part_amount), str(operator_code), order_id),
                    )
            conn.commit()
        rows = _split_rows(award_id)
        if [int(r.get("amount") or 0) for r in rows] != [int(x) for x in plan]:
            return None
        return rows

    def _part_update(part_id, status, **fields):
        allowed = {
            "provider_transaction_id", "voucher_code", "card_no", "voucher_pin",
            "voucher_url", "provider_message", "error_detail",
        }
        sets = ["status=%s", "updated_at=NOW()"]
        params = [str(status)]
        for key, value in fields.items():
            if key in allowed:
                sets.append(f"{key}=%s")
                params.append(value)
        if status in {"issued", "delivered"}:
            sets.append("issued_at=COALESCE(issued_at,NOW())")
        if status == "delivered":
            sets.append("delivered_at=COALESCE(delivered_at,NOW())")
        params.append(int(part_id))
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE reward_award_parts SET {', '.join(sets)} WHERE id=%s",
                    tuple(params),
                )
            conn.commit()

    def _mark_part_attempt(part):
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE reward_award_parts
                    SET status='issuing', issue_attempts=issue_attempts+1,
                        last_issue_attempt_at=NOW(), error_detail=NULL, updated_at=NOW()
                    WHERE id=%s
                    """,
                    (int(part["id"]),),
                )
            conn.commit()

    def _store_part_success(part, provider_data):
        _part_update(
            part["id"],
            "issued",
            provider_transaction_id=str(provider_data.get("transaction_id") or ""),
            voucher_code=str(provider_data.get("redeem_code") or ""),
            card_no=str(provider_data.get("card_no") or ""),
            voucher_pin=str(provider_data.get("voucher_pin") or ""),
            voucher_url=str(provider_data.get("voucher_url") or ""),
            provider_message=str(provider_data.get("message") or ""),
            error_detail=None,
        )

    def _issue_smaller_denomination_fallback(award, plan):
        amount = int(award.get("amount") or 0)
        if sum(plan) != amount:
            return False, "Fallback denomination plan does not match the prize amount."

        operator_code = str(award.get("brand_code") or "GPAPGV").upper()
        product = v97._catalogue_row(operator_code) or {}
        denoms = {int(float(x)) for x in v97._parse_denominations(product.get("denominations")) if float(x).is_integer()}
        if not all(int(x) in denoms for x in plan):
            return False, f"Required denominations {plan} are not available in the current GiftPort catalogue."

        mobile = v97._giftport_mobile(int(award["telegram_user_id"]))
        if not mobile:
            v97._award_update(
                award["id"], "waiting_mobile",
                error_detail="User mobile not available for denomination fallback",
            )
            return False, "Winner mobile is not available."

        parts = _ensure_fallback_parts(award, operator_code, plan)
        if not parts:
            v97._award_update(
                award["id"], "needs_config",
                error_detail="Stored fallback split plan does not match ₹200+₹200+₹100",
            )
            return False, "Stored fallback split plan does not match."

        already_issued = sum(
            int(p.get("amount") or 0)
            for p in parts
            if str(p.get("status") or "") in {"issued", "delivered"}
        )
        remaining = max(0, amount - already_issued)

        if remaining:
            bal_ok, balance, currency = v97._get_balance()
            if not bal_ok:
                v97._award_update(
                    award["id"], "provider_hold",
                    error_detail=str(currency or "Could not verify GiftPort balance"),
                )
                return False, str(currency or "Could not verify GiftPort balance")
            settings = v97.v89._reward_settings()
            reserve = int(settings.get("min_provider_balance") or 0)
            if str(currency or "INR").upper() != "INR":
                v97._award_update(
                    award["id"], "provider_hold",
                    error_detail=f"GiftPort wallet currency is {currency}, expected INR",
                )
                return False, f"GiftPort wallet currency is {currency}, expected INR"
            if float(balance) - remaining < reserve:
                msg = f"GiftPort balance ₹{balance:,.2f}; reserve ₹{reserve:,}"
                v97._award_update(award["id"], "balance_hold", error_detail=msg)
                return False, msg

        v97._award_update(
            award["id"], "issuing",
            provider_order_id=f"SPLIT:{int(award['id'])}",
            error_detail=None,
        )
        recipient_name = v97._recipient_name(int(award["telegram_user_id"]))

        for part in _split_rows(award["id"]):
            status = str(part.get("status") or "queued")
            if status in {"issued", "delivered"}:
                continue

            # If a prior child attempt exists, never repurchase it blindly.
            if int(part.get("issue_attempts") or 0) > 0:
                child_order = str(part.get("provider_order_id") or "")
                ok, status_data = v97._giftport_post("status", {"order_id": child_order})
                if ok and _has_voucher(status_data):
                    _store_part_success(part, status_data)
                    continue
                provider_status = str(status_data.get("status") or "").lower()
                provider_message = str(status_data.get("message") or "").lower()
                terminal = provider_status in {"failed","failure","rejected","declined","not_found","not found","cancelled","canceled"} or any(
                    x in provider_message for x in ("failed","failure","rejected","declined","not found","no order","does not exist")
                )
                if not terminal:
                    v97._award_update(
                        award["id"], "provider_unknown",
                        error_detail=f"Voucher part {part.get('part_no')} status is not safely retryable",
                    )
                    return False, f"Voucher part {part.get('part_no')} status is not safely retryable."
                # Terminal child failures require another explicit admin recovery,
                # not an automatic repeated purchase in this same action.
                v97._award_update(
                    award["id"], "provider_failed",
                    error_detail=f"Voucher part {part.get('part_no')} previously failed; manual review required",
                )
                return False, f"Voucher part {part.get('part_no')} previously failed; manual review required."

            _mark_part_attempt(part)
            ok, buy_data = v97._giftport_post(
                "buy",
                {
                    "order_id": str(part["provider_order_id"]),
                    "operator_code": operator_code,
                    "amount": int(part["amount"]),
                    "mobile": mobile,
                    "recipient_name": recipient_name,
                },
            )
            if ok and _has_voucher(buy_data):
                _store_part_success(part, buy_data)
                bot.logger.warning(
                    "DAILY_REWARD_SMALL_DENOM_PART_ISSUED award=%s part=%s amount=%s order=%s",
                    award["id"], part["part_no"], part["amount"], part["provider_order_id"],
                )
                continue

            message = str(buy_data.get("message") or buy_data.get("status") or "GiftPort split purchase failed")
            if buy_data.get("network_error"):
                child_status = parent_status = "provider_unknown"
            elif "insufficient" in message.lower() and "balance" in message.lower():
                child_status = parent_status = "balance_hold"
            else:
                child_status = parent_status = "provider_failed"
            _part_update(part["id"], child_status, error_detail=message[:1000])
            v97._award_update(
                award["id"], parent_status,
                provider_order_id=f"SPLIT:{int(award['id'])}",
                error_detail=f"Voucher part {part.get('part_no')}: {message}"[:1000],
            )
            return False, f"Voucher part {part.get('part_no')}: {message}"

        fresh = _split_rows(award["id"])
        if not fresh or not all(str(p.get("status") or "") in {"issued", "delivered"} for p in fresh):
            v97._award_update(
                award["id"], "provider_unknown",
                error_detail="Smaller-denomination voucher issuance incomplete",
            )
            return False, "Smaller-denomination voucher issuance incomplete."

        v97._award_update(
            award["id"], "issued",
            provider_order_id=f"SPLIT:{int(award['id'])}",
            provider_message="Fallback denominations: 200+200+100",
            error_detail=None,
        )
        delivered = bool(v97._deliver_award(int(award["id"])))
        return delivered, "Delivered" if delivered else "Issued; Telegram delivery pending"

    def install_with_recovery():
        base_handler = original_install()

        async def daily_quiz_reward_callback(update, context):
            q = getattr(update, "callback_query", None)
            data = str(getattr(q, "data", "") or "") if q else ""
            if not q or not (
                data.startswith("dq_reward_reconcile:")
                or data.startswith("dq_reward_retry:")
                or data.startswith("dq_reward_split:")
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
            stored_failure = str(award.get("error_detail") or award.get("provider_message") or "").lower()
            denomination_fallback = (
                amount == 500
                and "other denomination" in stored_failure
            )
            kb = None
            if retry_allowed and denomination_fallback:
                kb = bot.InlineKeyboardMarkup([[
                    bot.InlineKeyboardButton(
                        "🧩 Issue ₹500 as ₹200 + ₹200 + ₹100",
                        callback_data=f"dq_reward_split:{campaign_id}:{rank}",
                    )
                ]])
            elif retry_allowed:
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
                    (
                        "The ₹500 denomination was rejected. You may explicitly issue the same ₹500 prize "
                        "as <b>₹200 + ₹200 + ₹100</b>. Each child voucher uses its own deterministic order ID."
                        if denomination_fallback
                        else
                        "You may explicitly retry using the <b>same deterministic order ID</b>. "
                        "The bot will check status again immediately before purchase."
                    )
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

            if data.startswith("dq_reward_split:"):
                if not retry_allowed or not denomination_fallback:
                    await q.message.reply_text(
                        "⚠️ Smaller-denomination fallback is not currently authorized for this reward.",
                        parse_mode=bot.ParseMode.HTML,
                    )
                    return
                success, detail = _issue_smaller_denomination_fallback(award, [200, 200, 100])
                await q.message.reply_text(
                    (
                        "✅ <b>₹500 reward recovered using smaller denominations.</b>\n\n"
                        "Issued as: <b>₹200 + ₹200 + ₹100</b>\n"
                        f"Result: <b>{html.escape(detail)}</b>"
                    )
                    if success
                    else
                    (
                        "⚠️ <b>Smaller-denomination recovery did not fully complete.</b>\n\n"
                        f"Detail: <code>{html.escape(str(detail)[:700])}</code>\n\n"
                        "Already-issued child vouchers, if any, remain protected and will not be repurchased automatically."
                    ),
                    parse_mode=bot.ParseMode.HTML,
                )
                bot.logger.warning(
                    "DAILY_REWARD_SMALL_DENOM_RECOVERY admin=%s campaign=%s rank=%s award=%s amount=%s plan=200+200+100 success=%s detail=%s",
                    q.from_user.id, campaign_id, rank, award["id"], amount, success, str(detail)[:300],
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
            "smaller_denom_fallback=500_to_200+200+100 explicit_admin_only=on "
            "startup_auto_retry=off locked_rewards_unchanged=on"
        )
        return daily_quiz_reward_callback

    admin_rewards.install = install_with_recovery
    _installed = True
