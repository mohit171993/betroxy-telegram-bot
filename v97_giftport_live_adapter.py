import html
import os
import re
import threading
import time
from datetime import datetime, timezone

import requests

import bot
import v96_customer_menu_final as v96

v95 = v96.v95
v94 = v96.v94
v93 = v96.v93
v91 = v96.v91
v89 = v96.v89
v88 = v96.v88
v85 = v96.v85
v83 = v96.v83

# -----------------------------------------------------------------------------
# Giftport Universal Gift Card API
# Docs supplied by account owner:
#   Base: https://giftport.in/api/giftcard
#   POST /catalogue  {clientId, secretId}
#   POST /balance    {clientId, secretId}
#   POST /buy        {clientId, secretId, order_id, operator_code, amount,
#                     mobile, recipient_name, recipient_email?}
#   POST /status     {clientId, secretId, order_id}
#
# We deliberately use POST JSON only so credentials are not placed in URLs.
# Real issuance remains OFF until credentials are configured and Auto Issue is ON.
# -----------------------------------------------------------------------------

GIFTPORT_API_BASE = os.getenv("GIFTPORT_API_BASE", "https://giftport.in/api/giftcard").strip().rstrip("/")
GIFTPORT_CLIENT_ID = (os.getenv("GIFTPORT_CLIENT_ID", "") or os.getenv("GIFTPORT_API_KEY", "")).strip()
GIFTPORT_SECRET_ID = (os.getenv("GIFTPORT_SECRET_ID", "") or os.getenv("GIFTPORT_API_SECRET", "")).strip()
GIFTPORT_TIMEOUT = max(8, int(os.getenv("GIFTPORT_TIMEOUT", "20")))
GIFTPORT_STRICT_DENOMINATIONS = os.getenv("GIFTPORT_STRICT_DENOMINATIONS", "1").strip().lower() not in {"0", "false", "off", "no"}

_old_callback_handler = bot.callback_handler
_old_worker_cycle = v83._worker_cycle
_old_prepare_weekly_awards = v89._prepare_weekly_awards


def _ensure_v97_schema():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS reward_provider_catalogue (
                    operator_code TEXT PRIMARY KEY,
                    brand_name TEXT NOT NULL,
                    brand_image TEXT,
                    denominations TEXT,
                    variable BOOLEAN NOT NULL DEFAULT FALSE,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS reward_provider_state (
                    id INTEGER PRIMARY KEY,
                    provider TEXT NOT NULL DEFAULT 'giftport',
                    wallet_balance NUMERIC(16,2),
                    currency TEXT,
                    last_balance_at TIMESTAMPTZ,
                    last_catalogue_at TIMESTAMPTZ,
                    last_success_at TIMESTAMPTZ,
                    last_error TEXT,
                    last_error_at TIMESTAMPTZ,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute("INSERT INTO reward_provider_state(id) VALUES (1) ON CONFLICT(id) DO NOTHING")

            cur.execute("ALTER TABLE reward_awards ADD COLUMN IF NOT EXISTS provider_transaction_id TEXT")
            cur.execute("ALTER TABLE reward_awards ADD COLUMN IF NOT EXISTS card_no TEXT")
            cur.execute("ALTER TABLE reward_awards ADD COLUMN IF NOT EXISTS provider_message TEXT")
            cur.execute("ALTER TABLE reward_awards ADD COLUMN IF NOT EXISTS issue_attempts INTEGER NOT NULL DEFAULT 0")
            cur.execute("ALTER TABLE reward_awards ADD COLUMN IF NOT EXISTS last_issue_attempt_at TIMESTAMPTZ")
            cur.execute("ALTER TABLE reward_awards ADD COLUMN IF NOT EXISTS last_provider_check_at TIMESTAMPTZ")
            cur.execute("ALTER TABLE reward_awards ADD COLUMN IF NOT EXISTS delivered_message_id BIGINT")

            # V89 used a placeholder before the exact Giftport docs were supplied.
            cur.execute(
                """
                UPDATE reward_settings
                SET default_brand_code='AMZN', updated_at=NOW()
                WHERE id=1 AND COALESCE(default_brand_code,'') IN ('', 'AMAZON_IN')
                """
            )
        conn.commit()


def _giftport_ready():
    return bool(GIFTPORT_CLIENT_ID and GIFTPORT_SECRET_ID and GIFTPORT_API_BASE)


def _state_update(*, balance=None, currency=None, catalogue=False, success=False, error=None):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            sets = ["updated_at=NOW()"]
            params = []
            if balance is not None:
                sets += ["wallet_balance=%s", "last_balance_at=NOW()"]
                params.append(balance)
            if currency is not None:
                sets.append("currency=%s")
                params.append(str(currency))
            if catalogue:
                sets.append("last_catalogue_at=NOW()")
            if success:
                sets += ["last_success_at=NOW()", "last_error=NULL"]
            if error is not None:
                sets += ["last_error=%s", "last_error_at=NOW()"]
                params.append(str(error)[:1000])
            params.append(1)
            cur.execute(f"UPDATE reward_provider_state SET {', '.join(sets)} WHERE id=%s", tuple(params))
        conn.commit()


def _provider_state():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM reward_provider_state WHERE id=1")
            return cur.fetchone() or {}


def _giftport_post(endpoint, payload=None):
    if not _giftport_ready():
        return False, {"status": "failure", "message": "Giftport credentials are not configured"}
    body = {
        "clientId": GIFTPORT_CLIENT_ID,
        "secretId": GIFTPORT_SECRET_ID,
    }
    if payload:
        body.update(payload)
    try:
        r = requests.post(
            f"{GIFTPORT_API_BASE}/{endpoint.lstrip('/')}",
            json=body,
            timeout=GIFTPORT_TIMEOUT,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        try:
            data = r.json() if r.content else {}
        except Exception:
            data = {"status": "failure", "message": f"Non-JSON response (HTTP {r.status_code})"}
        ok = bool(r.ok and str(data.get("status") or "").lower() == "success")
        if ok:
            _state_update(success=True)
        else:
            _state_update(error=str(data.get("message") or f"HTTP {r.status_code}"))
        return ok, data
    except Exception as exc:
        msg = f"Network/API error: {exc}"
        _state_update(error=msg)
        return False, {"status": "failure", "message": msg, "network_error": True}


def _sync_catalogue():
    ok, data = _giftport_post("catalogue")
    if not ok:
        return False, str(data.get("message") or "Catalogue request failed"), 0
    catalogue = data.get("catalogue") or []
    if not isinstance(catalogue, list):
        return False, "Invalid catalogue response", 0
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE reward_provider_catalogue SET is_active=FALSE")
            for item in catalogue:
                code = str(item.get("operator_code") or "").strip().upper()
                name = str(item.get("brand_name") or code or "Gift Card").strip()
                if not code:
                    continue
                variable = str(item.get("variable") or "").strip().lower() in {"yes", "true", "1", "y"}
                cur.execute(
                    """
                    INSERT INTO reward_provider_catalogue(
                        operator_code,brand_name,brand_image,denominations,variable,is_active,synced_at
                    ) VALUES (%s,%s,%s,%s,%s,TRUE,NOW())
                    ON CONFLICT(operator_code) DO UPDATE SET
                        brand_name=EXCLUDED.brand_name,
                        brand_image=EXCLUDED.brand_image,
                        denominations=EXCLUDED.denominations,
                        variable=EXCLUDED.variable,
                        is_active=TRUE,
                        synced_at=NOW()
                    """,
                    (
                        code, name, item.get("brand_image"),
                        str(item.get("denominations") or ""), variable,
                    ),
                )
        conn.commit()
    _state_update(catalogue=True, success=True)
    return True, "Catalogue synced", len(catalogue)


def _get_balance():
    ok, data = _giftport_post("balance")
    if not ok:
        return False, None, str(data.get("message") or "Balance request failed")
    try:
        balance = float(data.get("balance"))
    except Exception:
        return False, None, "Invalid balance response"
    currency = str(data.get("currency") or "INR").upper()
    _state_update(balance=balance, currency=currency, success=True)
    return True, balance, currency


def _catalogue_row(operator_code):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM reward_provider_catalogue WHERE operator_code=%s AND is_active=TRUE",
                (str(operator_code or "").upper(),),
            )
            return cur.fetchone()


def _parse_denominations(raw):
    values = set()
    for token in re.split(r"[,|;/\s]+", str(raw or "")):
        token = token.strip()
        if not token:
            continue
        try:
            val = float(token)
            if val.is_integer():
                values.add(int(val))
            else:
                values.add(val)
        except Exception:
            pass
    return values


def _validate_reward_product(operator_code, amount):
    row = _catalogue_row(operator_code)
    if not row and _giftport_ready():
        _sync_catalogue()
        row = _catalogue_row(operator_code)
    if not row:
        return False, f"Giftport operator {operator_code} is not available in the synced catalogue", None
    denoms = _parse_denominations(row.get("denominations"))
    if GIFTPORT_STRICT_DENOMINATIONS and denoms and float(amount) not in {float(x) for x in denoms}:
        options = ", ".join(f"₹{int(x):,}" if float(x).is_integer() else f"₹{x}" for x in sorted(denoms))
        return False, f"₹{amount:,} is not listed for {row.get('brand_name')}. Available: {options}", row
    return True, "ok", row


def _giftport_mobile(uid):
    row = v89._mobile_row(uid) or {}
    digits = re.sub(r"\D+", "", str(row.get("mobile_number") or ""))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    if len(digits) == 10:
        return digits
    return None


def _recipient_name(uid):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT first_name,last_name,telegram_username
                FROM intelligence_leads WHERE telegram_user_id=%s
                """,
                (int(uid),),
            )
            row = cur.fetchone() or {}
    name = " ".join(x for x in [str(row.get("first_name") or "").strip(), str(row.get("last_name") or "").strip()] if x).strip()
    if not name:
        name = str(row.get("telegram_username") or "BETROXY User").lstrip("@")
    return name[:80]


def _budget_used(period):
    where = "COALESCE(issued_at,created_at) >= DATE_TRUNC('day',NOW())" if period == "day" else "COALESCE(issued_at,created_at) >= DATE_TRUNC('month',NOW())"
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT COALESCE(SUM(amount),0) AS n
                FROM reward_awards
                WHERE status IN ('issuing','provider_unknown','issued','delivery_pending','delivered')
                  AND {where}
                """
            )
            return int((cur.fetchone() or {}).get("n") or 0)


def _award_update(award_id, status, **fields):
    allowed = {
        "provider_order_id", "provider_transaction_id", "voucher_code", "voucher_pin",
        "voucher_url", "card_no", "provider_message", "error_detail",
        "delivered_message_id",
    }
    sets = ["status=%s"]
    params = [status]
    for key, value in fields.items():
        if key in allowed:
            sets.append(f"{key}=%s")
            params.append(value)
    if status in {"issued", "delivery_pending", "delivered"}:
        sets.append("issued_at=COALESCE(issued_at,NOW())")
    if status == "delivered":
        sets.append("delivered_at=COALESCE(delivered_at,NOW())")
    params.append(int(award_id))
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE reward_awards SET {', '.join(sets)} WHERE id=%s", tuple(params))
        conn.commit()


def _mark_issue_attempt(award_id, order_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE reward_awards
                SET status='issuing', provider_order_id=%s,
                    issue_attempts=issue_attempts+1, last_issue_attempt_at=NOW(), error_detail=NULL
                WHERE id=%s
                """,
                (order_id, int(award_id)),
            )
        conn.commit()


def _mark_provider_check(award_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE reward_awards SET last_provider_check_at=NOW() WHERE id=%s", (int(award_id),))
        conn.commit()


def _notify_mobile_needed(award):
    uid = int(award["telegram_user_id"])
    key = f"reward_mobile_needed:{int(award['id'])}"
    text = (
        "🏆 <b>You have a BETROXY reward waiting</b>\n\n"
        f"Reward value: <b>₹{int(award['amount']):,}</b>\n\n"
        "To issue your Indian gift voucher securely, please verify your own mobile number once. "
        "The voucher will stay reserved until verification."
    )
    kb = [[{"text": "📱 Verify Mobile", "url": v89.MOBILE_DEEPLINK}]]
    try:
        v83._send_claimed(uid, "reward", key, text, kb)
    except Exception:
        bot.logger.exception("V97_REWARD_MOBILE_NOTIFY_FAILED award=%s", award.get("id"))


def _store_provider_success(award, data):
    _award_update(
        award["id"],
        "issued",
        provider_order_id=str(data.get("order_id") or award.get("provider_order_id") or ""),
        provider_transaction_id=str(data.get("transaction_id") or ""),
        voucher_code=str(data.get("redeem_code") or ""),
        card_no=str(data.get("card_no") or ""),
        provider_message=str(data.get("message") or ""),
        error_detail=None,
    )


def _deliver_award(award_id):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM reward_awards WHERE id=%s", (int(award_id),))
            award = cur.fetchone()
    if not award or award.get("status") == "delivered":
        return False
    if not award.get("voucher_code") and not award.get("voucher_url"):
        return False

    brand = _catalogue_row(award.get("brand_code")) or {}
    brand_name = str(brand.get("brand_name") or award.get("brand_code") or "Gift Voucher")
    lines = [
        "🎉 <b>BETROXY Reward Delivered</b>",
        "",
        f"🏆 Rank: <b>#{int(award.get('rank') or 0)}</b>",
        f"🎁 Reward: <b>{html.escape(brand_name)}</b>",
        f"💰 Value: <b>₹{int(award.get('amount') or 0):,}</b>",
        "",
    ]
    if award.get("voucher_code"):
        lines.append(f"🔐 Redeem Code: <code>{html.escape(str(award['voucher_code']))}</code>")
    if award.get("card_no"):
        lines.append(f"💳 Card No.: <code>{html.escape(str(award['card_no']))}</code>")
    if award.get("voucher_pin"):
        lines.append(f"🔑 PIN: <code>{html.escape(str(award['voucher_pin']))}</code>")
    if award.get("voucher_url"):
        lines.append(f"🔗 Claim: {html.escape(str(award['voucher_url']))}")
    lines += ["", "Keep this voucher private. You can also find it later under 🎁 My Rewards."]

    ok, data = v83._tg_send(
        int(award["telegram_user_id"]),
        "\n".join(lines),
        [[{"text": "🎁 My Rewards", "url": f"https://t.me/{v83.OFFICIAL_BOT}?start=rewards"}]],
    )
    if ok:
        message_id = ((data.get("result") or {}).get("message_id")) if isinstance(data, dict) else None
        _award_update(award["id"], "delivered", delivered_message_id=message_id, error_detail=None)
        bot.logger.warning("V97_REWARD_DELIVERED award=%s uid=%s amount=%s", award["id"], award["telegram_user_id"], award["amount"])
        return True
    _award_update(award["id"], "delivery_pending", error_detail=str((data or {}).get("description") or "Telegram delivery failed")[:1000])
    return False


def _reconcile_unknown(award):
    last = award.get("last_provider_check_at")
    if last:
        try:
            if (datetime.now(timezone.utc) - last).total_seconds() < 600:
                return False
        except Exception:
            pass
    order_id = str(award.get("provider_order_id") or "").strip()
    if not order_id:
        _award_update(award["id"], "failed", error_detail="Missing provider order id for reconciliation")
        return False
    _mark_provider_check(award["id"])
    ok, data = _giftport_post("status", {"order_id": order_id})
    if ok and data.get("redeem_code"):
        _store_provider_success(award, data)
        return _deliver_award(award["id"])
    _award_update(award["id"], "provider_unknown", error_detail=str(data.get("message") or "Order status not yet confirmed")[:1000])
    return False


def _issue_award(award):
    settings = v89._reward_settings()
    amount = int(award.get("amount") or 0)
    uid = int(award["telegram_user_id"])
    operator_code = str(award.get("brand_code") or settings.get("default_brand_code") or "AMZN").upper()

    mobile = _giftport_mobile(uid)
    if not mobile:
        _award_update(award["id"], "waiting_mobile", error_detail="User mobile not verified")
        _notify_mobile_needed(award)
        return False

    valid, reason, product = _validate_reward_product(operator_code, amount)
    if not valid:
        _award_update(award["id"], "needs_config", error_detail=reason)
        return False

    daily_limit = int(settings.get("daily_budget") or 0)
    monthly_limit = int(settings.get("monthly_budget") or 0)
    if daily_limit and _budget_used("day") + amount > daily_limit:
        _award_update(award["id"], "budget_hold", error_detail="Daily reward budget guard reached")
        return False
    if monthly_limit and _budget_used("month") + amount > monthly_limit:
        _award_update(award["id"], "budget_hold", error_detail="Monthly reward budget guard reached")
        return False

    bal_ok, balance, currency = _get_balance()
    if not bal_ok:
        _award_update(award["id"], "provider_hold", error_detail=str(currency or "Could not verify Giftport balance"))
        return False
    reserve = int(settings.get("min_provider_balance") or 0)
    if str(currency or "INR").upper() != "INR":
        _award_update(award["id"], "provider_hold", error_detail=f"Giftport wallet currency is {currency}, expected INR")
        return False
    if float(balance) - amount < reserve:
        _award_update(award["id"], "balance_hold", error_detail=f"Giftport balance ₹{balance:,.2f}; reserve ₹{reserve:,}")
        return False

    order_id = str(award.get("provider_order_id") or f"BTRX_{int(award['id'])}")
    _mark_issue_attempt(award["id"], order_id)
    ok, data = _giftport_post(
        "buy",
        {
            "order_id": order_id,
            "operator_code": operator_code,
            "amount": amount,
            "mobile": mobile,
            "recipient_name": _recipient_name(uid),
        },
    )
    if ok and data.get("redeem_code"):
        award = dict(award)
        award["provider_order_id"] = order_id
        _store_provider_success(award, data)
        return _deliver_award(award["id"])

    message = str(data.get("message") or "Giftport purchase failed")
    if data.get("network_error"):
        # The provider may have accepted the order even if our HTTP response was lost.
        # Never repurchase. Reconcile by the same order_id first.
        _award_update(award["id"], "provider_unknown", provider_order_id=order_id, error_detail=message)
    elif "insufficient" in message.lower() and "balance" in message.lower():
        _award_update(award["id"], "balance_hold", provider_order_id=order_id, error_detail=message)
    else:
        _award_update(award["id"], "provider_failed", provider_order_id=order_id, error_detail=message)
    return False


def _queue_rows(limit=12):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM reward_awards
                WHERE status IN (
                    'queued','waiting_mobile','budget_hold','balance_hold','provider_hold',
                    'provider_unknown','issued','delivery_pending'
                )
                ORDER BY created_at ASC
                LIMIT %s
                """,
                (int(limit),),
            )
            return cur.fetchall()


def _process_reward_queue(limit=12):
    settings = v89._reward_settings()
    if not settings.get("program_enabled") or not settings.get("auto_issue_enabled") or not _giftport_ready():
        return {"processed": 0, "delivered": 0, "skipped": True}
    processed = 0
    delivered = 0
    for award in _queue_rows(limit):
        processed += 1
        status = str(award.get("status") or "queued")
        try:
            if status in {"issued", "delivery_pending"}:
                delivered += int(bool(_deliver_award(award["id"])))
            elif status == "provider_unknown":
                delivered += int(bool(_reconcile_unknown(award)))
            else:
                delivered += int(bool(_issue_award(award)))
        except Exception:
            bot.logger.exception("V97_REWARD_PROCESS_FAILED award=%s", award.get("id"))
        time.sleep(0.15)
    return {"processed": processed, "delivered": delivered, "skipped": False}


def _reward_counts():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) FILTER(WHERE status='queued') AS queued,
                    COUNT(*) FILTER(WHERE status='waiting_mobile') AS waiting_mobile,
                    COUNT(*) FILTER(WHERE status IN ('budget_hold','balance_hold','provider_hold','needs_config')) AS held,
                    COUNT(*) FILTER(WHERE status IN ('provider_unknown','provider_failed','delivery_pending')) AS attention,
                    COUNT(*) FILTER(WHERE status='delivered') AS delivered,
                    COALESCE(SUM(amount) FILTER(WHERE status='delivered'),0) AS delivered_value
                FROM reward_awards
                """
            )
            return cur.fetchone() or {}


def _reward_center_text():
    s = v89._reward_settings()
    st = _provider_state()
    c = _reward_counts()
    ready = "CONNECTED ✅" if _giftport_ready() else "WAITING FOR CREDENTIALS"
    balance = st.get("wallet_balance")
    bal_text = f"₹{float(balance):,.2f}" if balance is not None else "Not checked"
    last_err = str(st.get("last_error") or "").strip()
    return (
        "🎁 <b>BETROXY REWARD CENTER</b>\n\n"
        f"Program: <b>{'ON ✅' if s.get('program_enabled') else 'OFF'}</b>\n"
        f"Auto voucher issue: <b>{'ON ✅' if s.get('auto_issue_enabled') else 'OFF'}</b>\n"
        f"Giftport API: <b>{ready}</b>\n"
        f"Currency: <b>INR</b>\n"
        f"Wallet balance: <b>{bal_text}</b>\n"
        f"Default operator: <b>{html.escape(str(s.get('default_brand_code') or 'AMZN'))}</b>\n\n"
        f"🥇 Weekly #1: <b>₹{int(s.get('weekly_first_amount') or 0):,}</b>\n"
        f"🥈 Weekly #2: <b>₹{int(s.get('weekly_second_amount') or 0):,}</b>\n"
        f"🥉 Weekly #3: <b>₹{int(s.get('weekly_third_amount') or 0):,}</b>\n"
        f"Daily budget: <b>₹{int(s.get('daily_budget') or 0):,}</b>\n"
        f"Monthly budget: <b>₹{int(s.get('monthly_budget') or 0):,}</b>\n"
        f"Wallet reserve: <b>₹{int(s.get('min_provider_balance') or 0):,}</b>\n\n"
        f"Queued: <b>{int(c.get('queued') or 0)}</b> | Waiting mobile: <b>{int(c.get('waiting_mobile') or 0)}</b>\n"
        f"Held/config: <b>{int(c.get('held') or 0)}</b> | Needs attention: <b>{int(c.get('attention') or 0)}</b>\n"
        f"Delivered: <b>{int(c.get('delivered') or 0)}</b> (₹{int(c.get('delivered_value') or 0):,})"
        + (f"\n\n⚠️ Last Giftport error: <code>{html.escape(last_err[:220])}</code>" if last_err else "")
    )


def _reward_center_keyboard():
    s = v89._reward_settings()
    return bot.InlineKeyboardMarkup([
        [bot.InlineKeyboardButton(f"🏆 Reward Program: {'ON ✅' if s.get('program_enabled') else 'OFF'}", callback_data="v89_reward_toggle_program")],
        [bot.InlineKeyboardButton(f"⚡ Auto Issue: {'ON ✅' if s.get('auto_issue_enabled') else 'OFF'}", callback_data="v89_reward_toggle_issue")],
        [
            bot.InlineKeyboardButton("💰 Giftport Balance", callback_data="v97_gp_balance"),
            bot.InlineKeyboardButton("🗂 Sync Catalogue", callback_data="v97_gp_catalogue"),
        ],
        [bot.InlineKeyboardButton("🎫 Process Reward Queue", callback_data="v97_process_rewards")],
        [bot.InlineKeyboardButton("🧮 Prepare This Week's Winners", callback_data="v89_reward_settle")],
        [bot.InlineKeyboardButton("📊 Reward Reports", callback_data="v91_reports:engage")],
        [bot.InlineKeyboardButton("⬅️ Rewards & Engagement", callback_data="v91_admincat:rewards")],
    ])


def _catalogue_summary(limit=12):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM reward_provider_catalogue
                WHERE is_active=TRUE
                ORDER BY brand_name LIMIT %s
                """,
                (int(limit),),
            )
            rows = cur.fetchall()
            cur.execute("SELECT COUNT(*) AS n FROM reward_provider_catalogue WHERE is_active=TRUE")
            total = int((cur.fetchone() or {}).get("n") or 0)
    if not rows:
        return "No Giftport catalogue is cached yet."
    lines = [f"🗂 <b>Giftport Catalogue</b> — {total} active brand(s)", ""]
    for r in rows:
        den = html.escape(str(r.get("denominations") or "Variable/unspecified"))
        lines.append(f"• <b>{html.escape(str(r.get('brand_name') or r.get('operator_code')))}</b> — <code>{html.escape(str(r.get('operator_code')))}</code> — {den}")
    if total > len(rows):
        lines.append(f"\n…and {total-len(rows)} more. The full catalogue is cached in the database.")
    return "\n".join(lines)


async def v97_callback_handler(update, context):
    q = update.callback_query
    data = str(q.data or "") if q else ""
    if not q or not data.startswith("v97_"):
        return await _old_callback_handler(update, context)
    if not bot.is_admin(q.from_user.id):
        await q.answer("Admin only", show_alert=True)
        return

    if data == "v97_gp_balance":
        if not _giftport_ready():
            await q.answer("Add Giftport clientId + secretId first.", show_alert=True)
            return
        await q.answer("Checking Giftport wallet…")
        ok, balance, currency = _get_balance()
        text = f"💰 Giftport wallet: <b>₹{balance:,.2f}</b> {currency}" if ok else f"⚠️ Giftport balance check failed: <code>{html.escape(str(currency))}</code>"
        await q.message.reply_text(text, parse_mode=bot.ParseMode.HTML, reply_markup=_reward_center_keyboard())
        return

    if data == "v97_gp_catalogue":
        if not _giftport_ready():
            await q.answer("Add Giftport clientId + secretId first.", show_alert=True)
            return
        await q.answer("Syncing catalogue…")
        ok, message, count = _sync_catalogue()
        if not ok:
            await q.message.reply_text(f"⚠️ {html.escape(message)}", parse_mode=bot.ParseMode.HTML, reply_markup=_reward_center_keyboard())
            return
        await q.message.reply_text(_catalogue_summary(), parse_mode=bot.ParseMode.HTML, reply_markup=_reward_center_keyboard())
        return

    if data == "v97_process_rewards":
        if not _giftport_ready():
            await q.answer("Giftport credentials are not configured.", show_alert=True)
            return
        if not v89._reward_settings().get("auto_issue_enabled"):
            await q.answer("Turn Auto Issue ON first.", show_alert=True)
            return
        await q.answer("Processing reward queue…")
        result = _process_reward_queue(limit=20)
        await q.message.reply_text(
            f"🎫 <b>Reward Queue Processed</b>\n\nChecked: <b>{result['processed']}</b>\nDelivered now: <b>{result['delivered']}</b>",
            parse_mode=bot.ParseMode.HTML,
            reply_markup=_reward_center_keyboard(),
        )
        return


def _v97_prepare_weekly_awards(force=False):
    n = _old_prepare_weekly_awards(force=force)
    try:
        if v89._reward_settings().get("auto_issue_enabled") and _giftport_ready():
            _process_reward_queue(limit=12)
    except Exception:
        bot.logger.exception("V97_POST_WINNER_AUTO_ISSUE_FAILED")
    return n


def _v97_worker_cycle(force=False):
    _old_worker_cycle(force=force)
    try:
        _process_reward_queue(limit=12)
    except Exception:
        bot.logger.exception("V97_REWARD_QUEUE_CYCLE_FAILED")


def _startup_diagnostic():
    try:
        s = v89._reward_settings()
        c = _reward_counts()
        bot.logger.warning(
            "V97_GIFTPORT_DIAGNOSTIC configured=%s base=%s default_operator=%s auto_issue=%s queued=%s",
            "yes" if _giftport_ready() else "no",
            GIFTPORT_API_BASE,
            s.get("default_brand_code"),
            "on" if s.get("auto_issue_enabled") else "off",
            int(c.get("queued") or 0),
        )
    except Exception:
        bot.logger.exception("V97_GIFTPORT_DIAGNOSTIC_FAILED")


try:
    _ensure_v97_schema()
    bot.logger.warning("V97_GIFTPORT_SCHEMA ready=on catalogue_cache=on provider_state=on award_audit=on")
except Exception:
    bot.logger.exception("V97_GIFTPORT_SCHEMA_FAILED")

# Patch V89 globals used by the existing Reward Center callbacks.
v89._giftport_ready = _giftport_ready
v89._reward_center_text = _reward_center_text
v89._reward_center_keyboard = _reward_center_keyboard
v89._prepare_weekly_awards = _v97_prepare_weekly_awards

# V83 worker resolves this global dynamically.
v83._worker_cycle = _v97_worker_cycle
bot.callback_handler = v97_callback_handler

bot.logger.warning(
    "V97_GIFTPORT_LIVE_ADAPTER active=on post_json=on catalogue=on balance=on buy=on order_status=on "
    "idempotent_order_id=on mobile_required=on budget_guards=on low_balance_guard=on retry_delivery=on "
    "network_unknown_reconcile=on real_issue_requires_credentials+auto_issue"
)


if __name__ == "__main__":
    _startup_diagnostic()
    v96._startup_diagnostic()
    v93._startup_pdf_diagnostic()
    v88.v63.apply_signup_cta()
    v85._enable_smart_reply_without_reset()
    threading.Thread(target=v83._worker_loop, name="betroxy-engagement-worker", daemon=True).start()
    bot.logger.warning("V97 polling handover delay=12s")
    time.sleep(12)
    bot.main()
