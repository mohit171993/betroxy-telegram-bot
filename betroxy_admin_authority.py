"""BETROXY admin entrypoint with tester verification and a text-only welcome.

The welcome presentation is compact; existing buttons, verification scope,
product logic, channel banners and Business replies remain unchanged.
Rollback this presentation update to commit 54c572a77a6ce985d739f3e29adaaf4a6f41669b.
"""
import reward_receipt_confirmation_authority as prior
import betroxy_crm_ui
import betroxy_start_verification_pilot
import betroxy_text_welcome

if __name__ == "__main__":
    store, ui = betroxy_crm_ui.prepare(prior.production)
    betroxy_start_verification_pilot.attach(ui)
    betroxy_text_welcome.prepare(prior.production)
    prior.production.main()
