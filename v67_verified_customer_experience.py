import json
import os
import time
import requests

import bot
import v66_smart_customer_experience as v66

# V67 - verified production entrypoint for the V66 customer experience.
# Adds a small deploy-transition delay and a startup smoke test.

v63 = v66.v63


def verify_customer_ui():
    markup = v66._main_keyboard()
    rows = markup.inline_keyboard
    labels = [button.text for row in rows for button in row]
    urls = [button.url for row in rows for button in row if getattr(button, "url", None)]

    required_labels = {
        "🔵 Deposit",
        "🟠 Withdrawal",
        "🟣 Bonus",
        "🟢 Support",
        "🟡 Sports",
        "🔴 Casino",
        "🟢 PLAY NOW",
        "🌐 SIGN UP ON WEBSITE",
    }
    missing = required_labels.difference(labels)
    if missing:
        raise RuntimeError(f"V67 customer UI smoke test failed. Missing buttons: {sorted(missing)}")
    if v66.PRODUCT_BOT_URL not in urls:
        raise RuntimeError("V67 smoke test failed: PLAY NOW product bot URL missing")
    if v66.WEBSITE_URL not in urls:
        raise RuntimeError("V67 smoke test failed: website URL missing")
    if v66.SUPPORT_URL not in urls:
        raise RuntimeError("V67 smoke test failed: support URL missing")

    print("V67_CUSTOMER_UI_SMOKE_TEST SUCCESS labels=8 destinations=verified", flush=True)
    bot.logger.warning("V67_CUSTOMER_UI_SMOKE_TEST SUCCESS labels=8 destinations=verified")


def telegram_deploy_test():
    token = os.getenv("BOT_TOKEN", "").strip()
    admin_id = os.getenv("ADMIN_ID", "").strip()
    if not token or not admin_id:
        bot.logger.warning("V67_TELEGRAM_TEST skipped BOT_TOKEN/ADMIN_ID missing")
        return

    keyboard = {
        "inline_keyboard": [
            [{"text": "🟢 PLAY NOW", "url": v66.PRODUCT_BOT_URL}],
            [{"text": "🌐 SIGN UP ON WEBSITE", "url": v66.WEBSITE_URL}],
        ]
    }
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={
                "chat_id": admin_id,
                "text": "✅ BETROXY Smart Customer Experience deployed\n\nV67 smoke test passed. Customer buttons and destinations are active.",
                "reply_markup": json.dumps(keyboard),
                "disable_web_page_preview": "true",
            },
            timeout=20,
        )
        payload = response.json() if response.content else {}
        if response.ok and payload.get("ok"):
            print("V67_TELEGRAM_DEPLOY_TEST SUCCESS", flush=True)
            bot.logger.warning("V67_TELEGRAM_DEPLOY_TEST SUCCESS")
        else:
            raise RuntimeError(f"Telegram test failed status={response.status_code} response={payload}")
    except Exception as exc:
        bot.logger.exception("V67_TELEGRAM_DEPLOY_TEST FAILED: %s", exc)


if __name__ == "__main__":
    print("V67_VERIFIED_CUSTOMER_EXPERIENCE active=on", flush=True)
    verify_customer_ui()

    # Railway can overlap old/new containers briefly during a rolling deploy.
    # Give the previous Telegram long-poll request time to terminate.
    time.sleep(6)

    v63.apply_signup_cta()
    v63.enable_business_smart_auto_reply()
    telegram_deploy_test()
    bot.main()
