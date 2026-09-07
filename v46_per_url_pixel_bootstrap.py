import re

import bot
import v45_smartcheck_history_bootstrap as v45


# ============================================================
# PER-URL TRACKING PIXEL
# ============================================================
# Admin-only setting stored on each campaign_links row. A pixel attached to one
# Batraxy creator URL is injected only into that URL's live landing page.

_original_inject_theme = bot.inject_theme
_original_edit_menu = bot.campaign_edit_menu
_original_username_start = bot.campaign_edit_username_start
_original_username_save = bot.campaign_edit_username_save


def _ensure_pixel_schema():
    # init_db is idempotent and guarantees campaign_links exists before ALTER.
    bot.init_db()
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                ALTER TABLE campaign_links
                ADD COLUMN IF NOT EXISTS pixel_code TEXT
                """
            )
        conn.commit()


def _set_pixel_code(code, pixel_code):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE campaign_links
                SET pixel_code=%s
                WHERE LOWER(agent_code)=LOWER(%s)
                RETURNING *
                """,
                (pixel_code or None, code),
            )
            row = cur.fetchone()
        conn.commit()
    return row


def _meta_pixel_from_id(pixel_id):
    pid = re.sub(r"\D", "", str(pixel_id or ""))
    return f"""<!-- Meta Pixel Code -->
<script>
!function(f,b,e,v,n,t,s)
{{if(f.fbq)return;n=f.fbq=function(){{n.callMethod?
n.callMethod.apply(n,arguments):n.queue.push(arguments)}};
if(!f._fbq)f._fbq=n;n.push=n;n.loaded=!0;n.version='2.0';
n.queue=[];t=b.createElement(e);t.async=!0;
t.src=v;s=b.getElementsByTagName(e)[0];
s.parentNode.insertBefore(t,s)}}(window, document,'script',
'https://connect.facebook.net/en_US/fbevents.js');
fbq('init', '{pid}');
fbq('track', 'PageView');
</script>
<noscript><img height="1" width="1" style="display:none"
src="https://www.facebook.com/tr?id={pid}&ev=PageView&noscript=1"
/></noscript>
<!-- End Meta Pixel Code -->"""


def _inject_before_close(html_text, snippet):
    if not snippet:
        return html_text
    text = str(html_text)
    marker = "\n<!-- BETROXY PER-URL PIXEL -->\n" + snippet.strip() + "\n<!-- /BETROXY PER-URL PIXEL -->\n"

    # Preferred placement for standard tracking scripts.
    m = re.search(r"</head\s*>", text, flags=re.I)
    if m:
        return text[:m.start()] + marker + text[m.start():]

    # Defensive fallback for uploaded themes without a normal head element.
    m = re.search(r"</body\s*>", text, flags=re.I)
    if m:
        return text[:m.start()] + marker + text[m.start():]
    return text + marker


def v46_inject_theme(theme, link, preview=False):
    rendered = _original_inject_theme(theme, link, preview=preview)
    if preview:
        return rendered
    pixel_code = (link.get('pixel_code') or '').strip() if hasattr(link, 'get') else ''
    if not pixel_code:
        return rendered
    return _inject_before_close(rendered, pixel_code)


def v46_campaign_edit_menu(code):
    row = bot.campaign_link_by_code(code)
    pixel_on = bool(row and str(row.get('pixel_code') or '').strip())
    pixel_label = '🎯 Pixel ✅' if pixel_on else '🎯 Tracking Pixel'
    return bot.InlineKeyboardMarkup(
        [
            [
                bot.InlineKeyboardButton('👤 Instagram Name', callback_data=f'campaign_edit_username:{code}'),
                bot.InlineKeyboardButton('🔗 Landing Slug', callback_data=f'campaign_edit_slug:{code}'),
            ],
            [
                bot.InlineKeyboardButton('🆔 Affiliate Code', callback_data=f'campaign_edit_code:{code}'),
                bot.InlineKeyboardButton('🏷 Source', callback_data=f'campaign_edit_source:{code}'),
            ],
            [
                bot.InlineKeyboardButton(pixel_label, callback_data=f'campaign_edit_username:pixel|{code}'),
            ],
            [bot.InlineKeyboardButton('⬅️ Creator', callback_data=f'campaign_creator:{code}')],
            [bot.InlineKeyboardButton('🏠 Campaign Tracker', callback_data='campaign_home')],
        ]
    )


async def v46_campaign_edit_username_start(update, context):
    q = update.callback_query
    data = q.data or ''
    value = data.split(':', 1)[1] if ':' in data else ''

    if not value.startswith('pixel|'):
        return await _original_username_start(update, context)

    await q.answer()
    if not bot.is_admin(q.from_user.id):
        return bot.ConversationHandler.END

    code = value.split('|', 1)[1]
    row = bot.campaign_link_by_code(code)
    if not row:
        await q.message.reply_text('❌ Creator link not found.', reply_markup=bot.campaign_menu())
        return bot.ConversationHandler.END

    context.user_data['v46_pixel_edit'] = True
    context.user_data['v46_pixel_code'] = code
    landing_url = f"{bot.PUBLIC_BASE_URL}/{row['slug']}"
    current = bool(str(row.get('pixel_code') or '').strip())

    await q.message.reply_text(
        '🎯 <b>Tracking Pixel for this URL</b>\n\n'
        f'<code>{bot.html.escape(landing_url)}</code>\n\n'
        f"Current status: <b>{'ACTIVE' if current else 'NOT SET'}</b>\n\n"
        'Paste the full pixel/tag code here. You can use Meta Pixel, Google tag, or other HTML/JavaScript tracking code.\n\n'
        'For Meta Pixel, you may also send only the numeric Pixel ID and I will create the standard PageView code automatically.\n\n'
        'Send <code>clear</code> to remove the pixel from this URL.\n\n'
        '<b>It will run only on this Batraxy URL.</b>',
        parse_mode=bot.ParseMode.HTML,
        disable_web_page_preview=True,
    )
    return bot.CAMPAIGN_EDIT_USERNAME


async def v46_campaign_edit_username_save(update, context):
    if not context.user_data.get('v46_pixel_edit'):
        return await _original_username_save(update, context)

    if not await bot.require_admin(update):
        return bot.ConversationHandler.END

    code = context.user_data.get('v46_pixel_code')
    raw = (update.message.text or '').strip()

    if raw.lower() == 'clear':
        pixel_code = None
    elif re.fullmatch(r'\d{5,30}', raw):
        pixel_code = _meta_pixel_from_id(raw)
    else:
        if len(raw) > 30000:
            await update.message.reply_text(
                '❌ Pixel code is too large. Maximum size is 30,000 characters.'
            )
            return bot.CAMPAIGN_EDIT_USERNAME
        # Generic snippets are intentionally allowed because this is an
        # admin-only feature. Do not execute or transform the pasted code.
        pixel_code = raw

    row = _set_pixel_code(code, pixel_code)
    if not row:
        context.user_data.clear()
        await update.message.reply_text('❌ Creator link not found.', reply_markup=bot.campaign_menu())
        return bot.ConversationHandler.END

    landing_url = f"{bot.PUBLIC_BASE_URL}/{row['slug']}"
    enabled = bool(str(row.get('pixel_code') or '').strip())
    context.user_data.clear()

    await update.message.reply_text(
        ('✅ <b>Tracking Pixel Saved</b>\n\n' if enabled else '✅ <b>Tracking Pixel Removed</b>\n\n')
        + f'<code>{bot.html.escape(landing_url)}</code>\n\n'
        + ('Pixel is active only on this URL.' if enabled else 'This URL now has no custom pixel.'),
        parse_mode=bot.ParseMode.HTML,
        reply_markup=v46_campaign_edit_menu(row['agent_code']),
        disable_web_page_preview=True,
    )
    return bot.ConversationHandler.END


_ensure_pixel_schema()
bot.inject_theme = v46_inject_theme
bot.campaign_edit_menu = v46_campaign_edit_menu
bot.campaign_edit_username_start = v46_campaign_edit_username_start
bot.campaign_edit_username_save = v46_campaign_edit_username_save

bot.logger.warning(
    'V46_PER_URL_PIXEL_ACTIVE admin_only=on per_campaign_url=on generic_code=on meta_id_autobuild=on preview_fire=off'
)


if __name__ == '__main__':
    bot.main()
