"""Correct reward-code presentation for GiftPort vouchers.

For Amazon Pay B2B (GPAPGV), GiftPort's `redeem_code` value is a provider/reference
number in our live response, while `card_no` is the customer-facing voucher/redeem
code. Never label the reference number as the redeem code.
"""
import html

import bot


def install(v97, v89, v83):
    def _reward_fields(row):
        brand = str(row.get("brand_code") or "").strip().upper()
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

    def _deliver_award(award_id):
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM reward_awards WHERE id=%s", (int(award_id),))
                award = cur.fetchone()
        if not award or award.get("status") == "delivered":
            return False

        fields = _reward_fields(award)
        if not fields["redeem_code"] and not fields["voucher_url"]:
            return False

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

        ok, data = v83._tg_send(
            int(award["telegram_user_id"]),
            "\n".join(lines),
            [[{"text": "🎁 My Rewards", "url": f"https://t.me/{v83.OFFICIAL_BOT}?start=rewards"}]],
        )
        if ok:
            message_id = ((data.get("result") or {}).get("message_id")) if isinstance(data, dict) else None
            v97._award_update(award["id"], "delivered", delivered_message_id=message_id, error_detail=None)
            bot.logger.warning("REWARD_CODE_DISPLAY_FIX delivered award=%s uid=%s", award["id"], award["telegram_user_id"])
            return True
        v97._award_update(
            award["id"],
            "delivery_pending",
            error_detail=str((data or {}).get("description") or "Telegram delivery failed")[:1000],
        )
        return False

    v89._my_rewards_text = _my_rewards_text
    v97.v89._my_rewards_text = _my_rewards_text
    v97._deliver_award = _deliver_award
    bot.logger.warning("REWARD_CODE_DISPLAY_FIX active=on GPAPGV card_no=redeem_code redeem_code=reference")
    return _my_rewards_text, _deliver_award
