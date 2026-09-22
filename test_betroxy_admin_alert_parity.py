"""Offline regression tests for BETROXY admin-alert parity.

No Telegram login, production DB, customer messages or provider calls.
"""
import inspect
import unittest
from unittest.mock import patch

import betroxy_admin_alert_parity as policy


class AdminAlertParityTests(unittest.TestCase):
    def test_new_verified_alert_is_once_only(self):
        with (
            patch.object(policy, "_mark_once", return_value=False) as mark,
            patch.object(policy, "_send_admin") as send,
        ):
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
        self.assertIn("payout_repeat_alerts=off", src)

    def test_business_popups_and_digest_are_suppressed(self):
        src = inspect.getsource(policy._install_quiet_business_policy)
        self.assertIn("_send_attention_alert = silent_attention", src)
        self.assertIn("_send_new_lead_alert = silent_new", src)
        self.assertIn("_maybe_send_business_digest = lambda: None", src)

    def test_weekly_repeat_is_runtime_filtered_but_initial_card_survives(self):
        src = inspect.getsource(policy._install_weekly_reward_alert_policy)
        self.assertIn("if int(slot or 0) > 0", src)
        self.assertIn("return original(campaign, rows, slot)", src)

    def test_startup_weekly_preview_is_runtime_suppressed(self):
        src = inspect.getsource(policy.prepare)
        self.assertIn("previews.send_all_once = quiet_preview", src)
        self.assertIn("startup_preview=off", src)

    def test_receipt_and_other_legacy_popups_are_filtered_additively(self):
        src = inspect.getsource(policy._install_async_admin_message_filter)
        self.assertIn("WINNER CONFIRMED VOUCHER RECEIPT", src)
        self.assertIn("BETROXY Reminder Live Status", src)
        self.assertIn("BETROXY BUSINESS INBOX", src)
        self.assertIn("BETROXY DAILY QUIZ — PERFORMANCE REPORT", src)
        self.assertIn("return await original", src)

    def test_protected_reward_and_weekly_files_are_not_edited_for_suppression(self):
        receipt = open("reward_receipt_confirmation.py", encoding="utf-8").read()
        weekly = open("weekly_mega_channel_schedule.py", encoding="utf-8").read()
        self.assertIn("WINNER CONFIRMED VOUCHER RECEIPT", receipt)
        self.assertIn("_send_admin_reward_alert(latest, rows, 1)", weekly)
        self.assertIn("preview.send_all_once", weekly)

    def test_initial_payout_workflow_is_explicitly_preserved(self):
        src = inspect.getsource(policy.prepare)
        self.assertIn("initial_action_required_payout_card=preserved", src)


if __name__ == "__main__":
    unittest.main()
