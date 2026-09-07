import asyncio
import html
import io
import logging
import os
import time
from datetime import datetime, timezone

import requests

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak

import bot
import v40_apify_only_bootstrap as v40

# V41 keeps V40 Apify-only architecture and adds:
# - fail-closed Apify account allowance check before paid actors start
# - before/after usage display
# - date-aware compliance PDFs
# - promoter report containing confirmed negative outcomes only
# - day-wise + lifetime click/enquiry report for ACTIVE links only

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram.request").setLevel(logging.WARNING)

_previous_callback_handler = bot.callback_handler


def _money(value):
    try:
        return f"${float(value):,.2f}"
    except Exception:
        return "—"


def apify_account_allowance():
    """Return current monthly usage limit/usage using Apify's official API.

    This is deliberately fail-closed: if we cannot verify the account allowance,
    Smart Check does not launch any paid actor.
    """
    if not bot.APIFY_TOKEN:
        raise RuntimeError("APIFY_TOKEN is missing in Railway Variables.")

    r = requests.get(
        "https://api.apify.com/v2/users/me/limits",
        headers={
            "Authorization": f"Bearer {bot.APIFY_TOKEN}",
            "Accept": "application/json",
        },
        timeout=20,
    )
    if not r.ok:
        raise RuntimeError(f"Apify balance check HTTP {r.status_code}")

    data = (r.json() or {}).get("data") or {}
    limits = data.get("limits") or {}
    current = data.get("current") or {}
    max_usage = limits.get("maxMonthlyUsageUsd")
    used = current.get("monthlyUsageUsd")
    if max_usage is None or used is None:
        raise RuntimeError("Apify did not return monthly usage allowance data")

    max_usage = float(max_usage)
    used = float(used)
    remaining = max(0.0, max_usage - used)
    cycle = data.get("monthlyUsageCycle") or {}
    return {
        "limit": max_usage,
        "used": used,
        "remaining": remaining,
        "cycle_start": cycle.get("startAt"),
        "cycle_end": cycle.get("endAt"),
    }


def _current_rows_with_verification():
    rows = []
    for cl in v40._active_instagram_rows():
        day = bot.current_campaign_day(cl)
        v = bot.get_verification_row(cl["id"], day) or {}
        row = dict(cl)
        row.update({
            "campaign_link_id": cl["id"],
            "campaign_day": day,
            "bio_status": v.get("bio_status") or "pending",
            "only_our_link_status": v.get("only_our_link_status") or "pending",
            "story_status": v.get("story_status") or "pending",
            "story_link_status": v.get("story_link_status") or "pending",
            "checked_at": v.get("checked_at"),
            "auto_checked_at": v.get("auto_checked_at"),
            "checker_mode": v.get("checker_mode"),
            "auto_check_status": v.get("auto_check_status") or "pending",
            "auto_check_detail": v.get("auto_check_detail"),
            "detected_bio_links": v.get("detected_bio_links"),
            "detected_story_count": v.get("detected_story_count") or 0,
        })
        rows.append(row)
    return rows


def _confirmed_negative(row):
    return any(
        (row.get(k) or "pending") in {"missing", "issue"}
        for k in (
            "bio_status",
            "only_our_link_status",
            "story_status",
            "story_link_status",
        )
    )


def _issue_summary(row):
    issues = []
    if (row.get("bio_status") or "pending") in {"missing", "issue"}:
        issues.append("Bio link missing/issue")
    if (row.get("only_our_link_status") or "pending") in {"missing", "issue"}:
        issues.append("Extra/incorrect bio link")
    if (row.get("story_status") or "pending") in {"missing", "issue"}:
        issues.append("Story missing/issue")
    if (row.get("story_link_status") or "pending") in {"missing", "issue"}:
        issues.append("Story link missing/issue")
    return "; ".join(issues) or "—"


def _checked_text(row, with_time=True):
    checked = bot.verification_last_checked(row)
    if not checked:
        return "Not checked"
    if with_time:
        return checked.strftime("%d %b %Y %H:%M UTC")
    return checked.strftime("%d %b %Y")


def _campaign_date_for_row(row, day):
    created = row.get("created_at")
    if not created:
        return "—"
    try:
        from datetime import timedelta
        return (created.date() + timedelta(days=max(0, int(day) - 1))).strftime("%d %b %Y")
    except Exception:
        return "—"


def build_datewise_verification_pdf(rows, day):
    """Standard compliance PDF with explicit calendar dates.

    Campaign Day and actual Last Checked date are separate so a label such as
    Day 6/7 can never be mistaken for 6 September.
    """
    day = max(1, min(int(day), 7))
    output = io.BytesIO()
    generated = datetime.now(timezone.utc)
    doc = SimpleDocTemplate(
        output,
        pagesize=landscape(A4),
        rightMargin=18,
        leftMargin=18,
        topMargin=20,
        bottomMargin=20,
        title=f"BETROXY Compliance Day {day} - {generated:%Y-%m-%d}",
        author="BETROXY",
    )
    styles = getSampleStyleSheet()
    body = ParagraphStyle("v41body", parent=styles["BodyText"], fontSize=8, leading=10)
    small = ParagraphStyle("v41small", parent=styles["BodyText"], fontSize=6.6, leading=8)

    counts = {"PASS": 0, "ACTION REQUIRED": 0, "MANUAL REVIEW": 0}
    for r in rows:
        counts[bot.verification_final_result(r)] += 1

    story = [
        Paragraph(f"BETROXY Status Checker Report — Campaign Day {day}/7", styles["Title"]),
        Paragraph(
            f"<b>Generated:</b> {generated.strftime('%d %b %Y %H:%M UTC')} &nbsp;&nbsp; "
            f"<b>Total:</b> {len(rows)} &nbsp;&nbsp; <b>PASS:</b> {counts['PASS']} &nbsp;&nbsp; "
            f"<b>ACTION REQUIRED:</b> {counts['ACTION REQUIRED']} &nbsp;&nbsp; "
            f"<b>MANUAL REVIEW:</b> {counts['MANUAL REVIEW']}<br/>"
            "Campaign Day is the 1–7 campaign sequence. Check Date is the actual calendar date/time of the result.",
            body,
        ),
        Spacer(1, 6),
    ]

    data = [["Creator", "Campaign Date", "Bio", "Extra", "Story", "S.Link", "Result", "Check Date / Time"]]
    for r in rows:
        username = str(r.get("instagram_username") or "").strip().lstrip("@")
        source_url = bot.verification_source_url(r)
        final_short = {
            "PASS": "PASS",
            "ACTION REQUIRED": "FIX",
            "MANUAL REVIEW": "REVIEW",
        }[bot.verification_final_result(r)]
        data.append([
            Paragraph(f'<link href="{html.escape(source_url, quote=True)}">@{html.escape(username)}</link>', small),
            _campaign_date_for_row(r, day),
            bot.verification_status_word(r.get("bio_status"), "bio"),
            bot.verification_status_word(r.get("only_our_link_status"), "only"),
            bot.verification_status_word(r.get("story_status"), "story"),
            bot.verification_status_word(r.get("story_link_status"), "story_link"),
            final_short,
            _checked_text(r, with_time=True),
        ])

    table = Table(data, repeatRows=1, colWidths=[165, 82, 55, 55, 55, 55, 58, 125])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#081C15")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("GRID", (0, 0), (-1, -1), .4, colors.HexColor("#B9CCC2")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 1), (-1, -1), "CENTER"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#F7FBF9"), colors.HexColor("#EDF6F1")]),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(table)
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "PASS = all four checks verified. FIX = one or more confirmed negative outcomes. "
        "REVIEW = one or more checks could not be confirmed.", body,
    ))
    doc.build(story)
    output.seek(0)
    return output


def build_negative_promoter_pdf(rows):
    output = io.BytesIO()
    generated = datetime.now(timezone.utc)
    doc = SimpleDocTemplate(
        output,
        pagesize=landscape(A4),
        rightMargin=18,
        leftMargin=18,
        topMargin=20,
        bottomMargin=20,
        title=f"BETROXY Promoter Action Report {generated:%Y-%m-%d}",
        author="BETROXY",
    )
    styles = getSampleStyleSheet()
    body = ParagraphStyle("negbody", parent=styles["BodyText"], fontSize=8, leading=10)
    small = ParagraphStyle("negsmall", parent=styles["BodyText"], fontSize=6.5, leading=8)
    story = [
        Paragraph("BETROXY Promoter Action Report — Negative Outcomes Only", styles["Title"]),
        Paragraph(
            f"<b>Generated:</b> {generated.strftime('%d %b %Y %H:%M UTC')} &nbsp;&nbsp; "
            f"<b>Creators requiring correction:</b> {len(rows)}<br/>"
            "This report intentionally excludes creators that passed and excludes unresolved-only rows unless a confirmed negative exists.",
            body,
        ),
        Spacer(1, 6),
    ]
    data = [["Creator", "Day", "Check Date", "Bio", "Extra", "Story", "S.Link", "Required correction"]]
    for r in rows:
        username = str(r.get("instagram_username") or "").strip().lstrip("@")
        source_url = bot.verification_source_url(r)
        data.append([
            Paragraph(f'<link href="{html.escape(source_url, quote=True)}">@{html.escape(username)}</link>', small),
            str(r.get("campaign_day") or "—"),
            _checked_text(r, with_time=False),
            bot.verification_status_word(r.get("bio_status"), "bio"),
            bot.verification_status_word(r.get("only_our_link_status"), "only"),
            bot.verification_status_word(r.get("story_status"), "story"),
            bot.verification_status_word(r.get("story_link_status"), "story_link"),
            Paragraph(html.escape(_issue_summary(r)), small),
        ])
    table = Table(data, repeatRows=1, colWidths=[145, 34, 72, 48, 48, 48, 48, 250])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#6B1010")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("GRID", (0, 0), (-1, -1), .4, colors.HexColor("#D6B6B6")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 1), (6, -1), "CENTER"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#FFF9F9"), colors.HexColor("#FCEEEE")]),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(table)
    story.append(Spacer(1, 8))
    story.append(Paragraph("Promoter action: correct the listed issue(s), then send updated proof/re-run verification.", body))
    doc.build(story)
    output.seek(0)
    return output


def negative_promoter_keyboard(rows):
    buttons = []
    for r in rows[:40]:
        username = str(r.get("instagram_username") or "").strip().lstrip("@")
        buttons.append([
            bot.InlineKeyboardButton(f"📸 @{username[:20]}", url=bot.verification_source_url(r)),
            bot.InlineKeyboardButton("🔗 Assigned Link", url=f"{bot.PUBLIC_BASE_URL}/{r['slug']}"),
        ])
    buttons.append([bot.InlineKeyboardButton("⬅️ Campaign Tracker", callback_data="campaign_home")])
    return bot.InlineKeyboardMarkup(buttons)


def active_click_report_rows():
    """Clicks/enquiries grouped by UTC date for currently active links only.

    Disabled links are excluded by cl.is_active=TRUE. Deleted links cannot join
    campaign_links and therefore are also excluded from every count, including
    lifetime totals.
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
                        COUNT(DISTINCT le.visitor_hash) FILTER (WHERE le.visitor_hash IS NOT NULL) AS unique_visitors
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
                    COALESCE(l.d, o.d) AS report_date,
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


def click_report_totals(rows):
    keys = [
        "enquiries", "unique_visitors", "website_clicks", "telegram_clicks",
        "casino_clicks", "sportsbook_clicks", "popular_clicks", "promotion_clicks",
    ]
    return {k: sum(int(r.get(k) or 0) for r in rows) for k in keys}


def click_report_text(rows):
    totals = click_report_totals(rows)
    lines = [
        "📈 <b>BETROXY Click / Enquiry Report</b>",
        "",
        "Counts include <b>active campaign links only</b>. Disabled/deleted links are excluded.",
        "Website/checkout = click from the Batraxy landing page to the Betroxy website.",
        "",
        "<b>Lifetime total — active links</b>",
        f"Enquiries / landing visits: <b>{totals['enquiries']}</b>",
        f"Unique visitors: <b>{totals['unique_visitors']}</b>",
        f"Website / checkout clicks: <b>{totals['website_clicks']}</b>",
        f"Telegram clicks: <b>{totals['telegram_clicks']}</b>",
        "",
        "<b>Day-wise (latest 14 days with activity)</b>",
        "<pre>Date        Enq  Web  TG</pre>",
    ]
    for r in rows[:14]:
        d = r["report_date"].strftime("%d-%m-%Y") if r.get("report_date") else "—"
        lines.append(f"<pre>{d:<10} {int(r['enquiries'] or 0):>4} {int(r['website_clicks'] or 0):>4} {int(r['telegram_clicks'] or 0):>3}</pre>")
    if not rows:
        lines.append("No click data yet.")
    return "\n".join(lines)


def build_click_report_pdf(rows):
    output = io.BytesIO()
    generated = datetime.now(timezone.utc)
    totals = click_report_totals(rows)
    doc = SimpleDocTemplate(
        output,
        pagesize=landscape(A4),
        rightMargin=18,
        leftMargin=18,
        topMargin=20,
        bottomMargin=20,
        title=f"BETROXY Click Report {generated:%Y-%m-%d}",
        author="BETROXY",
    )
    styles = getSampleStyleSheet()
    body = ParagraphStyle("clickbody", parent=styles["BodyText"], fontSize=8, leading=10)
    story = [
        Paragraph("BETROXY Click / Enquiry Report — Active Links Only", styles["Title"]),
        Paragraph(
            f"<b>Generated:</b> {generated.strftime('%d %b %Y %H:%M UTC')}<br/>"
            f"<b>Lifetime enquiries:</b> {totals['enquiries']} &nbsp;&nbsp; "
            f"<b>Unique:</b> {totals['unique_visitors']} &nbsp;&nbsp; "
            f"<b>Website/checkout clicks:</b> {totals['website_clicks']} &nbsp;&nbsp; "
            f"<b>Telegram clicks:</b> {totals['telegram_clicks']}<br/>"
            "Disabled and deleted campaign links are excluded from both day-wise and lifetime totals.",
            body,
        ),
        Spacer(1, 6),
    ]
    data = [["Date (UTC)", "Enquiries", "Unique", "Website / Checkout", "Telegram", "Casino", "Sportsbook", "Popular", "Promotions"]]
    for r in rows:
        data.append([
            r["report_date"].strftime("%d %b %Y") if r.get("report_date") else "—",
            int(r.get("enquiries") or 0),
            int(r.get("unique_visitors") or 0),
            int(r.get("website_clicks") or 0),
            int(r.get("telegram_clicks") or 0),
            int(r.get("casino_clicks") or 0),
            int(r.get("sportsbook_clicks") or 0),
            int(r.get("popular_clicks") or 0),
            int(r.get("promotion_clicks") or 0),
        ])
    data.append([
        "TOTAL", totals["enquiries"], totals["unique_visitors"], totals["website_clicks"],
        totals["telegram_clicks"], totals["casino_clicks"], totals["sportsbook_clicks"],
        totals["popular_clicks"], totals["promotion_clicks"],
    ])
    table = Table(data, repeatRows=1, colWidths=[88, 63, 63, 105, 65, 58, 68, 58, 68])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#081C15")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#D8F3DC")),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("GRID", (0, 0), (-1, -1), .4, colors.HexColor("#B9CCC2")),
        ("ALIGN", (1, 1), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.HexColor("#F7FBF9"), colors.HexColor("#EDF6F1")]),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(table)
    doc.build(story)
    output.seek(0)
    return output


def v41_campaign_menu():
    return bot.InlineKeyboardMarkup([
        [
            bot.InlineKeyboardButton("☁️ Run Smart Check", callback_data="verify_request_local_run"),
            bot.InlineKeyboardButton("🟡 Auto Report", callback_data="verify_auto_report:1"),
        ],
        [
            bot.InlineKeyboardButton("✅ Verification Center", callback_data="verify_home"),
            bot.InlineKeyboardButton("📄 Compliance PDF", callback_data="verify_pdf:1"),
        ],
        [
            bot.InlineKeyboardButton("📈 Click Report", callback_data="campaign_click_report"),
            bot.InlineKeyboardButton("📅 Today", callback_data="campaign_today"),
        ],
        [
            bot.InlineKeyboardButton("📊 Campaign Report", callback_data="campaign_report"),
            bot.InlineKeyboardButton("🏆 Top Pages", callback_data="campaign_top"),
        ],
        [
            bot.InlineKeyboardButton("🔗 Creator Links", callback_data="campaign_links"),
            bot.InlineKeyboardButton("✅ Sync Final Links", callback_data="campaign_sync_final"),
        ],
        [
            bot.InlineKeyboardButton("➕ Add Creator", callback_data="campaign_add_single"),
            bot.InlineKeyboardButton("📚 Bulk Create", callback_data="campaign_add_bulk"),
        ],
        [
            bot.InlineKeyboardButton("📥 Export CSV", callback_data="campaign_export"),
            bot.InlineKeyboardButton("☁️ Apify Status", callback_data="verify_hybrid_status"),
        ],
        [
            bot.InlineKeyboardButton("🔴 Disable Link", callback_data="campaign_disable_by_link"),
            bot.InlineKeyboardButton("🗑 Delete Link", callback_data="campaign_delete_by_link"),
        ],
        [
            bot.InlineKeyboardButton("🎨 Landing Design", callback_data="theme_home"),
            bot.InlineKeyboardButton("🔄 Refresh Dashboard", callback_data="campaign_home"),
        ],
        [bot.InlineKeyboardButton("⬅️ Admin Panel", callback_data="admin_home")],
    ])


async def _send_negative_report(q):
    current_rows = _current_rows_with_verification()
    negatives = [r for r in current_rows if _confirmed_negative(r)]
    if not negatives:
        await q.message.reply_text(
            "🟢 <b>Promoter Action Report</b>\n\nNo confirmed negative outcomes in the latest current-day results.",
            parse_mode=bot.ParseMode.HTML,
            reply_markup=bot.campaign_menu(),
        )
        return

    lines = [
        "🔴 <b>PROMOTER ACTION REPORT — NEGATIVE ONLY</b>",
        "",
        f"Creators requiring correction: <b>{len(negatives)}</b>",
        "PASS creators are not shown.",
        "",
    ]
    for r in negatives:
        username = html.escape(str(r.get("instagram_username") or "").strip().lstrip("@"))
        lines.append(f"• <b>@{username}</b> — {html.escape(_issue_summary(r))}")
    text = "\n".join(lines)
    if len(text) > 3900:
        text = text[:3850] + "\n…Full list is in the attached PDF."
    await q.message.reply_text(
        text,
        parse_mode=bot.ParseMode.HTML,
        reply_markup=negative_promoter_keyboard(negatives),
        disable_web_page_preview=True,
    )
    pdf = build_negative_promoter_pdf(negatives)
    await q.message.reply_document(
        document=pdf,
        filename=f"BETROXY_Promoter_Negative_Only_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.pdf",
        caption="🔴 Promoter report — confirmed negative outcomes only",
        reply_markup=bot.campaign_menu(),
    )


async def v41_callback_handler(update, context):
    q = update.callback_query
    data = q.data if q else ""

    if data == "verify_request_local_run":
        await q.answer()
        if not bot.is_admin(q.from_user.id):
            return
        if not bot.APIFY_TOKEN:
            await q.message.reply_text(
                "❌ <b>Apify is not connected.</b>\n\nAPIFY_TOKEN is missing in Railway Variables.",
                parse_mode=bot.ParseMode.HTML,
                reply_markup=bot.campaign_menu(),
            )
            return

        # Check account usage BEFORE changing run state or launching either paid actor.
        try:
            before = await asyncio.to_thread(apify_account_allowance)
        except Exception as exc:
            bot.logger.exception("APIFY_BALANCE_PRECHECK_FAILED")
            await q.message.reply_text(
                "🛑 <b>Smart Check blocked — balance could not be verified</b>\n\n"
                f"<code>{html.escape(str(exc)[:1000])}</code>\n\n"
                "No Apify actor was launched.",
                parse_mode=bot.ParseMode.HTML,
                reply_markup=bot.campaign_menu(),
            )
            return

        if before["remaining"] <= 0.0:
            await q.message.reply_text(
                "🛑 <b>Smart Check blocked — no Apify allowance remaining</b>\n\n"
                f"Monthly limit: <b>{_money(before['limit'])}</b>\n"
                f"Used: <b>{_money(before['used'])}</b>\n"
                f"Remaining: <b>{_money(before['remaining'])}</b>\n\n"
                "No paid actor was launched.",
                parse_mode=bot.ParseMode.HTML,
                reply_markup=bot.campaign_menu(),
            )
            return

        if not v40._begin_apify_run():
            await q.message.reply_text(
                "⏳ <b>Apify Smart Check already running</b>\n\nA second paid run was not started.",
                parse_mode=bot.ParseMode.HTML,
                reply_markup=bot.campaign_menu(),
            )
            return

        await q.message.reply_text(
            "☁️ <b>Apify-only Smart Check started</b>\n\n"
            f"Monthly limit: <b>{_money(before['limit'])}</b>\n"
            f"Used before run: <b>{_money(before['used'])}</b>\n"
            f"Remaining before run: <b>{_money(before['remaining'])}</b>\n\n"
            "Same-day full PASS creators are skipped. Browser checker is OFF. Automatic schedule is OFF.",
            parse_mode=bot.ParseMode.HTML,
            reply_markup=bot.campaign_menu(),
        )

        try:
            result = await asyncio.to_thread(v40.run_apify_smart_check_current_days)
            warning = bool(result.get("warnings"))
            # Give Apify a brief moment to reflect usage in account limits.
            await asyncio.sleep(1.5)
            try:
                after = await asyncio.to_thread(apify_account_allowance)
            except Exception:
                after = None

            cost_line = "Usage after run: unavailable"
            if after:
                delta = max(0.0, after["used"] - before["used"])
                cost_line = (
                    f"Used after run: <b>{_money(after['used'])}</b>\n"
                    f"Recorded usage increase: <b>{_money(delta)}</b>\n"
                    f"Remaining after run: <b>{_money(after['remaining'])}</b>"
                )

            summary = (
                f"Apify-only complete: total={result['total']}, checked={result['checked']}, "
                f"same-day PASS skipped={result['skipped_pass']}, profile_records={result['profile_records']}, "
                f"story_records={result['story_records']}, PASS={result['pass_count']}, "
                f"FIX={result['issue_count']}, REVIEW={result['manual_count']}"
            )
            v40._finish_apify_run(summary, warning=warning)

            warning_text = ""
            if warning:
                warning_text = "\n\n⚠️ " + html.escape(" | ".join(result["warnings"])[:1000])

            await q.message.reply_text(
                "✅ <b>Apify Smart Check complete</b>\n\n"
                f"Active Instagram creators: <b>{result['total']}</b>\n"
                f"Checked now: <b>{result['checked']}</b>\n"
                f"Same-day full PASS skipped: <b>{result['skipped_pass']}</b>\n"
                f"Profile records: <b>{result['profile_records']}</b>\n"
                f"Story records: <b>{result['story_records']}</b>\n\n"
                f"PASS: <b>{result['pass_count']}</b>\n"
                f"ACTION REQUIRED: <b>{result['issue_count']}</b>\n"
                f"MANUAL REVIEW: <b>{result['manual_count']}</b>\n\n"
                + cost_line + warning_text,
                parse_mode=bot.ParseMode.HTML,
                reply_markup=bot.campaign_menu(),
            )

            # Immediately produce the promoter-facing negative-only report.
            await _send_negative_report(q)
        except Exception as exc:
            bot.logger.exception("V41_APIFY Smart Check failed")
            v40._finish_apify_run(f"Apify-only failed: {type(exc).__name__}: {exc}", warning=True)
            await q.message.reply_text(
                "❌ <b>Apify Smart Check failed</b>\n\n"
                f"<code>{html.escape(str(exc)[:1400])}</code>",
                parse_mode=bot.ParseMode.HTML,
                reply_markup=bot.campaign_menu(),
            )
        return

    if data == "verify_hybrid_status":
        await q.answer()
        control = bot.get_local_verifier_control() or {}
        try:
            bal = await asyncio.to_thread(apify_account_allowance)
            balance_text = (
                f"Monthly limit: <b>{_money(bal['limit'])}</b>\n"
                f"Used: <b>{_money(bal['used'])}</b>\n"
                f"Remaining: <b>{_money(bal['remaining'])}</b>"
            )
        except Exception as exc:
            balance_text = f"Balance check: <b>⚠️ unavailable</b> — {html.escape(str(exc)[:300])}"
        last = control.get("completed_at")
        last_text = last.strftime("%d %b %Y %H:%M UTC") if last else "Never"
        summary = html.escape(str(control.get("result_summary") or "No Apify-only run completed yet"))
        await q.message.reply_text(
            "☁️ <b>BETROXY Apify Checker</b>\n\n"
            f"Status: <b>{html.escape(str(control.get('status') or 'idle'))}</b>\n"
            f"Last completed: <b>{last_text}</b>\n"
            "Mode: <b>Apify only — manual</b>\n"
            "Browser checker: <b>OFF</b>\n"
            "Automatic schedule: <b>OFF</b>\n\n"
            + balance_text + "\n\n"
            f"Last summary: {summary}",
            parse_mode=bot.ParseMode.HTML,
            reply_markup=bot.campaign_menu(),
        )
        return

    if data == "campaign_click_report":
        await q.answer()
        rows = await asyncio.to_thread(active_click_report_rows)
        await q.message.reply_text(
            click_report_text(rows),
            parse_mode=bot.ParseMode.HTML,
            reply_markup=bot.campaign_menu(),
        )
        pdf = build_click_report_pdf(rows)
        await q.message.reply_document(
            document=pdf,
            filename=f"BETROXY_Click_Report_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.pdf",
            caption="📈 Day-wise + lifetime click/enquiry report — active links only",
            reply_markup=bot.campaign_menu(),
        )
        return

    return await _previous_callback_handler(update, context)


bot.campaign_menu = v41_campaign_menu
bot.build_verification_pdf = build_datewise_verification_pdf
bot.callback_handler = v41_callback_handler
bot.logger.warning(
    "V41_APIFY_BALANCE_REPORTS_ACTIVE balance_guard=fail_closed datewise_pdf=on "
    "negative_promoter_report=on click_report=daywise+lifetime active_links_only=on"
)


if __name__ == "__main__":
    bot.main()
