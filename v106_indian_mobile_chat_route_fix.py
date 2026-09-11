import re
import threading
import time
from datetime import datetime, timezone

import bot
import v105_indian_mobile_rewards as v105

from telegram import ReplyKeyboardRemove

v104 = v105.v104
v103 = v105.v103
v102 = v105.v102
v101 = v105.v101
v100 = v105.v100
v99 = v105.v99
v98 = v105.v98
v97 = v105.v97
v96 = v105.v96
v93 = v105.v93
v89 = v105.v89
v88 = v105.v88
v85 = v105.v85
v83 = v105.v83

_old_chat_handler = bot.chat_handler


def _mobile_flow_recent(uid):
    row = v89._mobile_row(uid) or {}
    prompted_at = row.get("mobile_prompted_at")
    if prompted_at is None:
        return False
    try:
        p = prompted_at if getattr(prompted_at, "tzinfo", None) else prompted_at.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - p).total_seconds() <= 1800
    except Exception:
        return False


def _compact_phone(text):
    return str(text or "").strip().replace(" ", "").replace("-", "")


async def v106_chat_handler(update, context):
    user = update.effective_user
    msg = update.effective_message
    chat = update.effective_chat

    if user and msg and chat and str(getattr(chat, "type", "")) == "private":
        text = str(getattr(msg, "text", "") or "").strip()
        compact = _compact_phone(text)
        phone_like = compact.startswith("+") or compact.isdigit()

        if phone_like and _mobile_flow_recent(user.id):
            if not v105._is_indian_mobile(compact):
                await msg.reply_text(
                    "❌ <b>Only an Indian +91 mobile is accepted for rewards.</b>\n\n"
                    "Please enter it exactly as <code>+91XXXXXXXXXX</code> — 10 digits after +91.",
                    parse_mode=bot.ParseMode.HTML,
                    reply_markup=v105._mobile_entry_markup(),
                )
                bot.logger.warning(
                    "V106_INDIA_MOBILE rejected uid=%s username=%s reason=invalid_or_non_india",
                    user.id, user.username,
                )
                return

            saved = v105._save_indian_mobile(user.id, compact, "officialbot_manual_india_v106")
            if not saved:
                await msg.reply_text(
                    "❌ Could not save that number. Please enter a valid Indian mobile as +91XXXXXXXXXX.",
                    reply_markup=v105._mobile_entry_markup(),
                )
                bot.logger.warning(
                    "V106_INDIA_MOBILE save_failed uid=%s username=%s",
                    user.id, user.username,
                )
                return

            digits = re.sub(r"\D+", "", str(saved.get("mobile_number") or ""))
            masked = "+91••••••" + digits[-4:]
            await msg.reply_text(
                f"✅ <b>Indian mobile verified for rewards</b>\n\n{masked}\n\n"
                "Your +91 number is saved. You can now continue with eligible INR rewards.",
                parse_mode=bot.ParseMode.HTML,
                reply_markup=ReplyKeyboardRemove(),
            )
            bot.logger.warning(
                "V106_INDIA_MOBILE saved uid=%s username=%s source=manual_chat_wrapper",
                user.id, user.username,
            )
            return

    return await _old_chat_handler(update, context)


# Robust fix: bot.main() resolves bot.chat_handler when it installs the generic
# text MessageHandler, so patching this function directly guarantees typed +91
# input reaches reward-mobile validation before the normal Welcome-back flow.
bot.chat_handler = v106_chat_handler


def _startup_selftest():
    valid = v105._is_indian_mobile("+918962697659")
    reject_uae = not v105._is_indian_mobile("+971509430642")
    reject_short = not v105._is_indian_mobile("+91896269")
    wrapper = bot.chat_handler is v106_chat_handler
    bot.logger.warning(
        "V106_SELFTEST india_valid=%s non_india_rejected=%s short_rejected=%s chat_wrapper_installed=%s",
        valid, reject_uae, reject_short, wrapper,
    )
    if not all([valid, reject_uae, reject_short, wrapper]):
        raise RuntimeError("V106 Indian mobile routing self-test failed")


bot.logger.warning(
    "V106_INDIA_MOBILE_CHAT_ROUTE_FIX active=on generic_chat_intercept=on format=+91XXXXXXXXXX"
)


if __name__ == "__main__":
    _startup_selftest()
    v97._startup_diagnostic()
    v96._startup_diagnostic()
    v93._startup_pdf_diagnostic()
    v101._phonepe_probe()
    v104._startup_mobile_diagnostic()
    v88.v63.apply_signup_cta()
    v85._enable_smart_reply_without_reset()
    threading.Thread(target=v83._worker_loop, name="betroxy-engagement-worker", daemon=True).start()
    bot.logger.warning("V106 polling handover delay=12s")
    time.sleep(12)
    bot.main()
