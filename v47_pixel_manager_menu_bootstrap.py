import math

import bot
import v46_per_url_pixel_bootstrap as v46


# ============================================================
# V47 - VISIBLE PER-URL PIXEL MANAGER
# ============================================================
# V46 already stores/injects the pixel safely per campaign URL. V47 makes the
# feature obvious from the main Campaign Tracker and from each creator card.

_original_campaign_menu = bot.campaign_menu
_original_creator_menu = bot.campaign_creator_menu
_original_callback_handler = bot.callback_handler


def _pixel_enabled(row):
    return bool(row and str(row.get('pixel_code') or '').strip())


def v47_campaign_menu():
    markup = _original_campaign_menu()
    rows = [list(r) for r in markup.inline_keyboard]
    pixel_row = [
        bot.InlineKeyboardButton(
            '🎯 URL Pixel Manager',
            callback_data='campaign_pixel_manager',
        )
    ]

    # Put Pixel Manager with the campaign utilities/settings, not hidden under
    # Creator -> Edit. Search for the current settings row so this also works
    # with the V42/V44 reporting menu layout.
    insert_at = max(0, len(rows) - 1)
    for i, row in enumerate(rows):
        labels = ' '.join(str(getattr(b, 'text', '') or '') for b in row)
        if 'Landing Design' in labels or 'Apify Usage' in labels or 'Checker Status' in labels:
            insert_at = i
            break
    rows.insert(insert_at, pixel_row)
    return bot.InlineKeyboardMarkup(rows)


def v47_creator_menu(code):
    markup = _original_creator_menu(code)
    rows = [list(r) for r in markup.inline_keyboard]
    row = bot.campaign_link_by_code(code)
    label = '🎯 Pixel ✅' if _pixel_enabled(row) else '🎯 Add Pixel'
    pixel_button = [
        bot.InlineKeyboardButton(
            label,
            callback_data=f'campaign_edit_username:~{code}',
        )
    ]

    # Keep Open Landing Page first, then make Pixel immediately visible.
    insert_at = 1 if rows else 0
    rows.insert(insert_at, pixel_button)
    return bot.InlineKeyboardMarkup(rows)


def _pixel_manager_keyboard(page=0, per_page=8):
    links = list(bot.all_campaign_links(include_inactive=False))
    links.sort(key=lambda r: str(r.get('instagram_username') or '').lower())

    total = len(links)
    total_pages = max(1, math.ceil(total / per_page))
    page = max(0, min(int(page), total_pages - 1))
    subset = links[page * per_page:(page + 1) * per_page]

    rows = []
    for r in subset:
        username = str(r.get('instagram_username') or r.get('slug') or 'creator')
        display = username[:34] + ('…' if len(username) > 34 else '')
        status = '✅' if _pixel_enabled(r) else '➕'
        rows.append([
            bot.InlineKeyboardButton(
                f'{status} @{display}',
                callback_data=f"campaign_edit_username:~{r['agent_code']}",
            )
        ])

    nav = []
    if page > 0:
        nav.append(bot.InlineKeyboardButton('⬅️', callback_data=f'campaign_pixel_page:{page-1}'))
    nav.append(bot.InlineKeyboardButton(f'{page+1}/{total_pages}', callback_data='campaign_noop'))
    if page < total_pages - 1:
        nav.append(bot.InlineKeyboardButton('➡️', callback_data=f'campaign_pixel_page:{page+1}'))
    rows.append(nav)
    rows.append([
        bot.InlineKeyboardButton('⬅️ Campaign Tracker', callback_data='campaign_home')
    ])
    return bot.InlineKeyboardMarkup(rows), links, page, total_pages


async def v47_callback_handler(update, context):
    q = update.callback_query
    data = q.data or ''

    if data == 'campaign_pixel_manager' or data.startswith('campaign_pixel_page:'):
        await q.answer()
        if not bot.is_admin(q.from_user.id):
            await q.message.reply_text('❌ Admin access required.')
            return

        page = 0
        if data.startswith('campaign_pixel_page:'):
            try:
                page = int(data.split(':', 1)[1])
            except Exception:
                page = 0

        kb, links, page, total_pages = _pixel_manager_keyboard(page)
        active_pixels = sum(1 for r in links if _pixel_enabled(r))
        await q.message.reply_text(
            '🎯 <b>URL Pixel Manager</b>\n\n'
            f'Active campaign URLs: <b>{len(links)}</b>\n'
            f'URLs with pixel: <b>{active_pixels}</b>\n\n'
            'Tap the exact creator URL where you want to add/change a pixel.\n'
            '✅ = pixel already set   ➕ = no pixel yet\n\n'
            'You can paste a full Meta/Google/custom tag, or just a numeric Meta Pixel ID.',
            parse_mode=bot.ParseMode.HTML,
            reply_markup=kb,
            disable_web_page_preview=True,
        )
        return

    return await _original_callback_handler(update, context)


bot.campaign_menu = v47_campaign_menu
bot.campaign_creator_menu = v47_creator_menu
bot.callback_handler = v47_callback_handler

bot.logger.warning(
    'V47_PIXEL_MANAGER_VISIBLE main_dashboard=on creator_card=on per_url_selector=on'
)


if __name__ == '__main__':
    bot.main()
