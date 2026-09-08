import bot
import v51_telegram_business_auto_conversion_bootstrap as v51


def _reset_legacy_plain_ack_rows():
    """V49 sent a plain one-time acknowledgement before V51 existed.

    Rows that have never received a V51 smart reply have auto_reply_count=0.
    Clear only their legacy acknowledgement marker so the next customer message
    receives the new smart conversion welcome. Existing V51 replies are kept.
    """
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE telegram_business_enquiries
                SET auto_ack_sent_at=NULL
                WHERE COALESCE(auto_reply_count,0)=0
                  AND auto_ack_sent_at IS NOT NULL
                """
            )
            changed = cur.rowcount
        conn.commit()
    return changed


try:
    changed = _reset_legacy_plain_ack_rows()
    bot.logger.warning(
        'V52_BUSINESS_SMART_REPLY_LEGACY_RESET active=on legacy_rows_reset=%s',
        changed,
    )
except Exception:
    bot.logger.exception('V52_BUSINESS_SMART_REPLY_LEGACY_RESET_FAILED')


if __name__ == '__main__':
    bot.main()
