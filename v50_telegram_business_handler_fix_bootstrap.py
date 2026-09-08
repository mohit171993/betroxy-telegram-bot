import bot
import v49_telegram_business_inbox_bootstrap as v49

from telegram.ext import (
    Application,
    BusinessConnectionHandler,
    BusinessMessagesDeletedHandler,
    MessageHandler,
    filters,
)


# python-telegram-bot Application uses __slots__, so V49 cannot attach an
# arbitrary marker attribute to an Application instance. Track installation by
# object identity in module state instead.
_installed_application_ids = set()
_original_add_handler = v49._original_application_add_handler


def _v50_add_handler(self, handler, group=0):
    app_key = id(self)
    if app_key not in _installed_application_ids:
        _installed_application_ids.add(app_key)
        _original_add_handler(self, BusinessConnectionHandler(v49._business_connection_update), group=-2)
        _original_add_handler(
            self,
            MessageHandler(filters.UpdateType.BUSINESS_MESSAGE, v49._business_message_update),
            group=-2,
        )
        _original_add_handler(self, BusinessMessagesDeletedHandler(v49._business_deleted_update), group=-2)
    return _original_add_handler(self, handler, group=group)


Application.add_handler = _v50_add_handler

bot.logger.warning('V50_BUSINESS_HANDLER_INSTALL_FIX active=on slotted_application_safe=on')


if __name__ == '__main__':
    bot.main()
