import threading
import time

import bot
import v101_phonepe_catalogue_probe as v101

v100 = v101.v100
v99 = v100.v99
v98 = v100.v98
v97 = v100.v97
v96 = v100.v96
v93 = v100.v93
v89 = v100.v89
v88 = v100.v88
v85 = v100.v85
v83 = v100.v83

TEST_USERNAME = "mohit_97saxena"
TEST_OPERATOR = "GPPHP"
TEST_AMOUNT = 30
TEST_PERIOD_KEY = "manual_test:phonepe_b2b:inr30:v102"

_old_callback_handler = bot.callback_handler
_old_reward_center_keyboard = v97._reward_center_keyboard


def _v102_reward_center_keyboard():
    markup = _old_reward_center_keyboard()
    rows = [list(row) for row in markup.inline_keyboard]
    test_btn = bot.InlineKeyboardButton(
        "🧪 Test PhonePe B2B ₹30",
        callback_data="v102_phonepe_test",
    )
    if not any(any(getattr(btn, "callback_data", "") == "v102_phonepe_test" for btn in row) for row in rows):
        insert_at = max(0, len(rows) - 1)
        rows.insert(insert_at, [test_btn])
    return bot.InlineKeyboardMarkup(rows)


def _find_test_user():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT telegram_user_id, telegram_username, reachable_bot
                FROM intelligence_leads
                WHERE LOWER(telegram_username)=LOWER(%s)
                ORDER BY last_seen_at DESC NULLS LAST
                LIMIT 1
                """,
                (TEST_USERNAME,),
            )
            return cur.fetchone()


def _get_or_create_test_award(uid):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO reward_awards(
                    telegram_user_id,reward_type,period_key,rank,points,amount,currency,
                    brand_code,provider,status
                ) VALUES (%s,'manual_test',%s,1,0,%s,'INR',%s,'giftport','queued')
                ON CONFLICT(reward_type,period_key,rank) DO NOTHING
                """,
                (int(uid), TEST_PERIOD_KEY, TEST_AMOUNT, TEST_OPERATOR),
            )
            cur.execute(
                """
                SELECT * FROM reward_awards
                WHERE reward_type='manual_test' AND period_key=%s AND rank=1
                LIMIT 1
                """,
                (TEST_PERIOD_KEY,),
            )
            row = cur.fetchone()
        conn.commit()
    return row


async def v102_callback_handler(update, context):
    q = update.callback_query
    data = str(q.data or "") if q else ""
    if not q or not data.startswith("v102_"):
        return await _old_callback_handler(update, context)

    if not bot.is_admin(q.from_user.id):
        await q.answer("Admin only", show_alert=True)
        return

    if data == "v102_phonepe_test":
        ok, reason, row = v97._validate_reward_product(TEST_OPERATOR, TEST_AMOUNT)
        if not ok:
            await q.answer("Product validation failed", show_alert=True)
            await q.edit_message_text(
                f"❌ <b>PhonePe B2B test unavailable</b>\n\n{reason}",
                parse_mode=bot.ParseMode.HTML,
                reply_markup=bot.InlineKeyboardMarkup([
                    [bot.InlineKeyboardButton("⬅️ Reward Center", callback_data="v89_reward_center")]
                ]),
            )
            return

        lead = _find_test_user()
        if not lead:
            await q.answer("Test account not found", show_alert=True)
            return
        if not lead.get("reachable_bot"):
            await q.answer("Test account must start the bot first", show_alert=True)
            return
        if not v97._giftport_mobile(int(lead["telegram_user_id"])):
            await q.answer("Verify mobile on the test account first", show_alert=True)
            await q.edit_message_text(
                "📱 <b>Mobile verification required</b>\n\n"
                "Open @BetroxyOfficialBot from @mohit_97saxena and use <b>📱 Verify Mobile</b>. "
                "No voucher has been requested from Giftport.",
                parse_mode=bot.ParseMode.HTML,
                reply_markup=bot.InlineKeyboardMarkup([
                    [bot.InlineKeyboardButton("⬅️ Reward Center", callback_data="v89_reward_center")]
                ]),
            )
            return

        brand = str((row or {}).get("brand_name") or "Phone Pe Gift Voucher B2B")
        await q.answer()
        await q.edit_message_text(
            "🧪 <b>ONE-TIME LIVE VOUCHER TEST</b>\n\n"
            f"Product: <b>{brand}</b>\n"
            f"Operator: <code>{TEST_OPERATOR}</code>\n"
            f"Amount: <b>₹{TEST_AMOUNT}</b>\n"
            f"Recipient: <b>@{TEST_USERNAME}</b>\n\n"
            "This confirmation can create exactly one real Giftport voucher. "
            "The order is idempotent, so repeating the confirmation cannot create a second test award.",
            parse_mode=bot.ParseMode.HTML,
            reply_markup=bot.InlineKeyboardMarkup([
                [bot.InlineKeyboardButton("✅ Issue ₹30 Test Voucher", callback_data="v102_phonepe_confirm")],
                [bot.InlineKeyboardButton("❌ Cancel", callback_data="v89_reward_center")],
            ]),
        )
        return

    if data == "v102_phonepe_confirm":
        await q.answer("Processing one-time test…")
        lead = _find_test_user()
        if not lead or not lead.get("reachable_bot"):
            await q.edit_message_text("❌ Test account is not reachable. No voucher was issued.")
            return
        uid = int(lead["telegram_user_id"])
        if not v97._giftport_mobile(uid):
            await q.edit_message_text("❌ Verified mobile is missing. No voucher was issued.")
            return
        ok, reason, _ = v97._validate_reward_product(TEST_OPERATOR, TEST_AMOUNT)
        if not ok:
            await q.edit_message_text(f"❌ {reason}")
            return

        award = _get_or_create_test_award(uid)
        if not award:
            await q.edit_message_text("❌ Could not create the test award record. No voucher was issued.")
            return

        status_before = str(award.get("status") or "")
        if status_before == "delivered":
            await q.edit_message_text(
                "✅ <b>PhonePe B2B ₹30 test already completed.</b>\n\n"
                "The existing test award is already marked delivered, so no second voucher was requested.",
                parse_mode=bot.ParseMode.HTML,
                reply_markup=bot.InlineKeyboardMarkup([
                    [bot.InlineKeyboardButton("⬅️ Reward Center", callback_data="v89_reward_center")]
                ]),
            )
            return

        try:
            result = v97._issue_award(award)
        except Exception as exc:
            bot.logger.exception("V102_PHONEPE_TEST_FAILED award=%s", award.get("id"))
            await q.edit_message_text(
                f"❌ Test failed safely: <code>{str(exc)[:240]}</code>",
                parse_mode=bot.ParseMode.HTML,
            )
            return

        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM reward_awards WHERE id=%s", (int(award["id"]),))
                final = cur.fetchone() or award

        status = str(final.get("status") or "unknown")
        order_id = str(final.get("provider_order_id") or "not assigned")
        delivered = status == "delivered"
        await q.edit_message_text(
            "🧪 <b>PhonePe B2B ₹30 Test Result</b>\n\n"
            f"Operator: <code>{TEST_OPERATOR}</code>\n"
            f"Amount: <b>₹{TEST_AMOUNT}</b>\n"
            f"Order ID: <code>{order_id}</code>\n"
            f"Status: <b>{status}</b>\n"
            f"Telegram delivery: <b>{'SUCCESS ✅' if delivered else 'PENDING / CHECK STATUS'}</b>\n\n"
            "Global Auto Issue remains OFF unless you separately enable it in Reward Center.",
            parse_mode=bot.ParseMode.HTML,
            reply_markup=bot.InlineKeyboardMarkup([
                [bot.InlineKeyboardButton("⬅️ Reward Center", callback_data="v89_reward_center")]
            ]),
        )
        bot.logger.warning(
            "V102_PHONEPE_TEST result=%s award=%s status=%s order=%s",
            bool(result), award.get("id"), status, order_id,
        )
        return

    return await _old_callback_handler(update, context)


v97._reward_center_keyboard = _v102_reward_center_keyboard
bot.callback_handler = v102_callback_handler

bot.logger.warning(
    "V102_PHONEPE_B2B_ADMIN_TEST active=on operator=GPPHP amount=30 target=@mohit_97saxena global_auto_issue_unchanged=on"
)


if __name__ == "__main__":
    v97._startup_diagnostic()
    v96._startup_diagnostic()
    v93._startup_pdf_diagnostic()
    v101._phonepe_probe()
    v88.v63.apply_signup_cta()
    v85._enable_smart_reply_without_reset()
    threading.Thread(target=v83._worker_loop, name="betroxy-engagement-worker", daemon=True).start()
    bot.logger.warning("V102 polling handover delay=12s")
    time.sleep(12)
    bot.main()
