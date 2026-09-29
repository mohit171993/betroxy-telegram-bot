import unittest

import quiz_feedback_policy as policy


class QuizFeedbackPolicyTests(unittest.TestCase):
    def test_live_wrong_answer_never_reveals_key_or_unverified_close_time(self):
        text = policy.missed_answer_feedback().lower()
        self.assertIn("withheld", text)
        self.assertNotIn("correct answer", text)
        self.assertNotIn("ist", text)


if __name__ == "__main__":
    unittest.main()
