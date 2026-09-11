"""Deprecated BETROXY fixed-image test compatibility hook.

The production channel media flow now lives in channel_media_manager.py and
captures the four real creatives directly from the admin's Telegram uploads.
This compatibility module intentionally performs no preview upload or channel
publishing. It remains part of the production bootstrap, so lightweight late
production policy patches can be installed here without touching the stable
quiz/media routing.
"""
import bot


def install():
    # Apply the final customer-facing welcome presentation only after the main
    # production feature guard has validated the stable underlying routes.
    try:
        import clean_customer_menu
        import welcome_experience_v2
        welcome_experience_v2.install(clean_customer_menu, clean_customer_menu.v83.v75)
    except Exception:
        bot.logger.exception("WELCOME_EXPERIENCE_V2_INSTALL_FAILED")
        raise

    # Admin-controlled Banner Manager. It imports the existing four locked
    # channel creatives as fallbacks, allows no-code welcome/banner replacement,
    # and rotates additional approved quiz creatives round-robin by IST day.
    try:
        import banner_manager
        import banner_manager_runtime
        banner_manager.install()
        banner_manager_runtime.install()
    except Exception:
        bot.logger.exception("BANNER_MANAGER_INSTALL_FAILED")
        raise

    # Install the Business enquiry follow-up policy after all legacy engagement
    # modules have loaded so V83's worker resolves the patched selector at runtime.
    try:
        import business_followup_2h
        business_followup_2h.install()
    except Exception:
        bot.logger.exception("BUSINESS_FOLLOWUP_POLICY_INSTALL_FAILED")
        raise

    # One-time public connection verification requested after the bot was added
    # as channel admin. This uses the real production schedule/media path, then
    # removes its temporary test post automatically.
    try:
        import channel_connection_test
        channel_connection_test.start()
    except Exception:
        bot.logger.exception("CHANNEL_CONNECTION_TEST_INSTALL_FAILED")
        raise

    bot.logger.warning(
        "FIXED_CHANNEL_IMAGE_TEST deprecated=on active=off replacement=channel_media_manager_admin_upload "
        "welcome_v2=on banner_manager=on channel_rotation=round_robin_daily_IST"
    )
    return bot.callback_handler
