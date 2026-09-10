import json
import os
from datetime import datetime, timezone, timedelta

import requests

import bot
import v75_business_buttons_compatible as v75

# V76 diagnostic entrypoint.
# Runs a controlled Business API compatibility test ONLY against the most recent
# inbound test enquiry whose customer first name is Mohit and message is "Hi".
# This happens before polling starts, so getUpdates conflicts cannot hide the
# actual Telegram Business reply_markup result.

v63 = v75.v63
biz51 = v75.biz51


def _latest_mohit_hi():
    try:
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT e.id, e.connection_id, e.customer_chat_id,
                           e.customer_first_name, e.customer_username,
                           m.message_text, m.created_at
                    FROM telegram_business_messages m
                    JOIN telegram_business_enquiries e ON e.id=m.enquiry_id
                    WHERE m.direction='inbound'
                      AND LOWER(COALESCE(e.customer_first_name,''))='mohit'
                      AND LOWER(TRIM(COALESCE(m.message_text,'')))='hi'
                    ORDER BY m.created_at DESC, m.id DESC
                    LIMIT 1
                    """
                )
                row = cur.fetchone()
        if not row:
            bot.logger.warning("V76_DIAG skipped no recent Mohit/Hi enquiry")
            return None
        created = row.get("created_at")
        if created and (datetime.now(timezone.utc) - created) > timedelta(hours=4):
            bot.logger.warning("V76_DIAG skipped Mohit/Hi enquiry older_than_4h")
            return None
        return row
    except Exception as exc:
        bot.logger.exception("V76_DIAG database lookup failed: %s", exc)
        return None


def _send_raw(row, label, button_url):
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        return False, "BOT_TOKEN missing"
    markup = {
        "inline_keyboard": [[{"text": label, "url": button_url}]]
    }
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={
                "chat_id": str(row["customer_chat_id"]),
                "business_connection_id": str(row["connection_id"]),
                "text": f"BETROXY button compatibility test — {label}",
                "reply_markup": json.dumps(markup),
                "disable_web_page_preview": "true",
            },
            timeout=20,
        )
        payload = r.json() if r.content else {}
        desc = str(payload.get("description") or "")
        ok = bool(r.ok and payload.get("ok"))
        bot.logger.warning(
            "V76_DIAG_RESULT label=%s ok=%s http=%s description=%s",
            label.replace(" ", "_"), ok, r.status_code, desc[:300]
        )
        return ok, desc
    except Exception as exc:
        bot.logger.exception("V76_DIAG request failed label=%s: %s", label, exc)
        return False, str(exc)


def run_business_button_diagnostic():
    row = _latest_mohit_hi()
    if not row:
        return

    # A: known-safe https URL button.
    https_ok, _ = _send_raw(row, "Website", "https://betroxy.com/")

    # B: exact native Mini App deep link that previously worked in normal bot chat.
    mini_ok, mini_err = _send_raw(row, "PLAY NOW", v75.PLAY_NOW_URL)

    # C: HTTPS Direct Mini App URL fallback. This is still a direct Mini App link,
    # not the generic @BetroxyBot chat URL.
    https_mini = "https://t.me/BetroxyBot/populargames?startapp=populargames"
    https_mini_ok, https_mini_err = _send_raw(row, "PLAY NOW HTTPS", https_mini)

    bot.logger.warning(
        "V76_DIAG_SUMMARY website_https=%s tg_miniapp=%s https_miniapp=%s tg_error=%s https_mini_error=%s",
        https_ok, mini_ok, https_mini_ok, (mini_err or "")[:160], (https_mini_err or "")[:160]
    )


bot.logger.warning("V76_BUSINESS_BUTTON_DIAGNOSTIC active=on pre_polling_real_business_test=on")

if __name__ == "__main__":
    v63.apply_signup_cta()
    v63.enable_business_smart_auto_reply()
    run_business_button_diagnostic()
    bot.main()
