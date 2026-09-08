import bot
import v59_user_friendly_public_ux_bootstrap as v59
import v56_checkout_whatsapp_bootstrap as checkout

# V60 keeps the current V59 public UX while layering the new per-link
# checkout/WhatsApp feature on top.

META_CH_WHATSAPP_NUMBER = "16084301011"

_existing_init_db = bot.init_db


def v60_init_db():
    _existing_init_db()
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE campaign_links
                SET checkout_enabled=TRUE,
                    whatsapp_number=%s,
                    is_active=TRUE
                WHERE LOWER(slug)='meta-ch'
                """,
                (META_CH_WHATSAPP_NUMBER,),
            )
        conn.commit()


bot.init_db = v60_init_db
bot.start = v59.v59_start
bot.logger.warning(
    "V60_CHECKOUT_WHATSAPP_BOOTSTRAP active=on v59_public_ux=preserved meta_ch_whatsapp=%s",
    META_CH_WHATSAPP_NUMBER,
)

if __name__ == "__main__":
    bot.main()
