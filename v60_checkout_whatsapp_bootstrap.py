import bot
import v59_user_friendly_public_ux_bootstrap as v59
import v56_checkout_whatsapp_bootstrap as checkout

# V60 keeps the current V59 public UX while layering the new per-link
# checkout/WhatsApp feature on top.
bot.start = v59.v59_start
bot.logger.warning("V60_CHECKOUT_WHATSAPP_BOOTSTRAP active=on v59_public_ux=preserved")

if __name__ == "__main__":
    bot.main()
