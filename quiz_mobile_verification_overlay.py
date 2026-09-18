"""BETROXY Daily Quiz mobile verification overlay.

Keeps the locked quiz implementation intact while enforcing:
- a verified Telegram-linked mobile before Daily Quiz participation;
- international mobile numbers (not India-only) for quiz verification;
- fail-closed verification based on a dedicated audit table.

The existing India-only reward/payment rules are intentionally left unchanged.
"""
import re

from telegram import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, ApplicationHandlerStop, MessageHandler, filters

_installed = False
_installed_apps = set()
_previous_add_handler = None
_v110 = None
_bot = None
_v105 = None


def _normalize_mobile(value):
    digits = re.sub(r"\D+", "", str(value or ""))
    # E.164 permits up to 15 digits. Require a country code and a sensible
    # minimum length; request_contact normally supplies the country code.
    if not re.fullmatch(r"[1-9]\d{7,14}", digits):
        return None
    return "+" + digits


def _ensure_schema():
    with _bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS v110_mobile_verifications (
                    telegram_user_id BIGINT PRIMARY KEY,
                    mobile_number TEXT NOT NULL,
                    verified_via TEXT NOT NULL DEFAULT 'telegram_contact',
                    verified_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
        conn.commit()


def _verification_row(uid):
    try:
        with _bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM v110_mobile_verifications WHERE telegram_user_id=%s",
                    (int(uid),),
                )
                return cur.fetchone() or {}
    except Exception:
        _bot.logger.exception("QUIZ_MOBILE_VERIFY lookup_failed uid=%s", uid)
        return {}


def _verified_mobile(uid):
    """Return the user's verified quiz mobile, otherwise None.

    Verification is valid only when the currently stored contact number still
    matches the number that was verified by Telegram contact sharing.
    """
    try:
        profile = _v110.v89._mobile_row(uid) or {}
        current = _normalize_mobile(profile.get("mobile_number"))
        verified = _normalize_mobile((_verification_row(uid) or {}).get("mobile_number"))
        if current and verified and current == verified:
            return verified
    except Exception:
        _bot.logger.exception("QUIZ_MOBILE_VERIFY current_mobile_check_failed uid=%s", uid)
    return None


def _masked_mobile(uid):
    value = _verified_mobile(uid)
    if not value:
        return "Not verified"
    digits = re.sub(r"\D+", "", value)
    return "+••••••" + digits[-4:]


def _verification_markup():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📱 Verify My Mobile", request_contact=True)],
            [KeyboardButton("Cancel")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


async def _registration_prompt(msg, uid, campaign_id, purpose="quiz_register"):
    # Do not accept typed numbers for quiz verification. Telegram contact sharing
    # binds the contact to the Telegram user and is the verification step.
    flow = "change_mobile_verify" if purpose == "change_mobile" else "quiz_verify_mobile"
    _v110._clear_mobile_prompt_recency(uid)
    _v110._set_session(
        uid,
        campaign_id=int(campaign_id),
        flow_state=flow,
        entry_id=None,
        current_question_id=None,
        question_sent_at=None,
    )
    await msg.reply_text(
        "📱 <b>Mobile verification required</b>\n\n"
        "To enter the Daily Quiz, verify the mobile number linked to your Telegram account.\n\n"
        "✅ Numbers from <b>any country</b> are accepted.\n"
        "🔒 Tap <b>Verify My Mobile</b> below. Typing a number manually does not count as verification.",
        parse_mode=_bot.ParseMode.HTML,
        reply_markup=_verification_markup(),
    )


def _save_verification(uid, mobile):
    normalized = _normalize_mobile(mobile)
    if not normalized:
        return None

    # v105 keeps the pre-India-restriction saver as _old_save_mobile. Use it
    # only for the quiz verification path so the global reward flow remains
    # untouched.
    saved = _v105._old_save_mobile(
        int(uid),
        normalized,
        "v110_quiz_verified_telegram_contact",
    )
    if not saved:
        return None

    stored = _normalize_mobile(saved.get("mobile_number") if isinstance(saved, dict) else normalized)
    if not stored:
        stored = normalized

    with _bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO v110_mobile_verifications(
                    telegram_user_id, mobile_number, verified_via, verified_at, updated_at
                ) VALUES (%s,%s,'telegram_contact',NOW(),NOW())
                ON CONFLICT(telegram_user_id) DO UPDATE SET
                    mobile_number=EXCLUDED.mobile_number,
                    verified_via='telegram_contact',
                    verified_at=NOW(),
                    updated_at=NOW()
                """,
                (int(uid), stored),
            )
        conn.commit()
    return stored


async def _verified_contact_handler(update, context):
    user = update.effective_user
    msg = update.effective_message
    chat = update.effective_chat
    contact = getattr(msg, "contact", None) if msg else None

    if not user or not msg or not contact or not chat or str(getattr(chat, "type", "")) != "private":
        return

    state = _v110._session(user.id) or {}
    flow = str(state.get("flow_state") or "")
    if flow not in {"quiz_verify_mobile", "change_mobile_verify"}:
        # Not our flow: allow the existing contact handlers to process it.
        return

    contact_uid = getattr(contact, "user_id", None)
    if contact_uid is None or int(contact_uid) != int(user.id):
        await msg.reply_text(
            "❌ Verification failed. Please tap <b>Verify My Mobile</b> and share your own Telegram-linked number.",
            parse_mode=_bot.ParseMode.HTML,
            reply_markup=_verification_markup(),
        )
        raise ApplicationHandlerStop

    normalized = _normalize_mobile(getattr(contact, "phone_number", ""))
    if not normalized:
        await msg.reply_text(
            "❌ I could not verify that phone format. Please use the <b>Verify My Mobile</b> button again.",
            parse_mode=_bot.ParseMode.HTML,
            reply_markup=_verification_markup(),
        )
        raise ApplicationHandlerStop

    saved = _save_verification(user.id, normalized)
    if not saved:
        await msg.reply_text(
            "❌ Could not save the verified mobile. Please try again.",
            reply_markup=_verification_markup(),
        )
        raise ApplicationHandlerStop

    campaign_id = int(
        state.get("campaign_id")
        or _v110._today_campaign(test_mode=not _v110.PUBLIC_ENABLED)["id"]
    )
    masked = _masked_mobile(user.id)
    _bot.logger.warning(
        "QUIZ_MOBILE_VERIFIED uid=%s username=%s country_any=on source=telegram_contact",
        user.id,
        user.username,
    )

    if flow == "change_mobile_verify":
        _v110._set_session(user.id, flow_state=None)
        await msg.reply_text(
            f"✅ <b>Mobile verified and updated</b>\n\n{masked}",
            parse_mode=_bot.ParseMode.HTML,
            reply_markup=ReplyKeyboardRemove(),
        )
        raise ApplicationHandlerStop

    _v110._set_session(user.id, flow_state="awaiting_consent")
    await msg.reply_text(
        f"✅ <b>Mobile verified</b>\n\n{masked}\n\nYou can now continue to the Daily Quiz.",
        parse_mode=_bot.ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove(),
    )
    await _v110._consent_prompt(msg, user.id, campaign_id)
    raise ApplicationHandlerStop


def _install_contact_handler():
    global _previous_add_handler
    if _previous_add_handler is not None:
        return

    _previous_add_handler = Application.add_handler

    def _wrapped_add_handler(self, handler, group=0):
        app_id = id(self)
        if app_id not in _installed_apps:
            _installed_apps.add(app_id)
            # Run before V105's India-only contact handler (-120).
            _previous_add_handler(
                self,
                MessageHandler(
                    filters.CONTACT & ~filters.UpdateType.BUSINESS_MESSAGE,
                    _verified_contact_handler,
                ),
                group=-121,
            )
        return _previous_add_handler(self, handler, group=group)

    Application.add_handler = _wrapped_add_handler


def install(v110, bot_module):
    global _installed, _v110, _bot, _v105
    if _installed:
        return

    _v110 = v110
    _bot = bot_module
    _v105 = v110.v105

    _ensure_schema()

    # These names are resolved dynamically by the locked V110 callbacks and
    # deeplink/menu layers, so replacing them enforces verification everywhere
    # those routes already call the quiz mobile gate.
    v110._mobile = _verified_mobile
    v110._masked_mobile = _masked_mobile
    v110._registration_prompt = _registration_prompt

    _install_contact_handler()

    _installed = True
    _bot.logger.warning(
        "QUIZ_MOBILE_VERIFICATION active=on mandatory=on country_any=on "
        "verification=telegram_contact manual_entry=off locked_v110_unchanged=on"
    )
