import asyncio
import html
import io
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

import bot
import v41_apify_balance_reports_bootstrap as v41
import v42_reporting_center_bootstrap as v42

_previous_callback_handler = bot.callback_handler
_original_negative_sender = v41._send_negative_report


# ============================================================
# READ-ONLY LATEST STATUS DATA
# ============================================================

def current_active_status_rows():
    """Current campaign-day final status for all active Instagram links.

    This query is read-only. It does not create missing verification rows.
    """
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH active AS (
                    SELECT
                        cl.*,
                        GREATEST(
                            1,
                            LEAST(
                                7,
                                ((CURRENT_DATE - DATE(cl.created_at AT TIME ZONE 'UTC')) + 1)::INTEGER
                            )
                        ) AS current_day
                    FROM campaign_links cl
                    WHERE cl.is_active=TRUE
                      AND LOWER(COALESCE(cl.source_type,'instagram'))='instagram'
                )
                SELECT
                    a.id AS campaign_link_id,
                    a.instagram_username,
                    a.slug,
                    a.agent_code,
                    COALESCE(a.source_type,'instagram') AS source_type,
                    a.source_url,
                    a.created_at,
                    a.current_day AS campaign_day,
                    COALESCE(cv.bio_status,'pending') AS bio_status,
                    COALESCE(cv.only_our_link_status,'pending') AS only_our_link_status,
                    COALESCE(cv.story_status,'pending') AS story_status,
                    COALESCE(cv.story_link_status,'pending') AS story_link_status,
                    cv.checked_by,
                    cv.checked_at,
                    cv.auto_checked_at,
                    COALESCE(cv.auto_check_status,'pending') AS auto_check_status,
                    cv.checker_mode,
                    cv.auto_check_detail
                FROM active a
                LEFT JOIN campaign_verification cv
                  ON cv.campaign_link_id=a.id
                 AND cv.campaign_day=a.current_day
                ORDER BY a.instagram_username
                """
            )
            return cur.fetchall()


def latest_verifier_control():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT status, requested_at, claimed_at, completed_at, result_summary
                FROM verifier_control
                WHERE id=1
                """
            )
            return cur.fetchone() or {}


def _effective_checked_at(row):
    if row.get('auto_checked_at'):
        return row['auto_checked_at']
    if row.get('checked_by') is not None and row.get('checked_at'):
        return row['checked_at']
    return None


def _latest_counts(rows):
    counts = {'PASS': 0, 'ACTION REQUIRED': 0, 'MANUAL REVIEW': 0}
    for r in rows:
        counts[bot.verification_final_result(r)] += 1
    return counts


def latest_final_summary_text(rows, control=None):
    control = control or latest_verifier_control()
    counts = _latest_counts(rows)
    completed = control.get('completed_at')
    completed_text = completed.strftime('%d %b %Y %H:%M UTC') if completed else 'No completed Smart Check yet'
    return (
        "✅ <b>LATEST CHECK - FINAL STATUS</b>\n\n"
        f"Latest Smart Check completed: <b>{completed_text}</b>\n"
        f"Active Instagram creators: <b>{len(rows)}</b>\n\n"
        f"PASS: <b>{counts['PASS']}</b>\n"
        f"ACTION REQUIRED: <b>{counts['ACTION REQUIRED']}</b>\n"
        f"MANUAL REVIEW: <b>{counts['MANUAL REVIEW']}</b>\n\n"
        "This is the final current-day status after the latest run. Same-day full PASS creators may have been skipped by Apify and their preserved PASS is included."
    )


# ============================================================
# DATE LABELS FOR 7-DAY COMPLIANCE
# ============================================================

def campaign_batch_start_date():
    """Use the most common active Instagram creator creation date as batch start."""
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DATE(created_at AT TIME ZONE 'UTC') AS d, COUNT(*) AS c
                FROM campaign_links
                WHERE is_active=TRUE
                  AND LOWER(COALESCE(source_type,'instagram'))='instagram'
                GROUP BY 1
                ORDER BY c DESC, d ASC
                LIMIT 1
                """
            )
            row = cur.fetchone()
    return row['d'] if row and row.get('d') else datetime.now(timezone.utc).date()


def compliance_dates_keyboard():
    start = campaign_batch_start_date()
    buttons = []
    current = []
    for d in range(1, 8):
        report_date = start + timedelta(days=d - 1)
        current.append(
            bot.InlineKeyboardButton(
                report_date.strftime('%d %b'),
                callback_data=f'verify_pdf:{d}',
            )
        )
        if len(current) == 2:
            buttons.append(current)
            current = []
    if current:
        buttons.append(current)
    buttons.append([
        bot.InlineKeyboardButton('✅ Latest Final Status', callback_data='reports_latest_final'),
        bot.InlineKeyboardButton('🔴 Latest Negative', callback_data='reports_latest_negative'),
    ])
    buttons.append([bot.InlineKeyboardButton('⬅️ Reports Center', callback_data='reports_home')])
    return bot.InlineKeyboardMarkup(buttons)


# ============================================================
# LATEST FULL STATUS PDF
# ============================================================

def build_latest_final_status_pdf(rows, control=None):
    control = control or latest_verifier_control()
    generated = datetime.now(timezone.utc)
    completed = control.get('completed_at')
    counts = _latest_counts(rows)

    output = io.BytesIO()
    doc = SimpleDocTemplate(
        output,
        pagesize=landscape(A4),
        rightMargin=16,
        leftMargin=16,
        topMargin=18,
        bottomMargin=18,
        title=f"BETROXY Latest Check Final Status {generated:%Y-%m-%d}",
        author='BETROXY',
    )
    styles = getSampleStyleSheet()
    body = ParagraphStyle('v43body', parent=styles['BodyText'], fontSize=7.8, leading=9.5)
    small = ParagraphStyle('v43small', parent=styles['BodyText'], fontSize=6.2, leading=7.4)

    completed_text = completed.strftime('%d %b %Y %H:%M UTC') if completed else 'No completed Smart Check yet'
    story = [
        Paragraph('BETROXY Latest Smart Check - Final Status Report', styles['Title']),
        Paragraph(
            f"<b>Latest Smart Check completed:</b> {html.escape(completed_text)}<br/>"
            f"<b>Generated:</b> {generated.strftime('%d %b %Y %H:%M UTC')} &nbsp;&nbsp; "
            f"<b>Active creators:</b> {len(rows)} &nbsp;&nbsp; "
            f"<b>PASS:</b> {counts['PASS']} &nbsp;&nbsp; "
            f"<b>ACTION REQUIRED:</b> {counts['ACTION REQUIRED']} &nbsp;&nbsp; "
            f"<b>MANUAL REVIEW:</b> {counts['MANUAL REVIEW']}<br/>"
            "The report reflects final current-day status after the latest run. Same-day full PASS creators may have been skipped by Apify and remain included with their preserved result.",
            body,
        ),
        Spacer(1, 7),
    ]

    data = [[
        'Creator', 'Assigned Batraxy', 'Bio', 'Extra', 'Story', 'Story Link',
        'Final', 'Last Checked', 'Mode'
    ]]

    for r in rows:
        username = str(r.get('instagram_username') or '').strip().lstrip('@')
        source_url = bot.verification_source_url(r)
        assigned = f"{bot.PUBLIC_BASE_URL}/{r['slug']}"
        checked = _effective_checked_at(r)
        checked_text = checked.strftime('%d %b %Y %H:%M') if checked else 'Not checked'
        final = {
            'PASS': 'PASS',
            'ACTION REQUIRED': 'FIX',
            'MANUAL REVIEW': 'REVIEW',
        }[bot.verification_final_result(r)]
        mode = str(r.get('checker_mode') or '-')

        data.append([
            Paragraph(
                f'<link href="{html.escape(source_url, quote=True)}">@{html.escape(username)}</link>',
                small,
            ),
            Paragraph(
                f'<link href="{html.escape(assigned, quote=True)}">{html.escape(r["slug"])}</link>',
                small,
            ),
            bot.verification_status_word(r.get('bio_status'), 'bio'),
            bot.verification_status_word(r.get('only_our_link_status'), 'only'),
            bot.verification_status_word(r.get('story_status'), 'story'),
            bot.verification_status_word(r.get('story_link_status'), 'story_link'),
            final,
            checked_text,
            Paragraph(html.escape(mode), small),
        ])

    table = Table(
        data,
        repeatRows=1,
        colWidths=[130, 115, 48, 48, 50, 58, 50, 105, 80],
    )
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#081C15')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 6.6),
        ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#B9CCC2')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (2, 1), (7, -1), 'CENTER'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [
            colors.HexColor('#F7FBF9'),
            colors.HexColor('#EDF6F1'),
        ]),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(table)
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        '<b>Result meaning:</b> PASS = all four checks confirmed; FIX = at least one confirmed negative; REVIEW = one or more checks unresolved. Creator and Batraxy link cells are clickable.',
        body,
    ))
    doc.build(story)
    output.seek(0)
    return output


async def send_latest_final_report(q, automatic=False):
    rows = await asyncio.to_thread(current_active_status_rows)
    control = await asyncio.to_thread(latest_verifier_control)
    if not rows:
        await q.message.reply_text(
            'No active Instagram creators are available for the latest status report.',
            reply_markup=bot.campaign_menu(),
        )
        return

    await q.message.reply_text(
        latest_final_summary_text(rows, control),
        parse_mode=bot.ParseMode.HTML,
        reply_markup=reports_center_keyboard(),
    )
    pdf = build_latest_final_status_pdf(rows, control)
    prefix = 'Automatic latest final report after Smart Check' if automatic else 'Latest Smart Check final status'
    await q.message.reply_document(
        document=pdf,
        filename=f"BETROXY_Latest_Check_Final_Status_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.pdf",
        caption=f"✅ {prefix} - full report",
        reply_markup=reports_center_keyboard(),
    )


# ============================================================
# CLEAN REPORT MENUS
# ============================================================

def reports_center_keyboard():
    return bot.InlineKeyboardMarkup([
        [
            bot.InlineKeyboardButton('✅ Latest Final Status', callback_data='reports_latest_final'),
            bot.InlineKeyboardButton('🔴 Latest Negative', callback_data='reports_latest_negative'),
        ],
        [
            bot.InlineKeyboardButton('🗓 Date-wise Status PDF', callback_data='reports_status_history'),
            bot.InlineKeyboardButton('📄 Compliance by Date', callback_data='reports_compliance_days'),
        ],
        [
            bot.InlineKeyboardButton('📈 Day-wise Traffic', callback_data='reports_traffic'),
            bot.InlineKeyboardButton('🎯 Checkout Clicks', callback_data='reports_checkout'),
        ],
        [
            bot.InlineKeyboardButton('👥 Creator-wise Traffic', callback_data='reports_creators'),
            bot.InlineKeyboardButton('📅 Today Summary', callback_data='reports_today'),
        ],
        [
            bot.InlineKeyboardButton('📊 Campaign Summary', callback_data='reports_campaign'),
            bot.InlineKeyboardButton('🏆 Top Pages', callback_data='reports_top'),
        ],
        [
            bot.InlineKeyboardButton('📥 Export CSV', callback_data='campaign_export'),
            bot.InlineKeyboardButton('☁️ Apify Usage / Balance', callback_data='verify_hybrid_status'),
        ],
        [bot.InlineKeyboardButton('⬅️ Campaign Tracker', callback_data='campaign_home')],
    ])


def v43_campaign_menu():
    return bot.InlineKeyboardMarkup([
        [
            bot.InlineKeyboardButton('☁️ Run Smart Check', callback_data='verify_request_local_run'),
            bot.InlineKeyboardButton('✅ Latest Status', callback_data='reports_latest_final'),
        ],
        [bot.InlineKeyboardButton('📊 REPORTS CENTER', callback_data='reports_home')],
        [
            bot.InlineKeyboardButton('✅ Verification', callback_data='verify_home'),
            bot.InlineKeyboardButton('☁️ Apify Status', callback_data='verify_hybrid_status'),
        ],
        [
            bot.InlineKeyboardButton('🔗 Creator Links', callback_data='campaign_links'),
            bot.InlineKeyboardButton('➕ Add Creator', callback_data='campaign_add_single'),
        ],
        [
            bot.InlineKeyboardButton('📚 Bulk Create', callback_data='campaign_add_bulk'),
            bot.InlineKeyboardButton('✅ Sync Final Links', callback_data='campaign_sync_final'),
        ],
        [
            bot.InlineKeyboardButton('🔴 Disable Link', callback_data='campaign_disable_by_link'),
            bot.InlineKeyboardButton('🗑 Delete Link', callback_data='campaign_delete_by_link'),
        ],
        [
            bot.InlineKeyboardButton('🎨 Landing Design', callback_data='theme_home'),
            bot.InlineKeyboardButton('🔄 Refresh', callback_data='campaign_home'),
        ],
        [bot.InlineKeyboardButton('⬅️ Admin Panel', callback_data='admin_home')],
    ])


# ============================================================
# CALLBACKS + AUTOMATIC POST-CHECK REPORTS
# ============================================================

async def _smartcheck_negative_and_full(q):
    # Existing promoter negative PDF first, then full final status PDF.
    await _original_negative_sender(q)
    await send_latest_final_report(q, automatic=True)


async def v43_callback_handler(update, context):
    q = update.callback_query
    data = q.data if q else ''

    if data == 'reports_home':
        await q.answer()
        if not bot.is_admin(q.from_user.id):
            return
        await q.message.reply_text(
            '📊 <b>BETROXY REPORTS CENTER</b>\n\n'
            'Latest Smart Check reports are at the top. Traffic/enquiry reports count active campaign links only; disabled and deleted links are excluded.\n\n'
            'Choose the report you need:',
            parse_mode=bot.ParseMode.HTML,
            reply_markup=reports_center_keyboard(),
        )
        return

    if data == 'reports_compliance_days':
        await q.answer()
        start = await asyncio.to_thread(campaign_batch_start_date)
        end = start + timedelta(days=6)
        await q.message.reply_text(
            '📄 <b>Compliance PDF - choose date</b>\n\n'
            f"Campaign dates: <b>{start.strftime('%d %b %Y')} - {end.strftime('%d %b %Y')}</b>\n"
            'Buttons now show calendar dates instead of Day 1, Day 2, etc. Each PDF still contains the actual check date/time for every creator.',
            parse_mode=bot.ParseMode.HTML,
            reply_markup=compliance_dates_keyboard(),
        )
        return

    if data == 'reports_latest_final':
        await q.answer()
        if not bot.is_admin(q.from_user.id):
            return
        await send_latest_final_report(q, automatic=False)
        return

    if data in {'reports_latest_negative', 'reports_negative'}:
        await q.answer()
        if not bot.is_admin(q.from_user.id):
            return
        await _original_negative_sender(q)
        return

    return await _previous_callback_handler(update, context)


# Patch the V41 Smart Check post-run report step. Manual negative-report buttons
# are intercepted above and call the original negative sender only.
v41._send_negative_report = _smartcheck_negative_and_full
bot.campaign_menu = v43_campaign_menu
bot.callback_handler = v43_callback_handler


# Internal sanitized snapshot to support diagnostics/report export. No secrets,
# tokens, cookies, passwords, IP addresses, or user identifiers are written.
def _write_sanitized_latest_snapshot():
    try:
        rows = current_active_status_rows()
        control = latest_verifier_control()
        clean_rows = []
        for r in rows:
            checked = _effective_checked_at(r)
            clean_rows.append({
                'instagram_username': r.get('instagram_username'),
                'slug': r.get('slug'),
                'campaign_day': int(r.get('campaign_day') or 1),
                'bio_status': r.get('bio_status'),
                'only_our_link_status': r.get('only_our_link_status'),
                'story_status': r.get('story_status'),
                'story_link_status': r.get('story_link_status'),
                'final_result': bot.verification_final_result(r),
                'checker_mode': r.get('checker_mode'),
                'last_checked': checked.isoformat() if checked else None,
            })
        payload = {
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'verifier_status': control.get('status'),
            'verifier_completed_at': control.get('completed_at').isoformat() if control.get('completed_at') else None,
            'verifier_result_summary': control.get('result_summary'),
            'rows': clean_rows,
        }
        Path('/tmp/v43_latest_snapshot.json').write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
    except Exception:
        bot.logger.exception('V43_SANITIZED_SNAPSHOT_FAILED')


_write_sanitized_latest_snapshot()
bot.logger.warning(
    'V43_LATEST_REPORTS_ACTIVE compliance_buttons=calendar_dates latest_final_pdf=on '
    'latest_negative=on automatic_post_smartcheck_reports=negative+full'
)


if __name__ == '__main__':
    bot.main()
