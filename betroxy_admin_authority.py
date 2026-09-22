"""Optional BETROXY admin entrypoint. Rollback: previous receipt authority.

The original entrypoints, quizzes, rewards, banners and customer routing are
unchanged. No database records are deleted. SMS OTP is not enabled.
"""
import reward_receipt_confirmation_authority as prior
import betroxy_crm_ui

if __name__ == "__main__":
    betroxy_crm_ui.prepare(prior.production)
    prior.production.main()
