"""Persistent, account-wide QUIZ/FULL switch for the OfficialBot.

This controls the OfficialBot only. It cannot revoke an already delivered URL
button or change the separate @BetroxyBot Mini App or public website.
"""

import asyncio
import functools
import inspect
import logging
import re
import threading
import time
from urllib.parse import parse_qs, urlparse


_lock = threading.RLock()
_mode = "full"  # Startup loads the persisted value before polling begins.
_bot = None
_installed = False
_mode_checked_at = 0.0
_mode_refresh_failed = False
_reconcile_generation = 0
_reconcile_status = {"mode": "full", "state": "idle", "checked": 0, "updated": 0, "skipped": 0, "failed": 0}
MENU_REFRESH_PAGE_SIZE = 200
MENU_REFRESH_INTERVAL_SECONDS = 0.2
MODE_CACHE_SECONDS = 1.0
MODE_DB_TIMEOUT_SECONDS = 2
_LOG = logging.getLogger(__name__)

QUIZ_HOME_TEXT = (
    "🏆 <b>BETROXY Quiz</b>\n\n"
    "Play the daily sports quiz or practise with no prizes. "
    "Open the official quiz result after it closes."
)
_NON_QUIZ_WORDS = re.compile(
    r"\b(?:bet|bets|betting|casino|gambling|sportsbook|wager|deposit|"
    r"withdrawals?|odds|bonus|bonuses|promotions?|affiliate|referrals?|RTP)\b"
    r"|\b(?:open|explore)\s+betroxy\b",
    re.IGNORECASE,
)
_URL = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
_PRODUCT_DESTINATION = re.compile(
    r"(?<!\w)@?betroxybot\b|(?<![\w.])(?:[a-z0-9-]+\.)*(?:betroxy|batraxy)\.com\b",
    re.IGNORECASE,
)


def clean_quiz_text(value):
    """Return None when an outbound QUIZ message contains product promotion."""
    if not is_quiz():
        return value
    text = str(value or "")
    text = re.sub(
        r"Free to participate\s*[—–-]\s*no deposit or wager required\.?",
        "Entry is free.", text, flags=re.IGNORECASE,
    )
    text = re.sub(
        r"Free entry\s*[—–-]\s*no deposit or wager required\.?",
        "Entry is free.", text, flags=re.IGNORECASE,
    )
    if _NON_QUIZ_WORDS.search(text) or _PRODUCT_DESTINATION.search(text):
        return None
    for raw in _URL.findall(text):
        parsed = urlparse(raw.rstrip(".,;!?)"))
        payload = parse_qs(parsed.query).get("start", [""])[0].lower()
        if not (
            parsed.scheme == "https" and parsed.netloc.lower() == "t.me"
            and parsed.path.strip("/").lower() == "betroxyofficialbot"
            and payload in {"dailyquiz", "megaquiz", "sundaymega"}
        ):
            return None
    return text


def clean_quiz_reward_text(value):
    """Keep actual quiz voucher delivery without linking to the product app."""
    if not is_quiz():
        return value
    text = str(value or "")
    if not text.startswith("🎉 <b>BETROXY Reward Delivered</b>"):
        return None
    if _NON_QUIZ_WORDS.search(text) or _PRODUCT_DESTINATION.search(text):
        return None
    text = re.sub(
        r" You can also find (?:it|them) later under 🎁 My Rewards\.",
        "", text,
    )
    for raw in _URL.findall(text):
        parsed = urlparse(raw.rstrip(".,;!?)"))
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not host or host == "betroxy.com" or host.endswith(".betroxy.com") or host == "t.me":
            return None
    return text


def _row_mode(row):
    return str(row.get("mode") if isinstance(row, dict) else row[0]).lower()


def initialize(bot_module):
    """Load durable state before customer workers or polling can start."""
    global _bot, _mode, _mode_checked_at, _mode_refresh_failed
    with _lock:
        with bot_module.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """CREATE TABLE IF NOT EXISTS betroxy_officialbot_mode (
                           singleton SMALLINT PRIMARY KEY CHECK (singleton = 1),
                           mode TEXT NOT NULL CHECK (mode IN ('quiz', 'full')),
                           changed_by BIGINT,
                           changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                       )"""
                )
                cur.execute(
                    """INSERT INTO betroxy_officialbot_mode (singleton, mode)
                       VALUES (1, 'full') ON CONFLICT (singleton) DO NOTHING"""
                )
                cur.execute("SELECT mode FROM betroxy_officialbot_mode WHERE singleton=1")
                row = cur.fetchone()
                if not row or _row_mode(row) not in {"quiz", "full"}:
                    raise RuntimeError("BETROXY mode state is missing or invalid")
            conn.commit()
        _bot = bot_module
        _mode = _row_mode(row)
        _mode_checked_at = time.monotonic()
        _mode_refresh_failed = False
    return _mode


def current_mode():
    """Read through Postgres so overlapping workers share the same switch.

    A bounded cache avoids a query for every menu button or quiz send. If the
    mode store cannot be read, this worker stays in QUIZ until it recovers.
    """
    global _mode, _mode_checked_at, _mode_refresh_failed
    now = time.monotonic()
    if _bot is None or now - _mode_checked_at < MODE_CACHE_SECONDS:
        return _mode
    with _lock:
        now = time.monotonic()
        if now - _mode_checked_at < MODE_CACHE_SECONDS:
            return _mode
        try:
            with _bot.get_db(connect_timeout=MODE_DB_TIMEOUT_SECONDS) as conn:
                with conn.cursor() as cur:
                    cur.execute("SET LOCAL statement_timeout = 2000")
                    cur.execute("SELECT mode FROM betroxy_officialbot_mode WHERE singleton=1")
                    row = cur.fetchone()
            value = _row_mode(row) if row else ""
            if value not in {"quiz", "full"}:
                raise RuntimeError("BETROXY mode state is missing or invalid")
        except Exception as exc:
            if not _mode_refresh_failed:
                _LOG.error("BETROXY_MODE_REFRESH_FAILED fallback=quiz error_type=%s", type(exc).__name__)
            _mode = "quiz"
            _mode_refresh_failed = True
        else:
            if _mode_refresh_failed:
                _LOG.warning("BETROXY_MODE_REFRESH_RECOVERED mode=%s", value)
            _mode = value
            _mode_refresh_failed = False
        _mode_checked_at = time.monotonic()
        return _mode


def is_quiz():
    return current_mode() == "quiz"


def set_mode(value, admin_id):
    """Commit first, then switch the in-process routing state."""
    global _mode, _mode_checked_at, _mode_refresh_failed
    value = str(value or "").lower()
    if value not in {"quiz", "full"}:
        raise ValueError("mode must be quiz or full")
    with _lock:
        if _bot is None:
            raise RuntimeError("BETROXY mode store has not initialized")
        try:
            with _bot.get_db(connect_timeout=MODE_DB_TIMEOUT_SECONDS) as conn:
                with conn.cursor() as cur:
                    cur.execute("SET LOCAL statement_timeout = 2000")
                    cur.execute(
                        """UPDATE betroxy_officialbot_mode
                           SET mode=%s, changed_by=%s, changed_at=NOW()
                           WHERE singleton=1 RETURNING mode""",
                        (value, int(admin_id)),
                    )
                    row = cur.fetchone()
                    if not row or _row_mode(row) != value:
                        raise RuntimeError("BETROXY mode update was not persisted")
                conn.commit()
        except Exception as exc:
            if not _mode_refresh_failed:
                _LOG.error("BETROXY_MODE_UPDATE_FAILED fallback=quiz error_type=%s", type(exc).__name__)
            _mode = "quiz"
            _mode_checked_at = time.monotonic()
            _mode_refresh_failed = True
            raise
        _mode = value
        _mode_checked_at = time.monotonic()
        _mode_refresh_failed = False
    return value


def clean_menu():
    return _bot.InlineKeyboardMarkup([
        [_bot.InlineKeyboardButton("🏆 Daily Quiz", callback_data="compact_daily_quiz")],
        [_bot.InlineKeyboardButton("🎯 Practice Quiz · No prizes", callback_data="btx_practice")],
        [_bot.InlineKeyboardButton("📖 Answer Review", callback_data="btx_review")],
    ])


def clean_business_menu():
    return _bot.InlineKeyboardMarkup([[
        _bot.InlineKeyboardButton(
            "🏆 Daily Quiz", url=f"https://t.me/{_bot.BOT_USERNAME}?start=dailyquiz"
        )
    ]])


def strip_external_rows(rows):
    """Keep quiz actions and official quiz deep links, never product links."""
    if not is_quiz() or not rows:
        return rows

    def safe(button):
        if not isinstance(button, dict) or button.get("web_app"):
            return False
        # A blocked callback can still leak its label in an already sent
        # keyboard. Screen the label as well as the destination.
        if clean_quiz_text(button.get("text")) is None:
            return False
        if button.get("callback_data") and not button.get("url"):
            return _is_quiz_callback(str(button["callback_data"]))
        try:
            parsed = urlparse(str(button.get("url") or ""))
            payload = parse_qs(parsed.query).get("start", [""])[0].lower()
            return (
                parsed.scheme == "https" and parsed.netloc.lower() == "t.me"
                and parsed.path.strip("/").lower() == "betroxyofficialbot"
                and payload in {"dailyquiz", "megaquiz", "sundaymega"}
            )
        except (TypeError, ValueError):
            return False

    return [[dict(button) for button in row if safe(button)]
            for row in rows if any(safe(button) for button in row)]


def _is_admin(update):
    user = getattr(update, "effective_user", None)
    chat = getattr(update, "effective_chat", None)
    return bool(
        user and chat and getattr(chat, "type", None) == "private"
        and int(getattr(user, "id", 0)) == int(_bot.ADMIN_ID)
    )


def _admin_menu():
    return _bot.InlineKeyboardMarkup([
        [_bot.InlineKeyboardButton("🏆 QUIZ", callback_data="btx_mode:quiz")],
        [_bot.InlineKeyboardButton("🔓 FULL", callback_data="btx_mode:full")],
        [_bot.InlineKeyboardButton("📊 Status", callback_data="btx_mode:status")],
    ])


def _user_id_page(after_uid):
    """Keyset-page all known private users who may have a menu override."""
    with _bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT telegram_user_id FROM ("
                " SELECT telegram_user_id FROM v110_mobile_verifications"
                " UNION"
                " SELECT telegram_user_id FROM intelligence_leads WHERE reachable_bot=TRUE"
                ") known WHERE telegram_user_id>%s "
                "ORDER BY telegram_user_id LIMIT %s",
                (int(after_uid), MENU_REFRESH_PAGE_SIZE),
            )
            rows = cur.fetchall()
    return [int(row["telegram_user_id"] if isinstance(row, dict) else row[0]) for row in rows]


async def _reconcile_menu_buttons(telegram_bot, mode, generation):
    """Best effort across all known chats, bounded by page and call rate."""
    from telegram import MenuButtonCommands, MenuButtonWebApp, WebAppInfo
    import betroxy_universal_verification as verification

    status = _reconcile_status
    try:
        cursor = 0
        while True:
            if generation != _reconcile_generation or current_mode() != mode:
                status["state"] = "superseded"
                return
            ids = await asyncio.to_thread(_user_id_page, cursor)
            if not ids:
                break
            for uid in ids:
                if generation != _reconcile_generation or current_mode() != mode:
                    status["state"] = "superseded"
                    return
                cursor = uid
                status["checked"] += 1
                if uid <= 0:
                    status["skipped"] += 1
                    continue
                # A revoked account may still have an old per-chat WebApp menu.
                # Clear it in either mode; only currently verified users regain
                # the WebApp menu in FULL.
                verified = (mode == "full" and
                            await asyncio.to_thread(verification.is_verified, uid))
                menu = (MenuButtonWebApp(text="Open App", web_app=WebAppInfo(url=_bot.APP_URL))
                        if verified else MenuButtonCommands())
                for attempt in range(2):
                    try:
                        await telegram_bot.set_chat_menu_button(chat_id=uid, menu_button=menu)
                        status["updated"] += 1
                        break
                    except Exception as exc:
                        if attempt == 0 and getattr(exc, "retry_after", None) is not None:
                            delay = getattr(exc, "retry_after")
                            if hasattr(delay, "total_seconds"):
                                delay = delay.total_seconds()
                            await asyncio.sleep(min(30.0, max(1.0, float(delay))))
                            continue
                        _bot.logger.warning(
                            "BETROXY_MODE_MENU_REFRESH_FAILED uid=%s reason=%s",
                            uid, type(exc).__name__,
                        )
                        status["failed"] += 1
                await asyncio.sleep(MENU_REFRESH_INTERVAL_SECONDS)
        status["state"] = "complete"
    except Exception:
        _bot.logger.exception("BETROXY_MODE_MENU_REFRESH_FAILED mode=%s", mode)
        status["state"] = "failed"


def _start_reconciliation(telegram_bot, mode):
    global _reconcile_generation, _reconcile_status
    _reconcile_generation += 1
    _reconcile_status = {
        "mode": mode, "state": "running", "checked": 0,
        "updated": 0, "skipped": 0, "failed": 0,
    }
    asyncio.create_task(_reconcile_menu_buttons(telegram_bot, mode, _reconcile_generation))


async def _admin_action(update, context, action):
    global _reconcile_status
    msg = getattr(update, "effective_message", None)
    if action in {"quiz", "full"}:
        try:
            previous = current_mode()
            set_mode(action, update.effective_user.id)
        except Exception:
            _bot.logger.exception("BETROXY_MODE_CHANGE_FAILED requested=%s", action)
            if msg:
                await msg.reply_text("Mode did not change. Please retry.")
            return
        if previous != action:
            telegram_bot = getattr(context, "bot", None)
            if telegram_bot and hasattr(telegram_bot, "set_chat_menu_button"):
                _start_reconciliation(telegram_bot, action)
            else:
                _reconcile_status = {
                    "mode": action, "state": "unavailable", "checked": 0,
                    "updated": 0, "skipped": 0, "failed": 0,
                }
    if msg:
        refresh = _reconcile_status
        await msg.reply_text(
            f"BETROXY OfficialBot mode: {current_mode().upper()}\n"
            "This changes this bot's current routes for everyone. "
            "Previously sent links and the separate Mini App remain accessible.\n"
            f"Known-chat menu refresh: {refresh['state']} "
            f"({refresh['updated']} updated, {refresh['skipped']} skipped, "
            f"{refresh['failed']} failed, {refresh['checked']} checked; "
            f"{MENU_REFRESH_PAGE_SIZE} per page at five calls per second).",
            reply_markup=_admin_menu(),
        )


async def _mode_command(update, context):
    from telegram.ext import ApplicationHandlerStop

    if not _is_admin(update):
        raise ApplicationHandlerStop
    action = str((getattr(context, "args", None) or ["status"])[0]).lower()
    if action not in {"quiz", "full", "status"}:
        await update.effective_message.reply_text(
            "Use /mode quiz, /mode full, or /mode status.", reply_markup=_admin_menu()
        )
    else:
        await _admin_action(update, context, action)
    raise ApplicationHandlerStop


async def _mode_callback(update, context):
    from telegram.ext import ApplicationHandlerStop

    if not _is_admin(update):
        raise ApplicationHandlerStop
    query = update.callback_query
    action = str(query.data or "").split(":", 1)[-1]
    await query.answer()
    await _admin_action(update, context, action)
    raise ApplicationHandlerStop


def _is_quiz_callback(data):
    return (
        data in {"compact_daily_quiz", "btx_practice", "btx_review"}
        or data.startswith((
            "btx_practice:", "btx_practice_next:", "btx_review:",
            "v110_join:", "v110_consent:", "v110_answer:",
            "v110_leaderboard", "mega_join:", "mega_consent:",
            "mega_answer:", "mega_leaderboard:", "reward_received:",
        ))
    )


def _is_quiz_registration_input(uid, text):
    """Pass only a quiz's awaited mobile entry to legacy text handlers."""
    digits = re.sub(r"\D+", "", text)
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    if not re.fullmatch(r"[6-9]\d{9}", digits):
        return False
    try:
        import production
        import weekly_mega_quiz as mega
        return (
            str((production.v110._session(uid) or {}).get("flow_state") or "") == "quiz_register"
            or str((mega._session(uid) or {}).get("flow_state") or "") == "register"
        )
    except Exception:
        # A database failure must not open an unrelated customer text route.
        return False


async def _callback_guard(update, context):
    from telegram.ext import ApplicationHandlerStop
    import betroxy_universal_verification as verification

    if not is_quiz() or _is_admin(update):
        return
    query = getattr(update, "callback_query", None)
    if query is None:
        return
    user = getattr(update, "effective_user", None)
    # Let the existing neutral self-contact gate handle unverified customers.
    if not user or not verification.is_verified(user.id):
        return
    data = str(query.data or "")
    if _is_quiz_callback(data):
        return
    await query.answer()
    if query.message:
        await query.message.reply_text(QUIZ_HOME_TEXT, parse_mode="HTML", reply_markup=clean_menu())
    raise ApplicationHandlerStop


async def _message_guard(update, context):
    from telegram.ext import ApplicationHandlerStop
    import betroxy_universal_verification as verification

    if not is_quiz() or _is_admin(update):
        return
    msg = getattr(update, "effective_message", None)
    user = getattr(update, "effective_user", None)
    chat = getattr(update, "effective_chat", None)
    if not msg or not user:
        return
    if not chat or getattr(chat, "type", None) != "private":
        raise ApplicationHandlerStop
    if not verification.is_verified(user.id):
        return  # Existing verification middleware supplies neutral copy.
    text = str(getattr(msg, "text", "") or "").strip()
    command = text.split(None, 1)[0].split("@", 1)[0].lower() if text else ""
    if command in {"/start", "/stop", "/cancel"}:
        return
    if text and not command.startswith("/") and _is_quiz_registration_input(user.id, text):
        return
    await msg.reply_text(QUIZ_HOME_TEXT, parse_mode="HTML", reply_markup=clean_menu())
    raise ApplicationHandlerStop


def _install_application(app):
    from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler, filters

    app.add_handler(CommandHandler("mode", _mode_command), group=-30002)
    app.add_handler(CallbackQueryHandler(_mode_callback, pattern=r"^btx_mode:"), group=-30002)
    app.add_handler(CallbackQueryHandler(_callback_guard), group=-30001)
    app.add_handler(
        MessageHandler(filters.ALL & ~filters.UpdateType.BUSINESS_MESSAGE, _message_guard),
        group=-30000,
    )


def install(production, compact_menu):
    """Wrap the final production routes; FULL executes every original handler."""
    global _installed
    if _installed:
        return
    bot = production.bot
    if _bot is None:
        initialize(bot)

    original_start = bot.start
    original_chat = bot.chat_handler
    original_post_init = bot.post_init
    original_compact_menu = compact_menu.compact_public_menu

    def menu(user_id=None):
        return clean_menu() if is_quiz() else original_compact_menu(user_id)

    async def start(update, context):
        if not is_quiz():
            return await original_start(update, context)
        args = list(getattr(context, "args", None) or [])
        payload = str(args[0]).lower() if args else ""
        if payload in {"dailyquiz", "megaquiz", "mega_quiz", "sundaymega"}:
            return await original_start(update, context)
        msg = getattr(update, "effective_message", None)
        if msg:
            await msg.reply_text(QUIZ_HOME_TEXT, parse_mode="HTML", reply_markup=clean_menu())

    async def chat(update, context):
        if not is_quiz():
            return await original_chat(update, context)
        msg = getattr(update, "effective_message", None)
        if msg:
            await msg.reply_text(QUIZ_HOME_TEXT, parse_mode="HTML", reply_markup=clean_menu())

    async def post_init(app):
        result = original_post_init(app)
        if inspect.isawaitable(result):
            await result
        _install_application(app)
        # Resume cleanup after a restart while QUIZ remains persisted. The
        # previous per-chat override survives bot process restarts in Telegram.
        if is_quiz():
            _start_reconciliation(app.bot, "quiz")

    bot.start = start
    bot.chat_handler = chat
    bot.post_init = post_init
    bot.public_menu = menu
    compact_menu.compact_public_menu = menu
    compact_menu.v96.v96_public_menu = menu
    compact_menu.v53.v53_public_menu = menu

    # The installed Business sender resolves both of these globals at send time.
    v75 = production.v83.v75
    biz51 = v75.biz51
    original_business_menu = v75._business_menu
    original_business_payload = v75._business_reply_payload

    def business_menu_for_mode(styled=True):
        return clean_business_menu() if is_quiz() else original_business_menu(styled=styled)

    def business_payload(intent, first_reply=False):
        text, keyboard, stage = original_business_payload(intent, first_reply=first_reply)
        if is_quiz():
            return (
                "Open the official BETROXY bot to verify your account and join the daily quiz.",
                clean_business_menu(), stage,
            )
        return text, keyboard, stage

    v75._business_menu = business_menu_for_mode
    v75._business_reply_payload = business_payload
    biz51._reply_payload = business_payload

    original_business_sender = v75._send_business_reply

    async def business_sender(context, enquiry, intent):
        if not is_quiz():
            return await original_business_sender(context, enquiry, intent)

        import betroxy_universal_verification as verification
        uid = int(enquiry.get("customer_user_id") or 0)
        if not verification.is_verified(uid):
            return await verification.neutral_business_reply(
                context, enquiry, intent, biz51.v49, biz51
            )

        text = "Open the official BETROXY bot to join the daily quiz."
        first_reply = not bool(enquiry.get("auto_ack_sent_at"))
        try:
            sent = await context.bot.send_message(
                chat_id=int(enquiry["customer_chat_id"]),
                text=text,
                business_connection_id=str(enquiry["connection_id"]),
                reply_markup=clean_business_menu(),
                disable_web_page_preview=True,
            )
        except Exception:
            # No product-link fallback, even if Telegram rejects the keyboard.
            text += f"\nhttps://t.me/{bot.BOT_USERNAME}?start=dailyquiz"
            sent = await context.bot.send_message(
                chat_id=int(enquiry["customer_chat_id"]),
                text=text,
                business_connection_id=str(enquiry["connection_id"]),
                disable_web_page_preview=True,
            )
        if first_reply:
            biz51.v49._mark_auto_ack(enquiry["id"])
        biz51.v49._record_outbound(enquiry["id"], getattr(sent, "message_id", None), text)
        return biz51._update_lead_state(
            enquiry["id"], intent=intent, stage="engaged", auto_replied=True
        )

    v75._send_business_reply = business_sender
    biz51._send_smart_reply = business_sender

    # Text/image quiz sends use raw Telegram API rows rather than PTB markups.
    quiz = production.quiz
    v110 = production.v110
    schedule = production.daily_schedule

    original_text = quiz._tg_send_text

    @functools.wraps(original_text)
    def text_send(chat_id, text, rows):
        clean_text = clean_quiz_text(text)
        if clean_text is None:
            return False, {"description": "suppressed in quiz mode"}
        return original_text(chat_id, clean_text, strip_external_rows(rows))

    quiz._tg_send_text = text_send
    original_v110_photo = v110._tg_send_photo

    @functools.wraps(original_v110_photo)
    def daily_photo(chat_id, photo, caption, rows):
        if not is_quiz():
            return original_v110_photo(chat_id, photo, caption, rows)
        # Hero/result images can carry text not represented by their caption.
        # In QUIZ, publish the caption and safe quiz buttons as text instead.
        clean_caption = clean_quiz_text(caption)
        if clean_caption is None:
            return False, {"description": "suppressed in quiz mode"}
        return quiz._tg_send_text(chat_id, clean_caption, strip_external_rows(rows))

    v110._tg_send_photo = daily_photo
    original_daily_consent = v110._consent_prompt

    async def daily_consent(msg, uid, campaign_id):
        if not is_quiz():
            return await original_daily_consent(msg, uid, campaign_id)
        await msg.reply_text(
            "✅ <b>Registration — Step 2 of 2</b>\n\n"
            "By continuing, you agree to participate in the free BETROXY quiz "
            "and receive quiz updates about this entry.\n\n"
            "Ranking is skill-based: accuracy first, then hard-question "
            "accuracy, then speed.",
            parse_mode=bot.ParseMode.HTML,
            reply_markup=v110._consent_markup(campaign_id),
        )

    v110._consent_prompt = daily_consent
    original_schedule_text = schedule._send_text

    @functools.wraps(original_schedule_text)
    def schedule_send(chat_id, text, rows=None):
        clean_text = clean_quiz_text(text)
        if clean_text is None:
            return False, {"description": "suppressed in quiz mode"}
        return original_schedule_text(chat_id, clean_text, strip_external_rows(rows))

    schedule._send_text = schedule_send

    # The legacy engagement worker is disabled in this production entrypoint,
    # but retain a guard if another module calls its send helpers directly.
    v83 = production.v83
    original_engagement_send = v83._tg_send
    original_send_claimed = v83._send_claimed

    @functools.wraps(original_engagement_send)
    def engagement_send(chat_id, text, keyboard=None, business_connection_id=None):
        reward_text = clean_quiz_reward_text(text)
        if is_quiz() and reward_text is not None:
            return original_engagement_send(
                chat_id, reward_text, strip_external_rows(keyboard),
                business_connection_id=business_connection_id,
            )
        clean_text = clean_quiz_text(text)
        if clean_text is None or (is_quiz() and "quiz" not in clean_text.lower()
                                  and "challenge" not in clean_text.lower()):
            return False, {"description": "suppressed in quiz mode"}
        return original_engagement_send(
            chat_id, clean_text, strip_external_rows(keyboard),
            business_connection_id=business_connection_id,
        )

    @functools.wraps(original_send_claimed)
    def send_claimed(user_id, action, key, text, keyboard, business_connection_id=None):
        clean_text = clean_quiz_text(text)
        if clean_text is None or (is_quiz() and action != "quiz"):
            return False
        return original_send_claimed(
            user_id, action, key, clean_text, strip_external_rows(keyboard),
            business_connection_id=business_connection_id,
        )

    v83._tg_send = engagement_send
    v83._send_claimed = send_claimed

    import safe_reminder_delivery as safe_delivery
    original_safe_send = safe_delivery.send_claimed_result

    @functools.wraps(original_safe_send)
    def safe_send(user_id, action, key, text, keyboard=None, **kwargs):
        clean_text = clean_quiz_text(text)
        if clean_text is None or (is_quiz() and action not in {"quiz", "quiz_rewards"}):
            return {
                "sent": False, "status": "suppressed_mode", "retried": 0,
                "permanent": False, "rate_limited": False,
            }
        return original_safe_send(
            user_id, action, key, clean_text, strip_external_rows(keyboard), **kwargs
        )

    safe_delivery.send_claimed_result = safe_send

    import weekly_mega_quiz as mega
    original_home_rows = mega._home_rows
    original_home_text = mega._home_text

    @functools.wraps(original_home_rows)
    def mega_home_rows(campaign):
        return strip_external_rows(original_home_rows(campaign))

    mega._home_rows = mega_home_rows

    @functools.wraps(original_home_text)
    def mega_home_text(campaign):
        return clean_quiz_text(original_home_text(campaign)) or QUIZ_HOME_TEXT

    mega._home_text = mega_home_text

    import weekly_mega_quiz_additions as mega_media
    original_media_photo = mega_media._api_send_photo

    @functools.wraps(original_media_photo)
    def media_photo(chat_id, file_id, caption, rows=None):
        if not is_quiz():
            return original_media_photo(chat_id, file_id, caption, rows)
        # Approved images may contain old product copy not visible in source.
        # Publish the same quiz announcement as text in QUIZ mode.
        clean_caption = clean_quiz_text(caption)
        if clean_caption is None:
            return False, {"description": "suppressed in quiz mode"}
        return quiz._tg_send_text(chat_id, clean_caption, strip_external_rows(rows))

    mega_media._api_send_photo = media_photo

    import betroxy_answer_review as review
    original_render_review = review._render_review

    @functools.wraps(original_render_review)
    def render_review(entry, rows, kind):
        body = original_render_review(entry, rows, kind)
        return clean_quiz_text(body) or "This answer review is unavailable right now."

    review._render_review = render_review
    _installed = True
    bot.logger.warning("BETROXY_MODE_READY mode=%s scope=OfficialBot ads=unchanged", _mode)
