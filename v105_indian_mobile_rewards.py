import re
import threading
import time
from datetime import datetime, timezone

import bot
import v104_mobile_capture_fix as v104

from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton
from telegram.ext import Application, ApplicationHandlerStop, MessageHandler, filters

v103 = v104.v103
v102 = v104.v102
v101 = v104.v101
v100 = v104.v100
v99 = v104.v99
v98 = v104.v98
v97 = v104.v97
v96 = v104.v96
v93 = v104.v93
v89 = v104.v89
v88 = v104.v88
v85 = v104.v85
v83 = v104.v83

_old_add_handler = Application.add_handler
_old_callback_handler = bot.callback_handler
_old_chat_handler = bot.chat_handler
_old_save_mobile = v89._save_mobile
_installed_apps = set()

INDIA_MOBILE_RE = re.compile(r"^\+91[6-9]\d{9}$")


def _is_indian_mobile(value):
    return bool(INDIA_MOBILE_RE.fullmatch(str(value or "").strip()))


def _save_indian_mobile(uid, raw, source="officialbot_indian_mobile"):
    value = str(raw or "").strip().replace(" ", "").replace("-", "")
    if not _is_indian_mobile(value):
        return None
    return _old_save_mobile(uid, value, source)


# India-only reward mobile rule.
v89._save_mobile = _save_indian_mobile


def _mobile_entry_markup():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📱 Share My Mobile", request_contact=True)], [KeyboardButton("Cancel")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


async def _send_india_mobile_prompt(msg, uid):
    v89._mark_mobile_prompted(uid)
    await msg.reply_text(
        "📱 <b>Indian mobile required for rewards</b>\n\n"
        "Enter your Indian mobile in this exact format:\n"
        "<code>+91XXXXXXXXXX</code>\n\n"
        "The number after +91 must contain exactly 10 digits. You can also tap <b>Share My Mobile</b> if your Telegram-linked number is an Indian +91 number.",
        parse_mode=bot.ParseMode.HTML,
        reply_markup=_mobile_entry_markup(),
    )


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


async def _save_typed_indian_mobile(update):
    user = update.effective_user
    msg = update.effective_message
    if not user or not msg:
        return False

    text = str(getattr(msg, "text", "") or "").strip()
    compact = text.replace(" ", "").replace("-", "")
    phone_like = compact.startswith("+") or compact.isdigit()
    if not phone_like or not _mobile_flow_recent(user.id):
        return False

    if not _is_indian_mobile(compact):
        await msg.reply_text(
            "❌ <b>Only an Indian +91 mobile is accepted for rewards.</b>\n\n"
            "Please enter it exactly as <code>+91XXXXXXXXXX</code> — 10 digits after +91.",
            parse_mode=bot.ParseMode.HTML,
            reply_markup=_mobile_entry_markup(),
        )
        bot.logger.warning("V105_INDIA_MOBILE rejected uid=%s username=%s source=manual", user.id, user.username)
        return True

    saved = _save_indian_mobile(user.id, compact, "officialbot_manual_india_v105")
    if not saved:
        await msg.reply_text(
            "❌ Could not save that number. Please enter a valid Indian mobile as +91XXXXXXXXXX.",
            reply_markup=_mobile_entry_markup(),
        )
        return True

    digits = re.sub(r"\D+", "", str(saved.get("mobile_number") or ""))
    masked = "+91••••••" + digits[-4:]
    await msg.reply_text(
        f"✅ <b>Indian mobile verified for rewards</b>\n\n{masked}\n\n"
        "Your +91 number is saved. You can now continue with eligible INR rewards.",
        parse_mode=bot.ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove(),
    )
    bot.logger.warning("V105_INDIA_MOBILE saved uid=%s username=%s source=manual_chat", user.id, user.username)
    return True


# Robust text route: bot.main installs bot.chat_handler for all ordinary text.
# Intercept a recently prompted reward-mobile entry before the generic Welcome-back handler.
async def v105_chat_handler(update, context):
    user = update.effective_user
    msg = update.effective_message
    chat = update.effective_chat
    if user and msg and chat and str(getattr(chat, "type", "")) == "private":
        if await _save_typed_indian_mobile(update):
            return
    return await _old_chat_handler(update, context)


async def _typed_indian_mobile_handler(update, context):
    if await _save_typed_indian_mobile(update):
        raise ApplicationHandlerStop


async def _indian_contact_handler(update, context):
    user = update.effective_user
    msg = update.effective_message
    chat = update.effective_chat
    contact = getattr(msg, "contact", None) if msg else None
    if not user or not msg or not contact or not chat or str(getattr(chat, "type", "")) != "private":
        return

    contact_uid = getattr(contact, "user_id", None)
    if contact_uid is not None and int(contact_uid) != int(user.id):
        await msg.reply_text("❌ Please share your own mobile number.", reply_markup=ReplyKeyboardRemove())
        raise ApplicationHandlerStop

    raw = str(getattr(contact, "phone_number", "") or "").strip()
    digits = re.sub(r"\D+", "", raw)
    normalized = "+" + digits if digits.startswith("91") else raw
    if not _is_indian_mobile(normalized):
        await msg.reply_text(
            "❌ <b>This is not an Indian +91 mobile.</b>\n\n"
            "For Giftport rewards, please type your Indian number manually as <code>+91XXXXXXXXXX</code>.",
            parse_mode=bot.ParseMode.HTML,
            reply_markup=ReplyKeyboardRemove(),
        )
        raise ApplicationHandlerStop

    saved = _save_indian_mobile(user.id, normalized, "officialbot_contact_india_v105")
    digits = re.sub(r"\D+", "", str((saved or {}).get("mobile_number") or ""))
    masked = "+91••••••" + digits[-4:]
    await msg.reply_text(
        f"✅ <b>Indian mobile verified for rewards</b>\n\n{masked}",
        parse_mode=bot.ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove(),
    )
    bot.logger.warning("V105_INDIA_MOBILE saved uid=%s username=%s source=contact", user.id, user.username)
    raise ApplicationHandlerStop


async def v105_callback_handler(update, context):
    q = update.callback_query
    data = str(q.data or "") if q else ""
    if q and data == "v89_mobile":
        await q.answer()
        row = v89._mobile_row(q.from_user.id) or {}
        current = str(row.get("mobile_number") or "").strip()
        if not _is_indian_mobile(current):
            await _send_india_mobile_prompt(q.message, q.from_user.id)
            return
        return await _old_callback_handler(update, context)
    return await _old_callback_handler(update, context)


def _v105_add_handler(self, handler, group=0):
    app_id = id(self)
    if app_id not in _installed_apps:
        _installed_apps.add(app_id)
        _old_add_handler(
            self,
            MessageHandler(filters.CONTACT & ~filters.UpdateType.BUSINESS_MESSAGE, _indian_contact_handler),
            group=-120,
        )
        _old_add_handler(
            self,
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.UpdateType.BUSINESS_MESSAGE, _typed_indian_mobile_handler),
            group=-119,
        )
    return _old_add_handler(self, handler, group=group)


def _startup_selftest():
    valid = _is_indian_mobile("+919876543210")
    reject_uae = not _is_indian_mobile("+971509430642")
    reject_short = not _is_indian_mobile("+91987654")
    wrapper = bot.chat_handler is v105_chat_handler
    bot.logger.warning(
        "V105_SELFTEST india_valid=%s non_india_rejected=%s short_rejected=%s chat_wrapper_installed=%s",
        valid, reject_uae, reject_short, wrapper,
    )
    if not all([valid, reject_uae, reject_short, wrapper]):
        raise RuntimeError("V105 Indian mobile routing self-test failed")


Application.add_handler = _v105_add_handler
bot.callback_handler = v105_callback_handler
bot.chat_handler = v105_chat_handler
v97._reward_center_keyboard = v102._v102_reward_center_keyboard
v89._reward_center_keyboard = v102._v102_reward_center_keyboard

bot.logger.warning("V105_INDIA_MOBILE_REWARDS active=on format=+91XXXXXXXXXX manual_entry=on direct_chat_route=on contact_share_india_only=on")


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
    bot.logger.warning("V105 polling handover delay=12s")
    time.sleep(12)
    bot.main()
