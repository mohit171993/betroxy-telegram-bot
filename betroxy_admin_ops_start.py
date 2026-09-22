"""Add the team CRM without changing the locked BETROXY boot/product modules."""
import betroxy_admin_ops_guard as guard
import reward_receipt_confirmation_authority as previous

production = previous.production
bot = production.bot
_previous_main = bot.main


def _main_with_admin_ops():
    old_post_init = bot.post_init

    async def post_init(application):
        await old_post_init(application)
        protected = {name:getattr(bot,name) for name in ('start','callback_handler','chat_handler','public_menu','admin_menu')}
        import betroxy_admin_ops
        try:
            await betroxy_admin_ops.install(application,bot)
            assert all(getattr(bot,name) is value for name,value in protected.items())
            bot.logger.info('BETROXY_ADMIN_CORE_ROUTE_GUARD unchanged=all no_customer_menu_replacement=on no_otp_provider_calls=on')
        except Exception as exc:
            # The original app has already initialised. A CRM boot failure must
            # not bring down quiz/result/reward/Business or customer handlers.
            bot.logger.error('BETROXY_CRM_INSTALL_FAILED type=%s original_product_continues=on',type(exc).__name__)
    bot.post_init = post_init
    return _previous_main()


bot.main = _main_with_admin_ops

if __name__ == '__main__':
    guard.verify()
    bot.logger.info('BETROXY_CORE_FILE_GUARD protected_files=%s unchanged=all',len(guard.PROTECTED))
    production.main()
