import json
import threading
import time
from datetime import datetime, timezone

from flask import jsonify, request

import bot
import v98_giftport_refinement as v98

v97 = v98.v97
v96 = v97.v96
v93 = v97.v93
v89 = v97.v89
v88 = v97.v88
v85 = v97.v85
v83 = v97.v83


def _ensure_v99_schema():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS giftport_webhook_events (
                    id BIGSERIAL PRIMARY KEY,
                    order_id TEXT,
                    event_status TEXT,
                    remote_ip TEXT,
                    payload JSONB,
                    verification_status TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
        conn.commit()


def _save_event(order_id, event_status, remote_ip, payload, verification_status):
    try:
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO giftport_webhook_events(order_id,event_status,remote_ip,payload,verification_status)
                    VALUES (%s,%s,%s,%s::jsonb,%s)
                    """,
                    (
                        order_id or None,
                        event_status or None,
                        remote_ip or None,
                        json.dumps(payload or {}, default=str),
                        verification_status,
                    ),
                )
            conn.commit()
    except Exception:
        bot.logger.exception("V99_GIFTPORT_WEBHOOK_AUDIT_FAILED")


def _param(payload, *names):
    for name in names:
        value = payload.get(name)
        if value not in (None, ""):
            return str(value).strip()
    return ""


@bot.tracker_api.route("/giftport/webhook", methods=["GET", "POST"])
def giftport_webhook():
    # Giftport documentation says callbacks are sent as HTTP GET query params.
    # POST is also accepted defensively. Webhook values are NEVER trusted as proof
    # of success: an authenticated Giftport Order Status API call is performed first.
    payload = dict(request.args or {})
    if request.method == "POST":
        body = request.get_json(silent=True)
        if isinstance(body, dict):
            payload.update(body)
        payload.update(dict(request.form or {}))

    order_id = _param(payload, "order_id", "orderId", "orderid", "merchant_order_id")
    event_status = _param(payload, "status", "transaction_status", "order_status")
    remote_ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()

    if not order_id:
        _save_event("", event_status, remote_ip, payload, "missing_order_id")
        return jsonify({"status": "received"}), 200

    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM reward_awards WHERE provider='giftport' AND provider_order_id=%s LIMIT 1",
                (order_id,),
            )
            award = cur.fetchone()

    if not award:
        _save_event(order_id, event_status, remote_ip, payload, "unknown_order")
        return jsonify({"status": "received"}), 200

    ok, verified = v97._giftport_post("status", {"order_id": order_id})
    if not ok:
        _save_event(order_id, event_status, remote_ip, payload, "provider_status_check_failed")
        return jsonify({"status": "received"}), 200

    try:
        v97._store_provider_success(award, verified)
        v97._deliver_award(award["id"])
        verification_status = "provider_verified"
    except Exception:
        verification_status = "provider_verified_delivery_pending"
        bot.logger.exception("V99_GIFTPORT_WEBHOOK_PROCESS_FAILED order_id=%s", order_id)

    _save_event(order_id, event_status, remote_ip, payload, verification_status)
    bot.logger.warning("V99_GIFTPORT_WEBHOOK order_id=%s verification=%s", order_id, verification_status)
    return jsonify({"status": "received"}), 200


try:
    _ensure_v99_schema()
    bot.logger.warning("V99_GIFTPORT_WEBHOOK_SCHEMA ready=on")
except Exception:
    bot.logger.exception("V99_GIFTPORT_WEBHOOK_SCHEMA_FAILED")

bot.logger.warning(
    "V99_GIFTPORT_WEBHOOK active=on path=/giftport/webhook callback_untrusted=on provider_status_verification=required audit=on"
)


if __name__ == "__main__":
    v97._startup_diagnostic()
    v96._startup_diagnostic()
    v93._startup_pdf_diagnostic()
    v88.v63.apply_signup_cta()
    v85._enable_smart_reply_without_reset()
    threading.Thread(target=v83._worker_loop, name="betroxy-engagement-worker", daemon=True).start()
    bot.logger.warning("V99 polling handover delay=12s")
    time.sleep(12)
    bot.main()
