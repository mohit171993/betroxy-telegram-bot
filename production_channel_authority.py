"""Additive production wrapper for the current BETROXY Telegram channel.

The locked production code is intentionally left unchanged. This wrapper only
corrects live runtime authorities/overlays after importing the locked entrypoint,
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

# Additive admin reward-screen correction: once an eligible award is already
# delivered, remove the stale Approve & Issue button and show completion state.
# The locked daily_quiz_admin_rewards.py file itself is not modified.
try:
    import daily_quiz_admin_rewards as admin_rewards
    import daily_quiz_admin_ui_fix as admin_ui_fix
    admin_ui_fix.prepare(admin_rewards, production.bot)
except Exception:
    production.bot.logger.exception("DAILY_QUIZ_ADMIN_UI_FIX_PREPARE_FAILED")

# Daily Quiz mobile gate: require a Telegram-verified contact before entry while
# accepting international numbers. This is quiz-only; provider/payment rules are
# not changed here, and the locked V110 implementation remains untouched.
try:
    import quiz_mobile_verification_overlay as quiz_mobile_verification
    quiz_mobile_verification.install(production.v110, production.bot)
except Exception:
    production.bot.logger.exception("QUIZ_MOBILE_VERIFICATION_OVERLAY_FAILED")
    raise

production.bot.logger.warning(
    "BETROXY_CHANNEL_AUTHORITY active=on channel=%s url=%s locked_production_unchanged=on",
    CORRECT_CHANNEL,
    CORRECT_CHANNEL_URL,
)

if __name__ == "__main__":
    production.main()
