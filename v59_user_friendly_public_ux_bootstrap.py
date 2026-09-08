import html

import bot
import v58_force_recheck_all_bootstrap as v58
import v57_reliable_banner_bootstrap as v57
import v53_attractive_customer_experience_bootstrap as v53

# ============================================================
# V59 - USER-FRIENDLY PUBLIC EXPERIENCE
# ============================================================
# Goals:
# - neutral, professional public intro (no betting promotion in welcome copy)
# - fewer, clearer top-level actions
# - structured support flow
# - friendly handling of normal public messages
# - preserve referral/deep-link, affiliate, admin, checker and Business features

_previous_start = bot.start
_previous_callback_handler = bot.callback_handler
_previous_chat_handler = bot.chat_handler


def _btn(text, *, callback_data=None, url=None, web_app=None, style=None):
    kwargs = {
        "text": text,
        "callback_data": callback_data,
        "url": url,
        "web_app": web_app,
    }
    if style:
        kwargs["api_kwargs"] = {"style": style}
    return bot.InlineKeyboardButton(**kwargs)


def v59_public_welcome_text(returning=False):
    if returning:
        return (
            "👋 <b>Welcome back to BETROXY</b>\n\n"
            "Access your account, platform services, updates and support from one place.\n\n"
            "What would you like to do? 👇"
        )
    return (
        "👋 <b>Welcome to BETROXY</b>\n\n"
        "Your official hub for account access, platform services, updates and customer support.\n\n"
        "✨ Fast access • Simple navigation • Help when you need it\n\n"
        "Choose an option below to continue 👇"
    )


def v59_public_menu(user_id=None):
    rows = []

    # Keep privileged controls visible only to the users who already had them.
    if user_id == bot.ADMIN_ID:
        rows.append([
            _btn("🛠 Admin Panel", callback_data="admin_home", style="danger")
        ])
    elif user_id and bot.find_agent_by_telegram_user_id(user_id):
        rows.append([
            _btn("📈 My Affiliate Performance", callback_data="affiliate_home", style="primary")
        ])

    # Simple public navigation. All platform/account actions safely open the
    # existing configured Web App; no existing backend routes are changed.
    rows.extend([
        [_btn("🚀 Open BETROXY App", web_app=bot.WebAppInfo(url=bot.APP_URL), style="success")],
        [
            _btn("👤 Account & Services", web_app=bot.WebAppInfo(url=bot.APP_URL), style="primary"),
            _btn("📜 Transactions", web_app=bot.WebAppInfo(url=bot.APP_URL), style="primary"),
        ],
        [
            _btn("🎧 Help & Support", callback_data="ux_support_home", style="primary"),
            _btn("📢 Updates", url=bot.UPDATES_URL, style="success"),
        ],
        [
            _btn("👥 Refer a Friend", callback_data="refer_friend"),
            _btn("ℹ️ How It Works", callback_data="ux_how_it_works"),
        ],
    ])
    return bot.InlineKeyboardMarkup(rows)


def v59_support_menu():
    return bot.InlineKeyboardMarkup([
        [
            _btn("🔐 Account / Login", callback_data="ux_help_account", style="primary"),
            _btn("💳 Payment Help", callback_data="ux_help_payment", style="primary"),
        ],
        [
            _btn("💸 Withdrawal Help", callback_data="ux_help_withdrawal", style="primary"),
            _btn("🛠 Technical Issue", callback_data="ux_help_technical", style="primary"),
        ],
        [
            _btn("💬 Telegram Support", url=bot.TELEGRAM_SUPPORT_URL, style="success"),
            _btn("🟢 WhatsApp Support", url=bot.WHATSAPP_SUPPORT_URL, style="success"),
        ],
        [_btn("⬅️ Main Menu", callback_data="ux_home")],
    ])


def _human_support_menu():
    return bot.InlineKeyboardMarkup([
        [
            _btn("💬 Telegram Support", url=bot.TELEGRAM_SUPPORT_URL, style="primary"),
            _btn("🟢 WhatsApp Support", url=bot.WHATSAPP_SUPPORT_URL, style="success"),
        ],
        [_btn("⬅️ Support Menu", callback_data="ux_support_home")],
        [_btn("🏠 Main Menu", callback_data="ux_home")],
    ])


async def v59_start(update, context):
    # Deep-links must keep original referral/claim attribution behavior.
    if getattr(context, "args", None):
        return await _previous_start(update, context)

    msg = update.effective_message
    user = update.effective_user
    if not msg:
        return

    # Preserve the reliable banner, but use neutral platform copy.
    try:
        await msg.reply_photo(
            photo=v57.BANNER_URL,
            caption="✨ <b>BETROXY</b> • Official Access & Support",
            parse_mode=bot.ParseMode.HTML,
        )
    except Exception:
        bot.logger.exception("V59 public banner send failed")

    # A user already present in referrals/affiliate records gets a shorter
    # returning-user greeting; failure to inspect never blocks /start.
    returning = False
    try:
        if user:
            with bot.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT 1 FROM referrals WHERE telegram_user_id=%s LIMIT 1",
                        (user.id,),
                    )
                    returning = bool(cur.fetchone())
    except Exception:
        bot.logger.exception("V59 returning-user lookup failed")

    await msg.reply_text(
        v59_public_welcome_text(returning=returning),
        parse_mode=bot.ParseMode.HTML,
        reply_markup=v59_public_menu(user.id if user else None),
        disable_web_page_preview=True,
    )


async def _send_help(q, title, body):
    await q.message.reply_text(
        f"{title}\n\n{body}\n\n"
        "If the issue continues, contact support below and include any relevant reference or screenshot.",
        parse_mode=bot.ParseMode.HTML,
        reply_markup=_human_support_menu(),
        disable_web_page_preview=True,
    )


async def v59_callback_handler(update, context):
    q = update.callback_query
    data = (q.data or "") if q else ""

    if data == "ux_home":
        await q.answer()
        await q.message.reply_text(
            v59_public_welcome_text(returning=True),
            parse_mode=bot.ParseMode.HTML,
            reply_markup=v59_public_menu(q.from_user.id),
            disable_web_page_preview=True,
        )
        return

    # Intercept the old public Home and Support callbacks too, so every route
    # now lands on the same polished V59 experience.
    if data == "home":
        await q.answer()
        await q.message.reply_text(
            v59_public_welcome_text(returning=True),
            parse_mode=bot.ParseMode.HTML,
            reply_markup=v59_public_menu(q.from_user.id),
            disable_web_page_preview=True,
        )
        return

    if data in {"ux_support_home", "support"}:
        await q.answer()
        await q.message.reply_text(
            "🎧 <b>BETROXY Help Center</b>\n\n"
            "Choose the topic that best matches what you need. You can also contact a support agent directly.",
            parse_mode=bot.ParseMode.HTML,
            reply_markup=v59_support_menu(),
        )
        return

    if data == "ux_how_it_works":
        await q.answer()
        await q.message.reply_text(
            "ℹ️ <b>Using BETROXY is simple</b>\n\n"
            "1️⃣ Tap <b>Open BETROXY App</b> for account and platform access.\n"
            "2️⃣ Use <b>Account & Services</b> or <b>Transactions</b> for quick access to your account.\n"
            "3️⃣ Use <b>Help & Support</b> whenever you need assistance.\n"
            "4️⃣ Follow <b>Updates</b> for official announcements.",
            parse_mode=bot.ParseMode.HTML,
            reply_markup=bot.InlineKeyboardMarkup([
                [_btn("🚀 Open BETROXY App", web_app=bot.WebAppInfo(url=bot.APP_URL), style="success")],
                [_btn("🎧 Help & Support", callback_data="ux_support_home", style="primary")],
                [_btn("⬅️ Main Menu", callback_data="ux_home")],
            ]),
        )
        return

    if data == "ux_help_account":
        await q.answer()
        await _send_help(
            q,
            "🔐 <b>Account / Login Help</b>",
            "Open the BETROXY App and try signing in again. If you see an error, note the exact message and whether the issue involves login, password or verification.",
        )
        return

    if data == "ux_help_payment":
        await q.answer()
        await _send_help(
            q,
            "💳 <b>Payment Help</b>",
            "For a pending payment, keep the amount, approximate time and transaction/reference details ready. Do not send passwords, OTPs or other secret credentials.",
        )
        return

    if data == "ux_help_withdrawal":
        await q.answer()
        await _send_help(
            q,
            "💸 <b>Withdrawal Help</b>",
            "Check the latest status in your account first. If it remains pending, keep the requested amount, request time and relevant reference available for support.",
        )
        return

    if data == "ux_help_technical":
        await q.answer()
        await _send_help(
            q,
            "🛠 <b>Technical Help</b>",
            "Try reopening the app once. If the issue continues, send support the error message plus a screenshot and mention whether you are using Android, iPhone, desktop or web.",
        )
        return

    return await _previous_callback_handler(update, context)


async def v59_chat_handler(update, context):
    user = update.effective_user
    if not user:
        return await _previous_chat_handler(update, context)

    # Preserve admin and affiliate free-text behavior exactly.
    if bot.is_admin(user.id) or bot.find_agent_by_telegram_user_id(user.id):
        return await _previous_chat_handler(update, context)

    text = (update.effective_message.text or "").strip().lower() if update.effective_message else ""
    if any(k in text for k in ("help", "support", "problem", "issue", "login", "payment", "withdraw")):
        await update.effective_message.reply_text(
            "🎧 <b>I can help with that.</b>\n\nChoose the closest support topic below:",
            parse_mode=bot.ParseMode.HTML,
            reply_markup=v59_support_menu(),
        )
        return

    await update.effective_message.reply_text(
        "👋 <b>Welcome back.</b>\n\nUse the shortcuts below for account access, services or support.",
        parse_mode=bot.ParseMode.HTML,
        reply_markup=v59_public_menu(user.id),
        disable_web_page_preview=True,
    )


# Patch all public entry points used by downstream handlers.
v53.v53_public_welcome_text = lambda: v59_public_welcome_text(returning=False)
v53.v53_public_menu = v59_public_menu
bot.public_welcome_text = lambda: v59_public_welcome_text(returning=False)
bot.public_menu = v59_public_menu
bot.support_menu = v59_support_menu
bot.start = v59_start
bot.callback_handler = v59_callback_handler
bot.chat_handler = v59_chat_handler

bot.logger.warning(
    "V59_USER_FRIENDLY_PUBLIC_UX active=on neutral_intro=on simplified_menu=on structured_support=on"
)

if __name__ == "__main__":
    bot.main()
