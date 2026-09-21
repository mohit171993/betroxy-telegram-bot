"""Final additive BETROXY production authority.

Keeps prior Daily/Weekly channel authority wrappers intact and prepares the
admin-only failed Daily reward diagnostic/recovery overlay before production
installs its reward handler.
"""
import weekly_mega_channel_authority_fix as base
import daily_quiz_admin_rewards as admin_rewards
import daily_reward_provider_failure_recovery as reward_recovery

# production_channel_authority already prepared the delivered-payout UI fix.
# Wrap that prepared install hook with provider diagnostics/recovery.
reward_recovery.prepare(admin_rewards, base.base.production.bot)

base.base.production.bot.logger.warning(
    "BETROXY_REWARD_RECOVERY_AUTHORITY active=on weekly_channel=%s "
    "daily_provider_failure_ui=on startup_auto_retry=off locked_branches_unchanged=on",
    base.CORRECT_WEEKLY_CHANNEL,
)

if __name__ == "__main__":
    base.base.production.main()
