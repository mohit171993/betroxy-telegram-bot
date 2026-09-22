"""BETROXY admin entrypoint with a pinned-test-account start verification pilot.

Original core files and non-test customer routing remain unchanged. No startup
mobile reset, SMS OTP, extra poller, or prize purchase is introduced.
Rollback: restore this file from commit 1fa3afe26dbbf6f924b116aea85eb5a5061a6666.
"""
import reward_receipt_confirmation_authority as prior
import betroxy_crm_ui
import betroxy_start_verification_pilot
import welcome_no_banner_overlay
import betroxy_admin_alert_parity

if __name__ == "__main__":
    store, ui = betroxy_crm_ui.prepare(prior.production)
    betroxy_start_verification_pilot.attach(ui)
    welcome_no_banner_overlay.prepare(prior.production)
    betroxy_admin_alert_parity.prepare(prior.production, store)
    prior.production.main()
