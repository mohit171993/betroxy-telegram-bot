import bot
import v55_force_public_start_bootstrap as v55
import v53_attractive_customer_experience_bootstrap as v53

# V56 - Telegram native styled inline buttons.
# Uses Bot API style field via api_kwargs so current PTB 21.10 can pass it through.

_original_v53_menu = v53.v53_public_menu


def _style_for_text(text):
    t = (text or "").lower()
    if "casino" in t or "withdraw" in t:
        return "danger"
    if "sportsbook" in t or "support" in t or "popular games" in t:
        return "primary"
    if any(x in t for x in ("deposit", "exchange", "promotion", "offers", "refer", "balance", "play now")):
        return "success"
    return None


def _clone_button(btn, style=None):
    kwargs = {
        "text": btn.text,
        "url": getattr(btn, "url", None),
        "callback_data": getattr(btn, "callback_data", None),
        "switch_inline_query": getattr(btn, "switch_inline_query", None),
        "switch_inline_query_current_chat": getattr(btn, "switch_inline_query_current_chat", None),
        "callback_game": getattr(btn, "callback_game", None),
        "pay": getattr(btn, "pay", None),
        "login_url": getattr(btn, "login_url", None),
        "web_app": getattr(btn, "web_app", None),
        "switch_inline_query_chosen_chat": getattr(btn, "switch_inline_query_chosen_chat", None),
        "copy_text": getattr(btn, "copy_text", None),
    }
    if style:
        kwargs["api_kwargs"] = {"style": style}
    return bot.InlineKeyboardButton(**kwargs)


def v56_public_menu(user_id=None):
    markup = _original_v53_menu(user_id)
    rows = []
    for row in markup.inline_keyboard:
        rows.append([
            _clone_button(button, _style_for_text(button.text))
            for button in row
        ])
    return bot.InlineKeyboardMarkup(rows)


v53.v53_public_menu = v56_public_menu
bot.public_menu = v56_public_menu
bot.logger.warning("V56_STYLED_PUBLIC_BUTTONS active=on")


if __name__ == "__main__":
    bot.main()
