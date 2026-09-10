import threading
import time

import bot
import v91_admin_categories_reporting_hub as v91

v90 = v91.v90
v89 = v91.v89
v88 = v91.v88
v85 = v91.v85
v83 = v91.v83


async def v92_ai_command(update, context):
    """Report-aware /ai command without recursive fallback."""
    user = update.effective_user
    msg = update.effective_message
    if not user or not msg:
        return
    if not bot.is_admin(user.id):
        await msg.reply_text(
            "🔒 The BETROXY admin assistant is available only to the configured admin account.\n"
            f"Your Telegram ID is <code>{user.id}</code>.",
            parse_mode=bot.ParseMode.HTML,
        )
        return

    raw = " ".join(getattr(context, "args", []) or []).strip()
    if not raw:
        raw = "ai help"

    if await v91._deliver_ai_report(update, context, raw):
        bot.logger.warning("V92_AI_REPORT uid=%s text=%s", user.id, raw[:180])
        return

    # Preserve V89 conveniences before handing the rest to the older admin parser.
    low = raw.lower()
    if any(x in low for x in ("mobile report", "phone report", "user mobile", "contact report")):
        await v89._send_mobile_report(msg)
        return
    if any(x in low for x in ("reward center", "rewards center", "giftport status", "reward status")):
        await msg.reply_text(
            v89._reward_center_text(),
            parse_mode=bot.ParseMode.HTML,
            reply_markup=v89._reward_center_keyboard(),
        )
        return

    proxy = v89._UpdateProxy(update, raw)
    await v89.v62.ai_admin_chat_handler(proxy, context)


def _startup_reporting_diagnostic():
    try:
        rows, label = v91._url_rows("url_today")
        counts = {}
        for cat in ("customers", "campaigns", "rewards", "affiliates", "system"):
            markup = v91._category_markup(cat)
            counts[cat] = max(0, len(markup.inline_keyboard) - 1)
        bot.logger.warning(
            "V92_REPORT_DIAGNOSTIC url_query=ok period=%s url_rows=%s category_counts=%s",
            label, len(rows), counts,
        )
    except Exception:
        bot.logger.exception("V92_REPORT_DIAGNOSTIC_FAILED")


# V89's Application.add_handler wrapper resolves this at application construction.
v89._ai_command = v92_ai_command

bot.logger.warning(
    "V92_AI_REPORT_ROUTER_HOTFIX active=on urlwise_natural_language=on recursive_fallback=off"
)


if __name__ == "__main__":
    _startup_reporting_diagnostic()
    v88.v63.apply_signup_cta()
    v85._enable_smart_reply_without_reset()
    threading.Thread(target=v83._worker_loop, name="betroxy-engagement-worker", daemon=True).start()
    bot.logger.warning("V92 polling handover delay=12s")
    time.sleep(12)
    bot.main()
