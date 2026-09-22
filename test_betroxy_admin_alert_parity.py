"""Offline regression tests for BETROXY admin-alert parity.

No Telegram login, production DB, customer messages or provider calls.
"""
import inspect
import unittest
from unittest.mock import MagicMock, patch

import betroxy_admin_alert_parity as policy


class AdminAlertParityTests(unittest.TestCase):
    def test_new_verified_alert_is_once_only(self):
        with patch.object(policy, "_mark_once", return_value=False) as mark,              patch.object(policy, "_send_admin") as send:
            policy._new_verified_alert(123, "+971501234567")
            mark.assert_called_once()
            send.assert_not_called()

    def test_reference_report_cadence_is_two_hours(self):
        self.assertEqual(policy.REPORT_INTERVAL_SECONDS, 2 * 60 * 60)

    def test_quiet_policy_has_no_15_minute_heartbeat_thread(self):
        src = inspect.getsource(policy._install_quiet_reminder_admin_policy)
        self.assertNotIn("_heartbeat_worker", src)
        self.assertIn("PROGRESS_EVERY = 10**9", src)
        self.assertIn("quiz_alerts._admin_notice = silent_notice", src)

    def test_business_popups_and_digest_are_suppressed(self):
        src = inspect.getsource(policy._install_quiet_business_policy)
        self.assertIn("_send_attention_alert = silent_attention", src)
        self.assertIn("_send_new_lead_alert = silent_new", src)
        self.assertIn("_maybe_send_business_digest = lambda: None", src)

    def test_weekly_repeat_and_startup_preview_removed(self):
        src = open("weekly_mega_channel_schedule.py", encoding="utf-8").read()
        self.assertNotIn("_send_admin_reward_alert(latest, rows, 1)", src)
        self.assertNotIn("_send_admin_reward_alert(latest, rows, 2)", src)
        self.assertNotIn("target=preview.send_all_once", src)
        self.assertIn("startup_auto_send=off", src)

    def test_receipt_tracking_remains_but_popup_is_off(self):
        src = open("reward_receipt_confirmation.py", encoding="utf-8").read()
        self.assertIn("REWARD_RECEIPT_CONFIRMED", src)
        self.assertIn("REWARD_RECEIPT_ADMIN_POPUP_SUPPRESSED", src)
        self.assertIn("admin_alert=off", src)
        self.assertNotIn("WINNER CONFIRMED VOUCHER RECEIPT", src)

    def test_initial_payout_workflow_not_removed_by_policy(self):
        src = inspect.getsource(policy.prepare)
        self.assertIn("initial_action_required_payout_card=preserved", src)


if __name__ == "__main__":
    unittest.main()
