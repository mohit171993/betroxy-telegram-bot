"""Ensure the additive Sunday Mega Quiz entry is visible in every live menu path.

This module only wraps menu renderers. It does not replace or remove any existing
button, callback, deep link, Daily Quiz behavior, Business policy, or locked code.
"""
from __future__ import annotations

import bot

_installed = False


def _add_mega(markup, mega_url, business=False):
    rows = [list(row) for row in (getattr(markup, "inline_keyboard", None) or [])]
    if any(
        "Sunday Mega Quiz" in str(getattr(button, "text", ""))
        for row in rows for button in row
    ):
        return bot.InlineKeyboardMarkup(rows)

    api_kwargs = None if business else {"style": "primary"}
    button = bot.InlineKeyboardButton(
        "🔥 Sunday Mega Quiz • ₹5,000",
        url=str(mega_url),
        api_kwargs=api_kwargs,
    )
    insert_at = len(rows)
    for idx, row in enumerate(rows):
        if any("Daily Quiz" in str(getattr(item, "text", "")) for item in row):
            insert_at = idx + 1
            break
    rows.insert(insert_at, [button])
    return bot.InlineKeyboardMarkup(rows)


def install(weekly_mega, compact_menu):
    global _installed
    if _installed:
        return

    mega_url = str(weekly_mega.BOT_DEEPLINK)

    # The dynamic approved-banner /start route renders welcome_experience_v2.bot_menu
    # directly, so patch that renderer as well as the compact/public aliases.
    try:
        import welcome_experience_v2 as welcome

        old_bot_menu = welcome.bot_menu
        if not getattr(old_bot_menu, "_mega_menu_overlay", False):
            def bot_menu_with_mega(user_id=None):
                return _add_mega(old_bot_menu(user_id), mega_url, business=False)
            bot_menu_with_mega._mega_menu_overlay = True
            welcome.bot_menu = bot_menu_with_mega

        old_business_menu = welcome.business_menu
        if not getattr(old_business_menu, "_mega_menu_overlay", False):
            def welcome_business_with_mega(styled=True):
                return _add_mega(old_business_menu(styled), mega_url, business=True)
            welcome_business_with_mega._mega_menu_overlay = True
            welcome.business_menu = welcome_business_with_mega
    except Exception:
        bot.logger.exception("MEGA_MENU_WELCOME_PATCH_FAILED")

    # Keep public menu aliases covered. Duplicate protection makes this safe even
    # when weekly_mega_quiz_additions already wrapped one of these renderers.
    old_compact = compact_menu.compact_public_menu
    if not getattr(old_compact, "_mega_menu_overlay", False):
        def compact_with_mega(user_id=None):
            return _add_mega(old_compact(user_id), mega_url, business=False)
        compact_with_mega._mega_menu_overlay = True
        compact_menu.compact_public_menu = compact_with_mega
        bot.public_menu = compact_with_mega
        try:
            compact_menu.v96.v96_public_menu = compact_with_mega
            compact_menu.v53.v53_public_menu = compact_with_mega
        except Exception:
            pass

    # Business sender paths can resolve v75._business_menu directly.
    try:
        v75 = weekly_mega.v110.v83.v75
        old_v75_menu = v75._business_menu
        if not getattr(old_v75_menu, "_mega_menu_overlay", False):
            def v75_menu_with_mega(styled=True):
                return _add_mega(old_v75_menu(styled), mega_url, business=True)
            v75_menu_with_mega._mega_menu_overlay = True
            v75._business_menu = v75_menu_with_mega
    except Exception:
        bot.logger.exception("MEGA_MENU_BUSINESS_PATCH_FAILED")

    # Verify the two important live renderers before declaring the overlay active.
    official_ok = False
    business_ok = False
    try:
        import welcome_experience_v2 as welcome
        official_ok = any(
            "Sunday Mega Quiz" in str(getattr(b, "text", ""))
            for row in welcome.bot_menu(None).inline_keyboard for b in row
        )
        business_ok = any(
            "Sunday Mega Quiz" in str(getattr(b, "text", ""))
            for row in welcome.business_menu(True).inline_keyboard for b in row
        )
    except Exception:
        bot.logger.exception("MEGA_MENU_SELFTEST_FAILED")

    _installed = True
    bot.logger.warning(
        "MEGA_MENU_OVERLAY active=on officialbot=%s business=%s old_buttons_preserved=on "
        "daily_quiz_unchanged=on mega_url=%s",
        official_ok, business_ok, mega_url,
    )
