"""Owner-approved additional full Betroxy admins.

bot.py is a protected baseline (see betroxy_crm_preflight.PROTECTED), so the
extra admin ids are layered on here instead of editing bot.py. Every caller of
``bot.is_admin`` (including bot.py's own /admin and require_admin paths, which
look the name up at call time) then grants these ids the same rights as
ADMIN_ID. Owner alerts and admin notifications still go to ADMIN_ID only.
"""
import bot

EXTRA_ADMIN_IDS = frozenset({8860632140})  # @Liveline_proadmin


def is_admin(user_id):
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return False
    return uid == int(bot.ADMIN_ID) or uid in EXTRA_ADMIN_IDS


def install():
    bot.EXTRA_ADMIN_IDS = EXTRA_ADMIN_IDS
    bot.is_admin = is_admin


install()
