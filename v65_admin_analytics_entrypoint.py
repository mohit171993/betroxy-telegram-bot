import bot
import v63_clean_public_banner_bootstrap as v63

# V65 - force Bot Analytics visibility for the configured admin.
# Keeps the V63 public/banner/business behavior unchanged.

_v53 = v63.v62.v61.v60.v59.v53
_original_public_menu = _v53.v53_public_menu


def _public_menu_with_admin_analytics(user_id=None):
    markup = _original_public_menu(user_id)
    rows = [list(row) for row in markup.inline_keyboard]
    if user_id == bot.ADMIN_ID:
        exists = any(
            any(getattr(btn, "callback_data", None) == "bot_analytics" for btn in row)
            for row in rows
        )
        if not exists:
            rows.insert(0, [
                bot.InlineKeyboardButton(
                    "📊 Bot Analytics",
                    callback_data="bot_analytics",
                )
            ])
    return bot.InlineKeyboardMarkup(rows)


_v53.v53_public_menu = _public_menu_with_admin_analytics
bot.logger.warning("V65_ADMIN_ANALYTICS_ENTRYPOINT active=on public_admin_button=on")

if __name__ == "__main__":
    v63.apply_signup_cta()
    v63.enable_business_smart_auto_reply()
    v63.run_banner_self_test_once()
    bot.main()
