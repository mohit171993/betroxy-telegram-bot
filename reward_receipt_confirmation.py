"""Additive winner receipt confirmation for BETROXY voucher rewards.

Purpose:
- add a user acknowledgement step after Telegram reward delivery;
- persist the winner's first confirmation timestamp;
- show Sent / Winner Confirmed state in the Daily Quiz admin screen;
- alert the admin when a winner confirms receipt.

This does not attempt to claim that the Amazon voucher was redeemed. It records
only that the intended Telegram user explicitly acknowledged receiving the
voucher message/code.
"""
import html
from datetime import timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
_installed = False
_callback_installed = False


def prepare(admin_rewards, bot):
    global _installed
    if _installed:
        return

    def _ensure_schema():
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS reward_delivery_confirmations (
                        reward_id BIGINT PRIMARY KEY,
                        telegram_user_id BIGINT NOT NULL,
                        confirmed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        confirmation_message_id BIGINT,
                        confirmation_chat_id BIGINT,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_reward_delivery_confirmations_user
                    ON reward_delivery_confirmations(telegram_user_id, confirmed_at DESC)
                    """
                )
            conn.commit()

    _ensure_schema()

    def _confirmation(reward_id):
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM reward_delivery_confirmations WHERE reward_id=%s",
                    (int(reward_id),),
                )
                return cur.fetchone()

    def _fmt_when(dt):
        if not dt:
            return ""
        try:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            local = dt.astimezone(IST)
            return local.strftime("%d %b %Y, %I:%M %p IST")
        except Exception:
            return str(dt)

    # Extend the already-composed admin screen without replacing any existing
    # payout/provider diagnostics.
    original_screen_text = admin_rewards._screen_text

    def _screen_text_with_receipt_state(campaign, rows):
        text = original_screen_text(campaign, rows)
        finalized, _ = admin_rewards._finalization_state(campaign)
        if not finalized or not rows:
            return text

        lines = ["", "📬 <b>Winner Receipt Confirmation</b>"]
        for rank in range(1, min(3, len(rows)) + 1):
            award = admin_rewards._award_for_rank(campaign, rank)
            if not award:
                lines.append(f"#{rank}: <b>Not sent</b>")
                continue

            status = str(award.get("status") or "queued")
            confirmation = _confirmation(award["id"])
            if confirmation:
                lines.append(
                    f"#{rank}: <b>Sent ✅ → Winner Confirmed ✅</b> — "
                    f"{html.escape(_fmt_when(confirmation.get('confirmed_at')))}"
                )
            elif status == "delivered":
                lines.append(f"#{rank}: <b>Sent ✅ → Not yet confirmed</b>")
            elif status in {"issued", "delivery_pending"}:
                lines.append(
                    f"#{rank}: <b>{html.escape(status.replace('_', ' ').title())} → Not yet confirmed</b>"
                )
            else:
                lines.append(
                    f"#{rank}: <b>Not confirmed</b> — reward status "
                    f"{html.escape(status.replace('_', ' ').title())}"
                )

        lines += [
            "",
            "<i>Winner Confirmed means the intended Telegram user tapped "
            "“I Received My Voucher”. It does not prove Amazon redemption.</i>",
        ]
        return text + "\n" + "\n".join(lines)

    admin_rewards._screen_text = _screen_text_with_receipt_state

    original_bot_main = bot.main

    def _install_callback_once():
        global _callback_installed
        if _callback_installed:
            return

        previous_callback = bot.callback_handler

        async def reward_receipt_callback(update, context):
            q = getattr(update, "callback_query", None)
            data = str(getattr(q, "data", "") or "") if q else ""
            if not q or not data.startswith("reward_received:"):
                return await previous_callback(update, context)

            try:
                reward_id = int(data.split(":", 1)[1])
            except Exception:
                try:
                    await q.answer("Invalid reward confirmation.", show_alert=True)
                except Exception:
                    pass
                return

            with bot.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT * FROM reward_awards WHERE id=%s",
                        (reward_id,),
                    )
                    award = cur.fetchone()

            if not award:
                try:
                    await q.answer("Reward not found.", show_alert=True)
                except Exception:
                    pass
                return

            uid = int(q.from_user.id)
            if uid != int(award.get("telegram_user_id") or 0):
                try:
                    await q.answer(
                        "This receipt confirmation belongs to another reward.",
                        show_alert=True,
                    )
                except Exception:
                    pass
                bot.logger.warning(
                    "REWARD_RECEIPT_CONFIRMATION_REJECTED reward=%s click_uid=%s owner_uid=%s",
                    reward_id, uid, award.get("telegram_user_id"),
                )
                return

            message_id = int(getattr(getattr(q, "message", None), "message_id", 0) or 0)
            chat_id = int(getattr(getattr(getattr(q, "message", None), "chat", None), "id", uid) or uid)

            with bot.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT confirmed_at FROM reward_delivery_confirmations WHERE reward_id=%s",
                        (reward_id,),
                    )
                    existing = cur.fetchone()
                    first_confirmation = existing is None
                    if first_confirmation:
                        cur.execute(
                            """
                            INSERT INTO reward_delivery_confirmations(
                                reward_id,telegram_user_id,confirmed_at,
                                confirmation_message_id,confirmation_chat_id,
                                created_at,updated_at
                            ) VALUES (%s,%s,NOW(),%s,%s,NOW(),NOW())
                            """,
                            (reward_id, uid, message_id or None, chat_id),
                        )
                    else:
                        cur.execute(
                            """
                            UPDATE reward_delivery_confirmations
                            SET updated_at=NOW(),
                                confirmation_message_id=COALESCE(confirmation_message_id,%s),
                                confirmation_chat_id=COALESCE(confirmation_chat_id,%s)
                            WHERE reward_id=%s
                            """,
                            (message_id or None, chat_id, reward_id),
                        )
                    cur.execute(
                        "SELECT * FROM reward_delivery_confirmations WHERE reward_id=%s",
                        (reward_id,),
                    )
                    confirmation = cur.fetchone()
                conn.commit()

            try:
                await q.answer(
                    "Voucher receipt confirmed ✅" if first_confirmation else "Already confirmed ✅"
                )
            except Exception:
                pass

            # Replace only the acknowledgement row; preserve My Rewards and any
            # other existing buttons.
            try:
                markup = getattr(q.message, "reply_markup", None)
                rows = [list(r) for r in (getattr(markup, "inline_keyboard", None) or [])]
                kept = []
                for row in rows:
                    if any(
                        str(getattr(b, "callback_data", "") or "").startswith("reward_received:")
                        for b in row
                    ):
                        continue
                    kept.append(row)
                new_rows = [[
                    bot.InlineKeyboardButton(
                        "✅ Voucher Receipt Confirmed",
                        callback_data=f"reward_received:{reward_id}",
                    )
                ]] + kept
                await q.message.edit_reply_markup(
                    reply_markup=bot.InlineKeyboardMarkup(new_rows)
                )
            except Exception as exc:
                bot.logger.warning(
                    "REWARD_RECEIPT_MARKUP_EDIT_SKIPPED reward=%s reason=%s",
                    reward_id, type(exc).__name__,
                )

            when_text = _fmt_when(confirmation.get("confirmed_at"))
            if first_confirmation:
                bot.logger.info(
                    "REWARD_RECEIPT_ADMIN_POPUP_SUPPRESSED reward=%s uid=%s "
                    "reason=reference_admin_alert_parity",
                    reward_id, uid,
                )

            bot.logger.warning(
                "REWARD_RECEIPT_CONFIRMED reward=%s uid=%s rank=%s amount=%s first=%s confirmed_at=%s",
                reward_id, uid, award.get("rank"), award.get("amount"),
                first_confirmation, when_text,
            )
            return

        bot.callback_handler = reward_receipt_callback
        _callback_installed = True
        bot.logger.warning(
            "REWARD_RECEIPT_CONFIRMATION_CALLBACK active=on owner_validation=on "
            "first_confirmation_persisted=on admin_alert=off redemption_claim=off"
        )

    def main_with_receipt_confirmation():
        # production.main calls bot.main only after all historical callback
        # overlays are installed. Installing here makes this the final outer
        # callback without disturbing the production feature guard.
        _install_callback_once()
        return original_bot_main()

    bot.main = main_with_receipt_confirmation

    bot.logger.warning(
        "REWARD_RECEIPT_CONFIRMATION active=on schema=reward_delivery_confirmations "
        "admin_screen=sent_vs_confirmed callback_installs_before_polling=on "
        "telegram_read_receipt_claim=off amazon_redemption_claim=off"
    )
    _installed = True
