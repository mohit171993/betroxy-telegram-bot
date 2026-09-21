"""Final additive BETROXY production authority with voucher receipt confirmation.

Imports all prior production authorities, then adds the winner acknowledgement
layer without changing any locked checkpoint branch.
"""
import reward_failure_recovery_authority as prior
import daily_quiz_admin_rewards as admin_rewards
import reward_receipt_confirmation as receipt_confirmation

production = prior.base.base.production

receipt_confirmation.prepare(admin_rewards, production.bot)

production.bot.logger.warning(
    "BETROXY_RECEIPT_CONFIRMATION_AUTHORITY active=on "
    "winner_ack_button=on admin_sent_vs_confirmed=on "
    "locked_branches_unchanged=on"
)

if __name__ == "__main__":
    production.main()
