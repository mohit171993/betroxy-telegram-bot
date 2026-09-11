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
    # Install the Business enquiry follow-up policy after all legacy engagement
    # modules have loaded so V83's worker resolves the patched selector at runtime.
    try:
        import business_followup_2h
        business_followup_2h.install()
    except Exception:
        bot.logger.exception("BUSINESS_FOLLOWUP_POLICY_INSTALL_FAILED")
        raise

    bot.logger.warning(
        "FIXED_CHANNEL_IMAGE_TEST deprecated=on active=off replacement=channel_media_manager_admin_upload"
    )
    return bot.callback_handler
