"""Additive BETROXY Sunday Mega Quiz channel authority wrapper.

Purpose:
- keep the 15/09 locked code and existing production wrappers unchanged;
- make the Weekly Mega Quiz use the current public channel @betroxyupdate;
- let the existing retry/result worker republish any due result whose delivery
  previously failed, then trigger the existing manual admin payout alert.

This file is intentionally a new top-level production entrypoint.
"""

import production_channel_authority as base
import weekly_mega_quiz as weekly_mega

CORRECT_WEEKLY_CHANNEL = base.CORRECT_CHANNEL

# weekly_mega_quiz keeps its own CHANNEL constant, separate from Daily Quiz.
# Set the shared weekly authority before production.main() imports/installs the
# weekly overlays and starts the Mega workers.
weekly_mega.CHANNEL = CORRECT_WEEKLY_CHANNEL

base.production.bot.logger.warning(
    "MEGA_CHANNEL_AUTHORITY active=on channel=%s catchup_via_existing_worker=on "
    "locked_15_09_branch_unchanged=on",
    CORRECT_WEEKLY_CHANNEL,
)

if __name__ == "__main__":
    base.production.main()
