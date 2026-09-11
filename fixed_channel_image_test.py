"""Deprecated BETROXY fixed-image test.

The production channel media flow now lives in channel_media_manager.py and
captures the four real creatives directly from the admin's Telegram uploads.
This compatibility module intentionally performs no preview upload or channel
publishing.
"""
import bot


def install():
    bot.logger.warning(
        "FIXED_CHANNEL_IMAGE_TEST deprecated=on active=off replacement=channel_media_manager_admin_upload"
    )
    return bot.callback_handler
