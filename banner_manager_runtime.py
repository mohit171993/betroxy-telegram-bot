"""Runtime wiring for the BETROXY Banner Manager.

Kept separate from banner_manager.py so the persistent library/UI can stay simple
and the live production monkey-patches are applied only after all legacy modules
and the final welcome experience have loaded.
"""
from __future__ import annotations

import bot
import banner_manager

_installed = False


def _patch_channel_rotation():
    import channel_media_manager

    old_selector = channel_media_manager._approved_file_id
    if getattr(old_selector, "_banner_rotation_patched", False):
        return

    def rotating_selector(asset_key):
        try:
            file_id = banner_manager.get_channel_file_id(asset_key)
            if file_id:
                return file_id
        except Exception:
            bot.logger.exception("BANNER_MANAGER_CHANNEL_SELECTOR_FAILED slot=%s", asset_key)
        # Preserve the original four locked Telegram file_ids as the hard fallback.
        return old_selector(asset_key)

    rotating_selector._banner_rotation_patched = True
    rotating_selector._locked_fallback_selector = old_selector
    channel_media_manager._approved_file_id = rotating_selector
    bot.logger.warning(
        "BANNER_MANAGER_CHANNEL_RUNTIME active=on selector=round_robin_daily_IST locked_file_id_fallback=on"
    )


def _patch_officialbot_start():
    import welcome_experience_v2 as welcome

    previous_start = bot.start
    if getattr(previous_start, "_banner_manager_start_patched", False):
        return

    async def dynamic_banner_start(update, context):
        user = getattr(update, "effective_user", None)
        msg = getattr(update, "effective_message", None)
        args = list(getattr(context, "args", []) or [])
        payload = str(args[0]).strip().lower() if args else ""

        # Admin/affiliate dashboards and every deep-link retain their existing routes.
        if not msg or payload or (user and (bot.is_admin(user.id) or bot.find_agent_by_telegram_user_id(user.id))):
            return await previous_start(update, context)

        file_id = banner_manager.get_welcome_file_id("start")
        if not file_id:
            # Until the admin uploads a custom Start banner, preserve the current
            # built-in welcome banner and all existing behavior exactly.
            return await previous_start(update, context)

        try:
            try:
                import clean_customer_menu
                if user:
                    clean_customer_menu.v83._touch_user(user.id, "banner_manager_start")
            except Exception:
                pass

            await msg.reply_photo(
                photo=file_id,
                caption="✨ <b>BETROXY</b> • Daily Quiz & Rewards",
                parse_mode=bot.ParseMode.HTML,
            )
            await msg.reply_text(
                welcome._bot_welcome_text(),
                parse_mode=bot.ParseMode.HTML,
                reply_markup=welcome.bot_menu(user.id if user else None),
                disable_web_page_preview=True,
            )
            bot.logger.warning(
                "BANNER_MANAGER_START_SENT uid=%s source=approved_telegram_file_id",
                getattr(user, "id", None),
            )
            return
        except Exception:
            # A bad Telegram file_id must never break /start. Fall back to the
            # pre-existing welcome implementation immediately.
            bot.logger.exception("BANNER_MANAGER_START_SEND_FAILED uid=%s fallback=legacy", getattr(user, "id", None))
            return await previous_start(update, context)

    dynamic_banner_start._banner_manager_start_patched = True
    dynamic_banner_start._previous_start = previous_start
    bot.start = dynamic_banner_start
    bot.logger.warning("BANNER_MANAGER_START_RUNTIME active=on dynamic_file_id=on legacy_fallback=on")


def _patch_business_banner():
    import v75_business_buttons_compatible as v75

    previous_send = v75._send_business_reply
    if getattr(previous_send, "_banner_manager_business_patched", False):
        return

    async def dynamic_business_send(context, enquiry, intent):
        first_reply = not bool(enquiry.get("auto_ack_sent_at"))
        file_id = banner_manager.get_welcome_file_id("business") if first_reply else None
        if not file_id:
            return await previous_send(context, enquiry, intent)

        # V75 resolves v63.BANNER_URL at send time. Swap only for this call and
        # restore in finally. Business messages are serialized by Telegram's update
        # processing in normal operation; all failures still fall back safely.
        old_banner = v75.v63.BANNER_URL
        try:
            v75.v63.BANNER_URL = file_id
            result = await previous_send(context, enquiry, intent)
            bot.logger.warning(
                "BANNER_MANAGER_BUSINESS_SENT enquiry=%s source=approved_telegram_file_id",
                enquiry.get("id"),
            )
            return result
        except Exception:
            bot.logger.exception("BANNER_MANAGER_BUSINESS_SEND_FAILED enquiry=%s fallback=legacy", enquiry.get("id"))
            v75.v63.BANNER_URL = old_banner
            return await previous_send(context, enquiry, intent)
        finally:
            v75.v63.BANNER_URL = old_banner

    dynamic_business_send._banner_manager_business_patched = True
    dynamic_business_send._previous_send = previous_send
    v75._send_business_reply = dynamic_business_send
    # V51/V75 route Business smart replies through this runtime pointer.
    v75.biz51._send_smart_reply = dynamic_business_send
    bot.logger.warning("BANNER_MANAGER_BUSINESS_RUNTIME active=on dynamic_file_id=on legacy_fallback=on")


def install():
    global _installed
    if _installed:
        return
    _patch_channel_rotation()
    _patch_officialbot_start()
    _patch_business_banner()
    _installed = True
    bot.logger.warning(
        "BANNER_MANAGER_RUNTIME active=on start=dynamic business=dynamic channel=rotating "
        "no_code_change_for_uploads=on no_redeploy_for_uploads=on"
    )
