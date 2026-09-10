import time

import bot
import v80_promotions_deeplink_button as v80

v79 = v80.v79
v78 = v80.v78
v75 = v80.v75
v63 = v80.v63
v59 = v80.v59
biz51 = v80.biz51
PROMOTIONS_URL = v80.PROMOTIONS_URL

# The live public /start handler in V63 renders v53.v53_public_menu(), not
# v59.v59_public_menu() directly. V80 patched the latter only, so the Promotions
# row could be missing from the actual message shown to users. Patch the exact
# menu object used by V63 while preserving Admin Analytics and all existing rows.
v53 = v63.v62.v61.v60.v59.v53
_current_live_public_menu = v53.v53_public_menu


def _has_promotions(rows):
    for row in rows:
        for btn in row:
            if getattr(btn, "url", None) == PROMOTIONS_URL:
                return True
            if "promotions" in str(getattr(btn, "text", "")).lower():
                return True
    return False


def _insert_promo_after_account_row(rows):
    if _has_promotions(rows):
        return rows

    promo = v59._btn("🎁 Promotions", url=PROMOTIONS_URL, style="success")
    insert_at = None
    for idx, row in enumerate(rows):
        labels = " ".join(str(getattr(btn, "text", "")) for btn in row)
        if "Account & Services" in labels or "Transactions" in labels:
            insert_at = idx + 1
            break

    if insert_at is None:
        # Fallback: keep it near the top but below any admin/analytics controls.
        insert_at = min(2, len(rows))

    rows.insert(insert_at, [promo])
    return rows


def live_public_menu_with_promotions(user_id=None):
    markup = _current_live_public_menu(user_id)
    rows = [list(row) for row in markup.inline_keyboard]
    return bot.InlineKeyboardMarkup(_insert_promo_after_account_row(rows))


# Patch the exact public menu path used by v63_start.
v53.v53_public_menu = live_public_menu_with_promotions
bot.public_menu = live_public_menu_with_promotions

# Also keep V59 callback/home menus on the V80 Promotions-aware menu.
v59.v59_public_menu = v80._official_public_menu_with_promotions

# Re-assert the Business menu patches and sender chain so fresh Business greetings
# also include the Promotions row.
v78.business_main_menu = v80._business_menu_with_promotions
v75._business_menu = v80._business_menu_with_promotions
v75._business_reply_payload = v78._business_reply_payload
biz51._reply_payload = v78._business_reply_payload
biz51._send_smart_reply = v75._send_business_reply

bot.logger.warning(
    "V81_PROMOTIONS_VISIBILITY_FIX active=on public_v63_menu=patched "
    "business_menu=patched promotions_deeplink=on"
)

if __name__ == "__main__":
    v63.apply_signup_cta()
    v63.enable_business_smart_auto_reply()
    bot.logger.warning("V81 polling handover delay=12s")
    time.sleep(12)
    bot.main()
