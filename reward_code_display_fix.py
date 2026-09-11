"""Correct reward-code presentation and denomination handling for GiftPort.

For Amazon Pay B2B (GPAPGV), GiftPort's `redeem_code` value is a provider/reference
number in our live response, while `card_no` is the customer-facing voucher/redeem
code. Never label the reference number as the redeem code.

GiftPort products can be variable-value or fixed-denomination. Variable products
accept the requested amount directly. For a fixed product, when the requested
reward is not one listed denomination but can be represented exactly by a small
combination of supported denominations, the award is fulfilled as multiple
provider orders with deterministic order IDs. Each part is persisted separately
before purchase so retries cannot silently repurchase an already-issued part.
"""
import html
from datetime import datetime, timezone

import bot


MAX_SPLIT_PARTS = 5


def install(v97, v89, v83):
    original_issue_award = v97._issue_award
    original_reconcile_unknown = v97._reconcile_unknown

    def _ensure_split_schema():
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS reward_award_parts (
                        id BIGSERIAL PRIMARY KEY,
                        award_id BIGINT NOT NULL,
                        part_no INTEGER NOT NULL,
                        amount INTEGER NOT NULL,
                        operator_code TEXT NOT NULL,
                        provider_order_id TEXT NOT NULL,
                        provider_transaction_id TEXT,
                        voucher_code TEXT,
                        card_no TEXT,
                        voucher_pin TEXT,
                        voucher_url TEXT,
                        provider_message TEXT,
                        status TEXT NOT NULL DEFAULT 'queued',
                        error_detail TEXT,
                        issue_attempts INTEGER NOT NULL DEFAULT 0,
                        last_issue_attempt_at TIMESTAMPTZ,
                        last_provider_check_at TIMESTAMPTZ,
                        issued_at TIMESTAMPTZ,
                        delivered_at TIMESTAMPTZ,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        UNIQUE(award_id, part_no),
                        UNIQUE(provider_order_id)
                    )
                    """
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_reward_award_parts_award ON reward_award_parts(award_id, part_no)"
                )
            conn.commit()

    _ensure_split_schema()

    # Preserve strict denomination protection for fixed-value products, but do
    # not reject a valid amount merely because a variable-value product's
    # catalogue also includes a denomination list.
    def _validate_reward_product(operator_code, amount):
        row = v97._catalogue_row(operator_code)
        if not row and v97._giftport_ready():
            v97._sync_catalogue()
            row = v97._catalogue_row(operator_code)
        if not row:
            return False, f"Giftport operator {operator_code} is not available in the synced catalogue", None

        denoms = v97._parse_denominations(row.get("denominations"))
        variable = bool(row.get("variable"))
        if (
            v97.GIFTPORT_STRICT_DENOMINATIONS
            and not variable
            and denoms
            and float(amount) not in {float(x) for x in denoms}
        ):
            options = ", ".join(
                f"₹{int(x):,}" if float(x).is_integer() else f"₹{x}"
                for x in sorted(denoms)
            )
            return False, (
                f"₹{amount:,} is not listed for {row.get('brand_name')}. "
                f"Available: {options}"
            ), row
        return True, "ok", row

    v97._validate_reward_product = _validate_reward_product

    def _fixed_integer_denoms(product):
        values = []
        for raw in v97._parse_denominations((product or {}).get("denominations")):
            try:
                value = float(raw)
            except Exception:
                continue
            if value > 0 and value.is_integer():
                values.append(int(value))
        return sorted(set(values), reverse=True)

    def _split_plan(product, amount):
        """Return the smallest exact fixed-denomination plan, or None."""
        try:
            target = int(amount)
        except Exception:
            return None
        if target <= 0 or bool((product or {}).get("variable")):
            return None
        denoms = [d for d in _fixed_integer_denoms(product) if d <= target]
        if not denoms or target in denoms:
            return None

        # Dynamic programming: minimize voucher count; for equal counts prefer
        # larger denominations first so ₹300 resolves to ₹200 + ₹100.
        best = {0: []}
        for subtotal in range(1, target + 1):
            candidate = None
            for d in denoms:
                prev = subtotal - d
                if prev < 0 or prev not in best:
                    continue
                plan = sorted(best[prev] + [d], reverse=True)
                if len(plan) > MAX_SPLIT_PARTS:
                    continue
                if candidate is None or len(plan) < len(candidate) or (
                    len(plan) == len(candidate) and tuple(plan) > tuple(candidate)
                ):
                    candidate = plan
            if candidate is not None:
                best[subtotal] = candidate
        plan = best.get(target)
        return plan if plan and len(plan) > 1 else None

    def _split_parts(award_id):
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM reward_award_parts WHERE award_id=%s ORDER BY part_no",
                    (int(award_id),),
                )
                return cur.fetchall()

    def _ensure_parts(award, operator_code, plan):
        award_id = int(award["id"])
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                for i, part_amount in enumerate(plan, 1):
                    order_id = f"BTRX_{award_id}_P{i}"
                    cur.execute(
                        """
                        INSERT INTO reward_award_parts(
                            award_id,part_no,amount,operator_code,provider_order_id,status
                        ) VALUES (%s,%s,%s,%s,%s,'queued')
                        ON CONFLICT(award_id,part_no) DO NOTHING
                        """,
                        (award_id, i, int(part_amount), str(operator_code), order_id),
                    )
            conn.commit()
        parts = _split_parts(award_id)
        if [int(p.get("amount") or 0) for p in parts] != [int(x) for x in plan]:
            return None
        return parts

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

    def _mark_part_provider_check(part_id):
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE reward_award_parts SET last_provider_check_at=NOW(), updated_at=NOW() WHERE id=%s",
                    (int(part_id),),
                )
            conn.commit()

    def _provider_payload_has_voucher(data):
        data = data or {}
        return bool(data.get("card_no") or data.get("redeem_code") or data.get("voucher_url"))

    def _store_part_success(part, data):
        _part_update(
            part["id"],
            "issued",
            provider_transaction_id=str(data.get("transaction_id") or ""),
            voucher_code=str(data.get("redeem_code") or ""),
            card_no=str(data.get("card_no") or ""),
            voucher_pin=str(data.get("voucher_pin") or ""),
            voucher_url=str(data.get("voucher_url") or ""),
            provider_message=str(data.get("message") or ""),
            error_detail=None,
        )

    def _reward_fields(row):
        brand = str(row.get("brand_code") or row.get("operator_code") or "").strip().upper()
        provider_ref = str(row.get("voucher_code") or "").strip()
        card_no = str(row.get("card_no") or "").strip()
        voucher_url = str(row.get("voucher_url") or "").strip()
        voucher_pin = str(row.get("voucher_pin") or "").strip()

        # Confirmed mapping for Amazon Pay Gift Voucher B2B on our GiftPort response:
        # redeem_code -> provider/reference number; card_no -> customer voucher code.
        if brand == "GPAPGV":
            return {
                "redeem_code": card_no,
                "reference": provider_ref,
                "voucher_url": voucher_url,
                "pin": voucher_pin,
            }

        return {
            "redeem_code": provider_ref,
            "reference": "",
            "voucher_url": voucher_url,
            "pin": voucher_pin,
        }

    def _single_delivery_text(award):
        fields = _reward_fields(award)
        if not fields["redeem_code"] and not fields["voucher_url"]:
            return None
        brand = v97._catalogue_row(award.get("brand_code")) or {}
        brand_name = str(brand.get("brand_name") or award.get("brand_code") or "Gift Voucher")
        lines = [
            "🎉 <b>BETROXY Reward Delivered</b>",
            "",
            f"🏆 Rank: <b>#{int(award.get('rank') or 0)}</b>",
            f"🎁 Reward: <b>{html.escape(brand_name)}</b>",
            f"💰 Value: <b>₹{int(award.get('amount') or 0):,}</b>",
            "",
        ]
        if fields["redeem_code"]:
            lines.append(f"🔐 Redeem Code: <code>{html.escape(fields['redeem_code'])}</code>")
        if fields["pin"]:
            lines.append(f"🔑 PIN: <code>{html.escape(fields['pin'])}</code>")
        if fields["voucher_url"]:
            lines.append(f"🔗 Claim: {html.escape(fields['voucher_url'])}")
        if fields["reference"]:
            lines.append(f"🧾 Reference: <code>{html.escape(fields['reference'])}</code>")
        lines += ["", "Keep this voucher private. You can also find it later under 🎁 My Rewards."]
        return "\n".join(lines)

    def _split_delivery_text(award, parts):
        if not parts:
            return None
        usable = []
        for part in parts:
            fields = _reward_fields(part)
            if not fields["redeem_code"] and not fields["voucher_url"]:
                return None
            usable.append((part, fields))

        brand = v97._catalogue_row(award.get("brand_code")) or {}
        brand_name = str(brand.get("brand_name") or award.get("brand_code") or "Gift Voucher")
        lines = [
            "🎉 <b>BETROXY Reward Delivered</b>",
            "",
            f"🏆 Rank: <b>#{int(award.get('rank') or 0)}</b>",
            f"🎁 Reward: <b>{html.escape(brand_name)}</b>",
            f"💰 Total Value: <b>₹{int(award.get('amount') or 0):,}</b>",
            f"🎫 Delivered as <b>{len(parts)} vouchers</b> because the provider uses fixed denominations.",
            "",
        ]
        for i, (part, fields) in enumerate(usable, 1):
            lines.append(f"<b>Voucher {i} — ₹{int(part.get('amount') or 0):,}</b>")
            if fields["redeem_code"]:
                lines.append(f"🔐 Redeem Code: <code>{html.escape(fields['redeem_code'])}</code>")
            if fields["pin"]:
                lines.append(f"🔑 PIN: <code>{html.escape(fields['pin'])}</code>")
            if fields["voucher_url"]:
                lines.append(f"🔗 Claim: {html.escape(fields['voucher_url'])}")
            if fields["reference"]:
                lines.append(f"🧾 Reference: <code>{html.escape(fields['reference'])}</code>")
            lines.append("")
        lines.append("Keep these vouchers private. You can also find them later under 🎁 My Rewards.")
        return "\n".join(lines)

    def _deliver_award(award_id):
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM reward_awards WHERE id=%s", (int(award_id),))
                award = cur.fetchone()
        if not award or award.get("status") == "delivered":
            return False

        parts = _split_parts(award_id)
        if parts:
            if not all(str(p.get("status") or "") in {"issued", "delivered"} for p in parts):
                return False
            text = _split_delivery_text(award, parts)
        else:
            text = _single_delivery_text(award)
        if not text:
            return False

        ok, data = v83._tg_send(
            int(award["telegram_user_id"]),
            text,
            [[{"text": "🎁 My Rewards", "url": f"https://t.me/{v83.OFFICIAL_BOT}?start=rewards"}]],
        )
        if ok:
            message_id = ((data.get("result") or {}).get("message_id")) if isinstance(data, dict) else None
            v97._award_update(award["id"], "delivered", delivered_message_id=message_id, error_detail=None)
            if parts:
                with bot.get_db() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE reward_award_parts
                            SET status='delivered', delivered_at=COALESCE(delivered_at,NOW()), updated_at=NOW()
                            WHERE award_id=%s AND status='issued'
                            """,
                            (int(award_id),),
                        )
                    conn.commit()
            bot.logger.warning(
                "REWARD_CODE_DISPLAY_FIX delivered award=%s uid=%s split_parts=%s",
                award["id"], award["telegram_user_id"], len(parts),
            )
            return True
        v97._award_update(
            award["id"],
            "delivery_pending",
            error_detail=str((data or {}).get("description") or "Telegram delivery failed")[:1000],
        )
        return False

    def _reconcile_one_part(part):
        if str(part.get("status") or "") != "provider_unknown":
            return str(part.get("status") or "") in {"issued", "delivered"}
        last = part.get("last_provider_check_at")
        if last:
            try:
                if (datetime.now(timezone.utc) - last).total_seconds() < 600:
                    return False
            except Exception:
                pass
        _mark_part_provider_check(part["id"])
        ok, data = v97._giftport_post("status", {"order_id": str(part.get("provider_order_id") or "")})
        if ok and _provider_payload_has_voucher(data):
            _store_part_success(part, data)
            return True
        _part_update(
            part["id"], "provider_unknown",
            error_detail=str(data.get("message") or "Order status not yet confirmed")[:1000],
        )
        return False

    def _reconcile_unknown(award):
        parts = _split_parts(award["id"])
        if not parts:
            return original_reconcile_unknown(award)
        for part in parts:
            if str(part.get("status") or "") == "provider_unknown":
                _reconcile_one_part(part)
        fresh = _split_parts(award["id"])
        if all(str(p.get("status") or "") in {"issued", "delivered"} for p in fresh):
            v97._award_update(award["id"], "issued", provider_order_id=f"SPLIT:{int(award['id'])}", error_detail=None)
            return _deliver_award(award["id"])
        if any(str(p.get("status") or "") == "provider_failed" for p in fresh):
            v97._award_update(award["id"], "provider_failed", error_detail="One split voucher part failed at the provider")
        else:
            v97._award_update(award["id"], "provider_unknown", error_detail="Waiting for split voucher provider confirmation")
        return False

    def _issue_split_award(award, operator_code, product, plan):
        uid = int(award["telegram_user_id"])
        amount = int(award.get("amount") or 0)
        mobile = v97._giftport_mobile(uid)
        if not mobile:
            v97._award_update(award["id"], "waiting_mobile", error_detail="User mobile not verified")
            v97._notify_mobile_needed(award)
            return False

        parts = _ensure_parts(award, operator_code, plan)
        if not parts:
            v97._award_update(
                award["id"], "needs_config",
                error_detail="Stored split voucher plan does not match the current denomination plan",
            )
            return False

        settings = v89._reward_settings()
        already_issued = sum(
            int(p.get("amount") or 0)
            for p in parts
            if str(p.get("status") or "") in {"issued", "delivered"}
        )
        remaining = max(0, amount - already_issued)

        # Apply the same budget guard as a normal award. The parent award amount
        # remains the accounting unit, so split vouchers never increase the prize.
        daily_limit = int(settings.get("daily_budget") or 0)
        monthly_limit = int(settings.get("monthly_budget") or 0)
        if already_issued == 0:
            if daily_limit and v97._budget_used("day") + amount > daily_limit:
                v97._award_update(award["id"], "budget_hold", error_detail="Daily reward budget guard reached")
                return False
            if monthly_limit and v97._budget_used("month") + amount > monthly_limit:
                v97._award_update(award["id"], "budget_hold", error_detail="Monthly reward budget guard reached")
                return False

        if remaining > 0:
            bal_ok, balance, currency = v97._get_balance()
            if not bal_ok:
                v97._award_update(award["id"], "provider_hold", error_detail=str(currency or "Could not verify Giftport balance"))
                return False
            reserve = int(settings.get("min_provider_balance") or 0)
            if str(currency or "INR").upper() != "INR":
                v97._award_update(award["id"], "provider_hold", error_detail=f"Giftport wallet currency is {currency}, expected INR")
                return False
            if float(balance) - remaining < reserve:
                v97._award_update(award["id"], "balance_hold", error_detail=f"Giftport balance ₹{balance:,.2f}; reserve ₹{reserve:,}")
                return False

        v97._award_update(
            award["id"], "issuing",
            provider_order_id=f"SPLIT:{int(award['id'])}",
            error_detail=None,
        )

        recipient_name = v97._recipient_name(uid)
        for part in _split_parts(award["id"]):
            status = str(part.get("status") or "queued")
            if status in {"issued", "delivered"}:
                continue
            if status == "provider_unknown":
                if not _reconcile_one_part(part):
                    v97._award_update(
                        award["id"], "provider_unknown",
                        error_detail=f"Voucher part {part.get('part_no')} is awaiting provider confirmation",
                    )
                    return False
                continue

            # A deterministic order ID plus a persisted attempt record prevents
            # silently purchasing the same part twice. Explicit failed attempts
            # remain visible for manual review instead of blind repurchase.
            if int(part.get("issue_attempts") or 0) > 0:
                v97._award_update(
                    award["id"], "provider_failed",
                    error_detail=f"Voucher part {part.get('part_no')} previously failed; manual review required",
                )
                return False

            _mark_part_attempt(part)
            ok, data = v97._giftport_post(
                "buy",
                {
                    "order_id": str(part["provider_order_id"]),
                    "operator_code": str(operator_code),
                    "amount": int(part["amount"]),
                    "mobile": mobile,
                    "recipient_name": recipient_name,
                },
            )
            if ok and _provider_payload_has_voucher(data):
                _store_part_success(part, data)
                bot.logger.warning(
                    "REWARD_SPLIT_PART_ISSUED award=%s part=%s amount=%s order=%s",
                    award["id"], part["part_no"], part["amount"], part["provider_order_id"],
                )
                continue

            message = str(data.get("message") or "Giftport split purchase failed")
            if data.get("network_error"):
                child_status = parent_status = "provider_unknown"
            elif "insufficient" in message.lower() and "balance" in message.lower():
                child_status = parent_status = "balance_hold"
            else:
                child_status = parent_status = "provider_failed"
            _part_update(part["id"], child_status, error_detail=message)
            v97._award_update(
                award["id"], parent_status,
                provider_order_id=f"SPLIT:{int(award['id'])}",
                error_detail=f"Voucher part {part.get('part_no')}: {message}"[:1000],
            )
            return False

        fresh_parts = _split_parts(award["id"])
        if not all(str(p.get("status") or "") in {"issued", "delivered"} for p in fresh_parts):
            v97._award_update(award["id"], "provider_unknown", error_detail="Split voucher issuance incomplete")
            return False

        v97._award_update(
            award["id"], "issued",
            provider_order_id=f"SPLIT:{int(award['id'])}",
            provider_message=f"Split denominations: {'+'.join(str(x) for x in plan)}",
            error_detail=None,
        )
        bot.logger.warning(
            "REWARD_SPLIT_COMPLETE award=%s uid=%s total=%s plan=%s",
            award["id"], uid, amount, plan,
        )
        return _deliver_award(award["id"])

    def _issue_award(award):
        settings = v89._reward_settings()
        amount = int(award.get("amount") or 0)
        operator_code = str(
            award.get("brand_code") or settings.get("default_brand_code") or "AMZN"
        ).upper()

        valid, reason, product = _validate_reward_product(operator_code, amount)
        if valid:
            return original_issue_award(award)

        plan = _split_plan(product, amount)
        if plan and sum(plan) == amount:
            bot.logger.warning(
                "REWARD_SPLIT_SELECTED award=%s operator=%s amount=%s plan=%s reason=%s",
                award.get("id"), operator_code, amount, plan, reason,
            )
            return _issue_split_award(award, operator_code, product, plan)

        # Preserve the original behavior for unsupported products/amounts.
        return original_issue_award(award)

    def _my_rewards_text(uid):
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM reward_awards
                    WHERE telegram_user_id=%s
                    ORDER BY created_at DESC LIMIT 8
                    """,
                    (int(uid),),
                )
                rows = cur.fetchall()
        if not rows:
            return (
                "🎁 <b>My Rewards</b>\n\n"
                "No voucher rewards have been issued to you yet. Keep playing the free Sports Quiz & live predictions to build your weekly score."
            )

        lines = ["🎁 <b>My Rewards</b>", ""]
        for r in rows:
            status = str(r.get("status") or "queued").replace("_", " ").title()
            lines.append(f"• ₹{int(r.get('amount') or 0):,} - {status} - {html.escape(str(r.get('period_key') or ''))}")
            parts = _split_parts(r["id"])
            if parts:
                for i, part in enumerate(parts, 1):
                    fields = _reward_fields(part)
                    lines.append(f"  Voucher {i}: ₹{int(part.get('amount') or 0):,}")
                    if fields["redeem_code"]:
                        lines.append(f"    Redeem Code: <code>{html.escape(fields['redeem_code'])}</code>")
                    if fields["pin"]:
                        lines.append(f"    PIN: <code>{html.escape(fields['pin'])}</code>")
                    if fields["voucher_url"]:
                        lines.append(f"    Claim: {html.escape(fields['voucher_url'])}")
                    if fields["reference"]:
                        lines.append(f"    Reference: <code>{html.escape(fields['reference'])}</code>")
                continue

            fields = _reward_fields(r)
            if fields["voucher_url"]:
                lines.append(f"  Claim: {html.escape(fields['voucher_url'])}")
            if fields["redeem_code"]:
                lines.append(f"  Redeem Code: <code>{html.escape(fields['redeem_code'])}</code>")
            if fields["pin"]:
                lines.append(f"  PIN: <code>{html.escape(fields['pin'])}</code>")
            if fields["reference"]:
                lines.append(f"  Reference: <code>{html.escape(fields['reference'])}</code>")
        return "\n".join(lines)

    v89._my_rewards_text = _my_rewards_text
    v97.v89._my_rewards_text = _my_rewards_text
    v97._deliver_award = _deliver_award
    v97._reconcile_unknown = _reconcile_unknown
    v97._issue_award = _issue_award

    try:
        gp = v97._catalogue_row("GPAPGV") or {}
        plan_300 = _split_plan(gp, 300)
        bot.logger.warning(
            "REWARD_DENOMINATION_FIX operator=GPAPGV variable=%s denominations=%s strict=%s amount300_plan=%s",
            bool(gp.get("variable")), str(gp.get("denominations") or "")[:300],
            bool(v97.GIFTPORT_STRICT_DENOMINATIONS), plan_300,
        )
    except Exception:
        bot.logger.exception("REWARD_DENOMINATION_FIX_DIAGNOSTIC_FAILED")

    bot.logger.warning(
        "REWARD_CODE_DISPLAY_FIX active=on GPAPGV card_no=redeem_code redeem_code=reference "
        "variable_value_validation=on split_fixed_denominations=on split_idempotency=on"
    )
    return _my_rewards_text, _deliver_award
