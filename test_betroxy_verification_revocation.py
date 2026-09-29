import unittest
from types import SimpleNamespace

import betroxy_verification_revocation as reset


class Cursor:
    def __init__(self, candidates=(reset.TARGET_UID,), pinned=reset.TARGET_UID,
                 marker=None, live_rows=1, pilot_rows=1):
        self.candidates = candidates
        self.pinned = pinned
        self.marker = marker
        self.live_rows = live_rows
        self.pilot_rows = pilot_rows
        self.calls = []
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        normalized = " ".join(sql.split())
        if "FROM intelligence_leads" in normalized and "UNION" in normalized:
            self.rows = [{"telegram_user_id": uid} for uid in self.candidates]
        elif "FROM btx_crm_test_identity" in normalized:
            self.rows = [{"telegram_user_id": self.pinned}] if self.pinned else []
        elif "FROM btx_verification_resets" in normalized:
            self.rows = [self.marker] if self.marker else []
        elif normalized.startswith("DELETE FROM v110_mobile_verifications"):
            self.rows = [{"telegram_user_id": reset.TARGET_UID}] * self.live_rows
        elif normalized.startswith("DELETE FROM btx_crm_verification_tests"):
            self.rows = [{"telegram_user_id": reset.TARGET_UID}] * self.pilot_rows
        elif normalized.startswith("SELECT COUNT(*)"):
            self.rows = [{"n": 0}]
        else:
            self.rows = []

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        rows, self.rows = self.rows, []
        return rows


class Connection:
    def __init__(self, cursor):
        self.the_cursor = cursor
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        return self.the_cursor

    def commit(self):
        self.commits += 1


class VerificationResetTests(unittest.TestCase):
    def run_reset(self, cursor, store_uid=reset.TARGET_UID):
        connection = Connection(cursor)
        bot = SimpleNamespace(get_db=lambda: connection)
        result = reset.apply_once(SimpleNamespace(test_uid=store_uid), bot)
        return result, connection

    def test_only_the_pinned_unique_account_is_changed(self):
        cursor = Cursor()
        result, connection = self.run_reset(cursor)
        self.assertTrue(result)
        self.assertEqual(connection.commits, 1)
        deletes = [(sql, params) for sql, params in cursor.calls if sql.strip().startswith("DELETE FROM")]
        self.assertEqual(len(deletes), 2)
        self.assertTrue(all(params == (reset.TARGET_UID,) for _, params in deletes))

    def test_ambiguous_username_fails_before_any_delete(self):
        cursor = Cursor(candidates=(reset.TARGET_UID, 999999))
        with self.assertRaises(RuntimeError):
            self.run_reset(cursor)
        self.assertFalse(any(sql.strip().startswith("DELETE FROM") for sql, _ in cursor.calls))

    def test_existing_marker_preserves_future_reverification(self):
        cursor = Cursor(marker={"telegram_user_id": reset.TARGET_UID,
                                "live_rows": 1, "pilot_rows": 1})
        result, connection = self.run_reset(cursor)
        self.assertFalse(result)
        self.assertEqual(connection.commits, 1)
        self.assertFalse(any(sql.strip().startswith("DELETE FROM") for sql, _ in cursor.calls))


if __name__ == "__main__":
    unittest.main()
