"""Additive production wrapper for the current BETROXY Telegram channel.

The locked production code is intentionally left unchanged. This wrapper only
corrects the live public-channel authority after importing the locked entrypoint,
then starts the normal production main function.
"""
import production

CORRECT_CHANNEL = "@betroxyupdate"
CORRECT_CHANNEL_URL = "https://t.me/betroxyupdate"

# Daily Quiz/public result workers use this shared runtime authority.
production.v110.CHANNEL_CHAT = CORRECT_CHANNEL

# Keep the optional reminder subscription CTA aligned if/when it is installed.
try:
    import channel_subscription_cta as channel_cta
    channel_cta.CHANNEL_HANDLE = CORRECT_CHANNEL
    channel_cta.CHANNEL_URL = CORRECT_CHANNEL_URL
except Exception:
    production.bot.logger.exception("BETROXY_CHANNEL_CTA_AUTHORITY_PATCH_FAILED")

production.bot.logger.warning(
    "BETROXY_CHANNEL_AUTHORITY active=on channel=%s url=%s locked_production_unchanged=on",
    CORRECT_CHANNEL,
    CORRECT_CHANNEL_URL,
)

if __name__ == "__main__":
    production.main()
