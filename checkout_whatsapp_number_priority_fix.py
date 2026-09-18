"""Additive priority fix for admin Checkout/WhatsApp number entry.

The checkout editor has always accepted international WhatsApp numbers (7-15
country-code digits), but the later India-only reward-mobile handler can consume
phone-like admin messages while its 30-minute reward-mobile prompt window is
still active. This overlay gives the explicit checkout-number edit state
priority without changing reward-mobile rules or locked files.
"""

from telegram.ext import ApplicationHandlerStop

_installed = False


def install(bot):
    global _installed
    if _installed:
        return

    import v105_indian_mobile_rewards as v105
    import v56_checkout_whatsapp_bootstrap as checkout

    original_typed_handler = v105._typed_indian_mobile_handler
    original_chat_handler = v105.v105_chat_handler

    async def _process_checkout_number(update, context):
        user = update.effective_user
        msg = update.effective_message
        if not user or not msg or not bot.is_admin(user.id):
            return False

        code = context.user_data.get("v56_checkout_number_code")
        if not code:
            return False

        raw = str(getattr(msg, "text", "") or "").strip()
        if not raw:
            return False

        number = checkout._clean_phone(raw[2:] if raw.startswith("00") else raw)
        if not (7 <= len(number) <= 15):
            await msg.reply_text(
                "❌ Please send a valid WhatsApp number with country code (7–15 digits).\n"
                "Example: +16084301011 or 16084301011"
            )
            return True

        row = checkout._save_checkout_number(code, number)
        if not row:
            await msg.reply_text("❌ Campaign link not found. Please reopen Checkout / WhatsApp settings.")
            context.user_data.pop("v56_checkout_number_code", None)
            return True

        context.user_data.pop("v56_checkout_number_code", None)
        context.user_data.pop("v56_checkout_message_code", None)

        await msg.reply_text(
            "✅ <b>WhatsApp number updated.</b>\n\n" + checkout._checkout_settings_text(row),
            parse_mode=bot.ParseMode.HTML,
            reply_markup=checkout._checkout_settings_markup(row),
            disable_web_page_preview=True,
        )
        bot.logger.warning(
            "CHECKOUT_WHATSAPP_NUMBER_PRIORITY saved admin=%s code=%s number=+%s international=on",
            user.id, code, number,
        )
        return True

    async def typed_handler_fixed(update, context):
        if await _process_checkout_number(update, context):
            raise ApplicationHandlerStop
        return await original_typed_handler(update, context)

    async def chat_handler_fixed(update, context):
        if await _process_checkout_number(update, context):
            return
        return await original_chat_handler(update, context)

    # V105's Application.add_handler wrapper resolves this module global when
    # the Telegram application is constructed, so replacing it before bot.main
    # ensures checkout-number entry runs before India-only reward validation.
    v105._typed_indian_mobile_handler = typed_handler_fixed
    v105.v105_chat_handler = chat_handler_fixed

    # Keep the ordinary text route aligned too, in case it already points at
    # the V105 wrapper.
    if bot.chat_handler is original_chat_handler:
        bot.chat_handler = chat_handler_fixed

    _installed = True
    bot.logger.warning(
        "CHECKOUT_WHATSAPP_NUMBER_PRIORITY active=on admin_checkout_first=on "
        "international=7-15_digits reward_mobile_rules_unchanged=on locked_files_unchanged=on"
    )
