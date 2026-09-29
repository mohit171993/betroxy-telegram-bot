"""Shared response policy for wrong answers in live prize quizzes."""


def missed_answer_feedback():
    # The answer key is available only through the verified, post-close review.
    # Do not hardcode a close time: Daily and Sunday campaign schedules differ.
    return "🎯 <b>Close one.</b> Answers are withheld while this quiz is open."
