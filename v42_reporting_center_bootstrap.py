import asyncio
import html
import io
from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

import bot
import v41_apify_balance_reports_bootstrap as v41

_previous_callback_handler = bot.callback_handler


# ============================================================
# ACTIVE-ONLY REPORT QUERIES
# ============================================================

def active_daily_report_rows():
    """Day-wise traffic for currently active campaign links only.

    Disabled links are excluded by is_active=TRUE. Deleted campaign links can no
    longer join campaign_links, so their historic events are excluded as well.
    """
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH active AS (
                    SELECT LOWER(agent_code) AS agent_code
                    FROM campaign_links
                    WHERE is_active=TRUE
                ),
                landing AS (
                    SELECT
                        DATE(le.created_at AT TIME ZONE 'UTC') AS d,
                        COUNT(*) AS enquiries,
                        COUNT(DISTINCT le.visitor_hash) FILTER (
                            WHERE le.visitor_hash IS NOT NULL
                        ) AS unique_visitors
                    FROM landing_events le
                    JOIN active a ON LOWER(le.agent_code)=a.agent_code
                    GROUP BY 1
                ),
                outbound AS (
                    SELECT
                        DATE(oe.created_at AT TIME ZONE 'UTC') AS d,
                        COUNT(*) FILTER (WHERE oe.destination='website') AS website_clicks,
                        COUNT(*) FILTER (WHERE oe.destination='telegram') AS telegram_clicks,
                        COUNT(*) FILTER (WHERE oe.destination='casino') AS casino_clicks,
                        COUNT(*) FILTER (WHERE oe.destination='sportsbook') AS sportsbook_clicks,
                        COUNT(*) FILTER (WHERE oe.destination='popular') AS popular_clicks,
                        COUNT(*) FILTER (WHERE oe.destination='promotions') AS promotion_clicks
                    FROM outbound_events oe
                    JOIN active a ON LOWER(oe.agent_code)=a.agent_code
                    GROUP BY 1
                )
                SELECT
                    COALESCE(l.d,o.d) AS report_date,
                    COALESCE(l.enquiries,0) AS enquiries,
                    COALESCE(l.unique_visitors,0) AS unique_visitors,
                    COALESCE(o.website_clicks,0) AS website_clicks,
                    COALESCE(o.telegram_clicks,0) AS telegram_clicks,
                    COALESCE(o.casino_clicks,0) AS casino_clicks,
                    COALESCE(o.sportsbook_clicks,0) AS sportsbook_clicks,
                    COALESCE(o.popular_clicks,0) AS popular_clicks,
                    COALESCE(o.promotion_clicks,0) AS promotion_clicks
                FROM landing l
                FULL OUTER JOIN outbound o ON o.d=l.d
                ORDER BY report_date DESC
                """
            )
            return cur.fetchall()


def active_creator_report_rows():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH landing AS (
                    SELECT
                        LOWER(agent_code) AS agent_code,
                        COUNT(*) AS enquiries,
                        COUNT(DISTINCT visitor_hash) FILTER (
                            WHERE visitor_hash IS NOT NULL
                        ) AS unique_visitors
                    FROM landing_events
                    GROUP BY 1
                ),
                outbound AS (
                    SELECT
                        LOWER(agent_code) AS agent_code,
                        COUNT(*) FILTER (WHERE destination='website') AS website_clicks,
                        COUNT(*) FILTER (WHERE destination='telegram') AS telegram_clicks,
                        COUNT(*) FILTER (WHERE destination='casino') AS casino_clicks,
                        COUNT(*) FILTER (WHERE destination='sportsbook') AS sportsbook_clicks,
                        COUNT(*) FILTER (WHERE destination='popular') AS popular_clicks,
                        COUNT(*) FILTER (WHERE destination='promotions') AS promotion_clicks
                    FROM outbound_events
                    GROUP BY 1
                ),
                conv AS (
                    SELECT
                        LOWER(agent_code) AS agent_code,
                        COUNT(*) FILTER (WHERE event_type='registration') AS registrations,
                        COUNT(*) FILTER (WHERE event_type='deposit') AS deposits,
                        COALESCE(SUM(amount) FILTER (WHERE event_type='deposit'),0) AS deposit_amount
                    FROM conversion_events
                    GROUP BY 1
                )
                SELECT
                    cl.id,
                    cl.instagram_username,
                    cl.slug,
                    cl.agent_code,
                    COALESCE(cl.source_type,'instagram') AS source_type,
                    cl.created_at,
                    COALESCE(l.enquiries,0) AS enquiries,
                    COALESCE(l.unique_visitors,0) AS unique_visitors,
                    COALESCE(o.website_clicks,0) AS website_clicks,
                    COALESCE(o.telegram_clicks,0) AS telegram_clicks,
                    COALESCE(o.casino_clicks,0) AS casino_clicks,
                    COALESCE(o.sportsbook_clicks,0) AS sportsbook_clicks,
                    COALESCE(o.popular_clicks,0) AS popular_clicks,
                    COALESCE(o.promotion_clicks,0) AS promotion_clicks,
                    COALESCE(c.registrations,0) AS registrations,
                    COALESCE(c.deposits,0) AS deposits,
                    COALESCE(c.deposit_amount,0) AS deposit_amount
                FROM campaign_links cl
                LEFT JOIN landing l ON l.agent_code=LOWER(cl.agent_code)
                LEFT JOIN outbound o ON o.agent_code=LOWER(cl.agent_code)
                LEFT JOIN conv c ON c.agent_code=LOWER(cl.agent_code)
                WHERE cl.is_active=TRUE
                ORDER BY COALESCE(l.enquiries,0) DESC,
                         COALESCE(o.website_clicks,0) DESC,
                         cl.id
                """
            )
            return cur.fetchall()


def active_status_history_rows():
    """Stored status checks grouped by their real calendar check date.

    Only active creators are shown. Pending rows that were never actually
    checked are excluded.
    """
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    cl.id AS campaign_link_id,
                    cl.instagram_username,
                    cl.slug,
                    cl.agent_code,
                    COALESCE(cl.source_type,'instagram') AS source_type,
                    cl.source_url,
                    cv.campaign_day,
                    cv.bio_status,
                    cv.only_our_link_status,
                    cv.story_status,
                    cv.story_link_status,
                    cv.auto_check_status,
                    cv.checker_mode,
                    cv.checked_at,
                    cv.auto_checked_at,
                    COALESCE(cv.auto_checked_at,cv.checked_at) AS effective_checked_at,
                    DATE(COALESCE(cv.auto_checked_at,cv.checked_at) AT TIME ZONE 'UTC') AS check_date
                FROM campaign_verification cv
                JOIN campaign_links cl ON cl.id=cv.campaign_link_id
                WHERE cl.is_active=TRUE
                  AND (cv.auto_checked_at IS NOT NULL OR cv.checked_by IS NOT NULL)
                ORDER BY effective_checked_at DESC, cl.id
                """
            )
            return cur.fetchall()


# ============================================================
# TEXT REPORTS
# ============================================================

def _daily_totals(rows):
    keys = [
        'enquiries','unique_visitors','website_clicks','telegram_clicks',
        'casino_clicks','sportsbook_clicks','popular_clicks','promotion_clicks',
    ]
    return {k: sum(int(r.get(k) or 0) for r in rows) for k in keys}


def campaign_summary_text():
    rows = active_creator_report_rows()
    totals = {
        'enquiries': sum(int(r.get('enquiries') or 0) for r in rows),
        'unique': sum(int(r.get('unique_visitors') or 0) for r in rows),
        'checkout': sum(int(r.get('website_clicks') or 0) for r in rows),
        'telegram': sum(int(r.get('telegram_clicks') or 0) for r in rows),
        'registrations': sum(int(r.get('registrations') or 0) for r in rows),
        'deposits': sum(int(r.get('deposits') or 0) for r in rows),
        'deposit_amount': sum(float(r.get('deposit_amount') or 0) for r in rows),
    }
    checkout_rate = totals['checkout'] / totals['enquiries'] * 100 if totals['enquiries'] else 0
    return (
        "📊 <b>ACTIVE CAMPAIGN SUMMARY</b>\n\n"
        "Disabled/deleted campaign links are excluded from all counts.\n\n"
        f"Active creator links: <b>{len(rows)}</b>\n"
        f"Enquiries / landing visits: <b>{totals['enquiries']}</b>\n"
        f"Unique visitors: <b>{totals['unique']}</b>\n"
        f"Checkout page clicks: <b>{totals['checkout']}</b> "
        f"(<b>{checkout_rate:.1f}%</b> of enquiries)\n"
        f"Telegram clicks: <b>{totals['telegram']}</b>\n"
        f"Registrations: <b>{totals['registrations']}</b>\n"
        f"Deposits: <b>{totals['deposits']}</b>\n"
        f"Deposit amount: <b>{totals['deposit_amount']:,.2f}</b>"
    )


def today_summary_text():
    rows = active_daily_report_rows()
    today = datetime.now(timezone.utc).date()
    r = next((x for x in rows if x.get('report_date') == today), None) or {}
    return (
        "📅 <b>TODAY - ACTIVE LINKS ONLY</b>\n\n"
        f"Date: <b>{today.strftime('%d %b %Y')} UTC</b>\n"
        f"Enquiries / landing visits: <b>{int(r.get('enquiries') or 0)}</b>\n"
        f"Unique visitors: <b>{int(r.get('unique_visitors') or 0)}</b>\n"
        f"Checkout page clicks: <b>{int(r.get('website_clicks') or 0)}</b>\n"
        f"Telegram clicks: <b>{int(r.get('telegram_clicks') or 0)}</b>\n"
        f"Casino clicks: <b>{int(r.get('casino_clicks') or 0)}</b>\n"
        f"Sportsbook clicks: <b>{int(r.get('sportsbook_clicks') or 0)}</b>\n\n"
        "Disabled/deleted links are excluded."
    )


def checkout_report_text(rows):
    total = sum(int(r.get('website_clicks') or 0) for r in rows)
    lines = [
        "🎯 <b>CHECKOUT PAGE CLICK REPORT</b>",
        "",
        "Checkout page click = Batraxy landing -> Betroxy website.",
        "Disabled/deleted links are excluded.",
        "",
        f"Total checkout clicks: <b>{total}</b>",
        "",
        "<b>Day-wise - latest 20 activity dates</b>",
        "<pre>Date        Clicks</pre>",
    ]
    for r in rows[:20]:
        d = r['report_date'].strftime('%d-%m-%Y') if r.get('report_date') else '-'
        lines.append(f"<pre>{d:<10} {int(r.get('website_clicks') or 0):>6}</pre>")
    if not rows:
        lines.append("No checkout click data yet.")
    return "\n".join(lines)


def top_pages_text():
    rows = active_creator_report_rows()[:10]
    lines = [
        "🏆 <b>TOP ACTIVE CREATOR PAGES</b>",
        "",
        "Ranked by enquiries, then checkout clicks.",
        "",
    ]
    if not rows:
        return "\n".join(lines + ["No active creator data yet."])
    for i, r in enumerate(rows, 1):
        name = html.escape(str(r.get('instagram_username') or '').lstrip('@'))
        lines.append(
            f"{i}. <b>@{name}</b> - Enq <b>{int(r.get('enquiries') or 0)}</b> | "
            f"Checkout <b>{int(r.get('website_clicks') or 0)}</b> | "
            f"TG <b>{int(r.get('telegram_clicks') or 0)}</b>"
        )
    return "\n".join(lines)


# ============================================================
# PDF REPORTS
# ============================================================

def build_checkout_pdf(rows):
    output = io.BytesIO()
    generated = datetime.now(timezone.utc)
    total = sum(int(r.get('website_clicks') or 0) for r in rows)
    doc = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=24,
        leftMargin=24,
        topMargin=24,
        bottomMargin=24,
        title=f"BETROXY Checkout Click Report {generated:%Y-%m-%d}",
        author="BETROXY",
    )
    styles = getSampleStyleSheet()
    body = ParagraphStyle('checkoutbody', parent=styles['BodyText'], fontSize=8.5, leading=11)
    story = [
        Paragraph("BETROXY Checkout Page Click Report", styles['Title']),
        Paragraph(
            f"<b>Generated:</b> {generated.strftime('%d %b %Y %H:%M UTC')}<br/>"
            f"<b>Total checkout clicks:</b> {total}<br/>"
            "Only currently active campaign links are counted. Disabled/deleted links are excluded.",
            body,
        ),
        Spacer(1, 8),
    ]
    data = [["Date (UTC)", "Checkout Page Clicks"]]
    for r in rows:
        data.append([
            r['report_date'].strftime('%d %b %Y') if r.get('report_date') else '-',
            int(r.get('website_clicks') or 0),
        ])
    data.append(["TOTAL", total])
    table = Table(data, repeatRows=1, colWidths=[230, 190])
    table.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0),colors.HexColor('#081C15')),
        ('TEXTCOLOR',(0,0),(-1,0),colors.white),
        ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),
        ('FONTNAME',(0,-1),(-1,-1),'Helvetica-Bold'),
        ('BACKGROUND',(0,-1),(-1,-1),colors.HexColor('#D8F3DC')),
        ('GRID',(0,0),(-1,-1),0.4,colors.HexColor('#B9CCC2')),
        ('ALIGN',(1,1),(-1,-1),'CENTER'),
        ('ROWBACKGROUNDS',(0,1),(-1,-2),[colors.HexColor('#F7FBF9'),colors.HexColor('#EDF6F1')]),
        ('TOPPADDING',(0,0),(-1,-1),6),
        ('BOTTOMPADDING',(0,0),(-1,-1),6),
    ]))
    story.append(table)
    doc.build(story)
    output.seek(0)
    return output


def build_creator_traffic_pdf(rows):
    output = io.BytesIO()
    generated = datetime.now(timezone.utc)
    doc = SimpleDocTemplate(
        output,
        pagesize=landscape(A4),
        rightMargin=18,
        leftMargin=18,
        topMargin=20,
        bottomMargin=20,
        title=f"BETROXY Creator Traffic Report {generated:%Y-%m-%d}",
        author="BETROXY",
    )
    styles = getSampleStyleSheet()
    body = ParagraphStyle('creatorbody', parent=styles['BodyText'], fontSize=8, leading=10)
    small = ParagraphStyle('creatorsmall', parent=styles['BodyText'], fontSize=7, leading=8)
    story = [
        Paragraph("BETROXY Creator-wise Traffic Report", styles['Title']),
        Paragraph(
            f"<b>Generated:</b> {generated.strftime('%d %b %Y %H:%M UTC')}<br/>"
            "Lifetime metrics for currently active campaign links only. Disabled/deleted links are excluded.",
            body,
        ),
        Spacer(1, 6),
    ]
    data = [["Creator", "Enquiries", "Unique", "Checkout", "Telegram", "Regs", "Deposits", "Deposit Amount"]]
    for r in rows:
        name = html.escape(str(r.get('instagram_username') or '').lstrip('@'))
        data.append([
            Paragraph(f"@{name}", small),
            int(r.get('enquiries') or 0),
            int(r.get('unique_visitors') or 0),
            int(r.get('website_clicks') or 0),
            int(r.get('telegram_clicks') or 0),
            int(r.get('registrations') or 0),
            int(r.get('deposits') or 0),
            f"{float(r.get('deposit_amount') or 0):,.2f}",
        ])
    table = Table(data, repeatRows=1, colWidths=[160,70,65,70,70,55,60,95])
    table.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0),colors.HexColor('#081C15')),
        ('TEXTCOLOR',(0,0),(-1,0),colors.white),
        ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),
        ('FONTSIZE',(0,0),(-1,-1),7.5),
        ('GRID',(0,0),(-1,-1),0.4,colors.HexColor('#B9CCC2')),
        ('ALIGN',(1,1),(-1,-1),'CENTER'),
        ('VALIGN',(0,0),(-1,-1),'MIDDLE'),
        ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.HexColor('#F7FBF9'),colors.HexColor('#EDF6F1')]),
        ('TOPPADDING',(0,0),(-1,-1),5),
        ('BOTTOMPADDING',(0,0),(-1,-1),5),
    ]))
    story.append(table)
    doc.build(story)
    output.seek(0)
    return output


def build_status_history_pdf(rows):
    output = io.BytesIO()
    generated = datetime.now(timezone.utc)
    doc = SimpleDocTemplate(
        output,
        pagesize=landscape(A4),
        rightMargin=16,
        leftMargin=16,
        topMargin=18,
        bottomMargin=18,
        title=f"BETROXY Date-wise Status History {generated:%Y-%m-%d}",
        author="BETROXY",
    )
    styles = getSampleStyleSheet()
    body = ParagraphStyle('statusbody', parent=styles['BodyText'], fontSize=7.5, leading=9)
    small = ParagraphStyle('statussmall', parent=styles['BodyText'], fontSize=6.5, leading=8)
    story = [
        Paragraph("BETROXY Date-wise Status Checker History", styles['Title']),
        Paragraph(
            f"<b>Generated:</b> {generated.strftime('%d %b %Y %H:%M UTC')}<br/>"
            "Actual calendar check date is shown separately from Campaign Day. Only active creators and completed/manual checks are included.",
            body,
        ),
        Spacer(1, 6),
    ]
    data = [["Check Date", "Creator", "Campaign Day", "Bio", "Extra", "Story", "Story Link", "Result", "Mode"]]
    for r in rows:
        final = bot.verification_final_result(r)
        mode = str(r.get('checker_mode') or '-')
        data.append([
            r['check_date'].strftime('%d %b %Y') if r.get('check_date') else '-',
            Paragraph('@' + html.escape(str(r.get('instagram_username') or '').lstrip('@')), small),
            str(r.get('campaign_day') or '-'),
            bot.verification_status_word(r.get('bio_status'),'bio'),
            bot.verification_status_word(r.get('only_our_link_status'),'only'),
            bot.verification_status_word(r.get('story_status'),'story'),
            bot.verification_status_word(r.get('story_link_status'),'story_link'),
            {'PASS':'PASS','ACTION REQUIRED':'FIX','MANUAL REVIEW':'REVIEW'}[final],
            Paragraph(html.escape(mode), small),
        ])
    table = Table(data, repeatRows=1, colWidths=[78,145,62,55,55,55,65,58,90])
    table.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0),colors.HexColor('#081C15')),
        ('TEXTCOLOR',(0,0),(-1,0),colors.white),
        ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),
        ('FONTSIZE',(0,0),(-1,-1),7),
        ('GRID',(0,0),(-1,-1),0.4,colors.HexColor('#B9CCC2')),
        ('ALIGN',(0,1),(0,-1),'CENTER'),
        ('ALIGN',(2,1),(7,-1),'CENTER'),
        ('VALIGN',(0,0),(-1,-1),'MIDDLE'),
        ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.HexColor('#F7FBF9'),colors.HexColor('#EDF6F1')]),
        ('TOPPADDING',(0,0),(-1,-1),4),
        ('BOTTOMPADDING',(0,0),(-1,-1),4),
    ]))
    story.append(table)
    doc.build(story)
    output.seek(0)
    return output


# ============================================================
# CLEAN MENUS
# ============================================================

def reports_center_keyboard():
    return bot.InlineKeyboardMarkup([
        [
            bot.InlineKeyboardButton("🗓 Date-wise Status PDF", callback_data="reports_status_history"),
            bot.InlineKeyboardButton("🔴 Promoter Negative", callback_data="reports_negative"),
        ],
        [
            bot.InlineKeyboardButton("📈 Day-wise Traffic", callback_data="reports_traffic"),
            bot.InlineKeyboardButton("🎯 Checkout Clicks", callback_data="reports_checkout"),
        ],
        [
            bot.InlineKeyboardButton("👥 Creator-wise Traffic", callback_data="reports_creators"),
            bot.InlineKeyboardButton("📅 Today Summary", callback_data="reports_today"),
        ],
        [
            bot.InlineKeyboardButton("📊 Campaign Summary", callback_data="reports_campaign"),
            bot.InlineKeyboardButton("🏆 Top Pages", callback_data="reports_top"),
        ],
        [
            bot.InlineKeyboardButton("📄 Day 1-7 Compliance", callback_data="reports_compliance_days"),
            bot.InlineKeyboardButton("📥 Export CSV", callback_data="campaign_export"),
        ],
        [
            bot.InlineKeyboardButton("☁️ Apify Usage / Balance", callback_data="verify_hybrid_status"),
        ],
        [bot.InlineKeyboardButton("⬅️ Campaign Tracker", callback_data="campaign_home")],
    ])


def compliance_days_keyboard():
    rows = []
    row = []
    for d in range(1,8):
        row.append(bot.InlineKeyboardButton(f"Day {d}", callback_data=f"verify_pdf:{d}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([bot.InlineKeyboardButton("⬅️ Reports Center", callback_data="reports_home")])
    return bot.InlineKeyboardMarkup(rows)


def v42_campaign_menu():
    return bot.InlineKeyboardMarkup([
        [
            bot.InlineKeyboardButton("☁️ Run Smart Check", callback_data="verify_request_local_run"),
            bot.InlineKeyboardButton("✅ Verification", callback_data="verify_home"),
        ],
        [bot.InlineKeyboardButton("📊 REPORTS CENTER", callback_data="reports_home")],
        [
            bot.InlineKeyboardButton("🔗 Creator Links", callback_data="campaign_links"),
            bot.InlineKeyboardButton("➕ Add Creator", callback_data="campaign_add_single"),
        ],
        [
            bot.InlineKeyboardButton("📚 Bulk Create", callback_data="campaign_add_bulk"),
            bot.InlineKeyboardButton("✅ Sync Final Links", callback_data="campaign_sync_final"),
        ],
        [
            bot.InlineKeyboardButton("🔴 Disable Link", callback_data="campaign_disable_by_link"),
            bot.InlineKeyboardButton("🗑 Delete Link", callback_data="campaign_delete_by_link"),
        ],
        [
            bot.InlineKeyboardButton("🎨 Landing Design", callback_data="theme_home"),
            bot.InlineKeyboardButton("☁️ Apify Status", callback_data="verify_hybrid_status"),
        ],
        [
            bot.InlineKeyboardButton("🔄 Refresh", callback_data="campaign_home"),
            bot.InlineKeyboardButton("⬅️ Admin Panel", callback_data="admin_home"),
        ],
    ])


# ============================================================
# CALLBACKS
# ============================================================

async def v42_callback_handler(update, context):
    q = update.callback_query
    data = q.data if q else ''

    if data == 'reports_home':
        await q.answer()
        if not bot.is_admin(q.from_user.id):
            return
        await q.message.reply_text(
            "📊 <b>BETROXY REPORTS CENTER</b>\n\n"
            "All traffic/enquiry reports below count <b>active campaign links only</b>.\n"
            "Disabled and deleted links are excluded from day-wise and total counts.\n\n"
            "Choose the report you need:",
            parse_mode=bot.ParseMode.HTML,
            reply_markup=reports_center_keyboard(),
        )
        return

    if data == 'reports_compliance_days':
        await q.answer()
        await q.message.reply_text(
            "📄 <b>Compliance PDF - choose campaign day</b>\n\n"
            "The PDF shows Campaign Day separately from the actual calendar check date/time.",
            parse_mode=bot.ParseMode.HTML,
            reply_markup=compliance_days_keyboard(),
        )
        return

    if data == 'reports_negative':
        await q.answer()
        await v41._send_negative_report(q)
        return

    if data == 'reports_traffic':
        await q.answer()
        rows = await asyncio.to_thread(active_daily_report_rows)
        await q.message.reply_text(
            v41.click_report_text(rows).replace('Website / checkout','Checkout page'),
            parse_mode=bot.ParseMode.HTML,
            reply_markup=reports_center_keyboard(),
        )
        pdf = v41.build_click_report_pdf(rows)
        await q.message.reply_document(
            document=pdf,
            filename=f"BETROXY_Daywise_Traffic_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.pdf",
            caption="📈 Day-wise + lifetime traffic/enquiry report - active links only",
            reply_markup=reports_center_keyboard(),
        )
        return

    if data == 'reports_checkout':
        await q.answer()
        rows = await asyncio.to_thread(active_daily_report_rows)
        await q.message.reply_text(
            checkout_report_text(rows),
            parse_mode=bot.ParseMode.HTML,
            reply_markup=reports_center_keyboard(),
        )
        pdf = build_checkout_pdf(rows)
        await q.message.reply_document(
            document=pdf,
            filename=f"BETROXY_Checkout_Clicks_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.pdf",
            caption="🎯 Checkout page clicks - day-wise + total, active links only",
            reply_markup=reports_center_keyboard(),
        )
        return

    if data == 'reports_creators':
        await q.answer()
        rows = await asyncio.to_thread(active_creator_report_rows)
        pdf = build_creator_traffic_pdf(rows)
        await q.message.reply_document(
            document=pdf,
            filename=f"BETROXY_Creator_Traffic_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.pdf",
            caption=f"👥 Creator-wise lifetime traffic report - {len(rows)} active creator links",
            reply_markup=reports_center_keyboard(),
        )
        return

    if data == 'reports_status_history':
        await q.answer()
        rows = await asyncio.to_thread(active_status_history_rows)
        pdf = build_status_history_pdf(rows)
        await q.message.reply_document(
            document=pdf,
            filename=f"BETROXY_Datewise_Status_History_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.pdf",
            caption=f"🗓 Date-wise status checker history - {len(rows)} stored checks",
            reply_markup=reports_center_keyboard(),
        )
        return

    if data == 'reports_today':
        await q.answer()
        text = await asyncio.to_thread(today_summary_text)
        await q.message.reply_text(
            text,
            parse_mode=bot.ParseMode.HTML,
            reply_markup=reports_center_keyboard(),
        )
        return

    if data == 'reports_campaign':
        await q.answer()
        text = await asyncio.to_thread(campaign_summary_text)
        await q.message.reply_text(
            text,
            parse_mode=bot.ParseMode.HTML,
            reply_markup=reports_center_keyboard(),
        )
        return

    if data == 'reports_top':
        await q.answer()
        text = await asyncio.to_thread(top_pages_text)
        await q.message.reply_text(
            text,
            parse_mode=bot.ParseMode.HTML,
            reply_markup=reports_center_keyboard(),
        )
        return

    return await _previous_callback_handler(update, context)


bot.campaign_menu = v42_campaign_menu
bot.callback_handler = v42_callback_handler
bot.logger.warning(
    "V42_REPORTING_CENTER_ACTIVE aligned_menu=on active_only_reports=on "
    "datewise_status=on negative_promoter=on checkout_daywise_total=on creator_pdf=on"
)


if __name__ == '__main__':
    bot.main()
