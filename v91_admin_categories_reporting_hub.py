import csv
import html as html_lib
import io
import re
import threading
import time
import unicodedata
from datetime import datetime, timezone

import bot
import v90_reporting_menu_cleanup as v90

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, LongTable, TableStyle, PageBreak

v89 = v90.v89
v88 = v90.v88
v85 = v90.v85
v83 = v90.v83
v82 = v90.v82

_old_callback_handler = bot.callback_handler
_old_chat_handler = bot.chat_handler


# ============================================================
# ADMIN NAVIGATION - CATEGORY FIRST
# ============================================================

def v91_admin_menu():
    return bot.InlineKeyboardMarkup([
        [bot.InlineKeyboardButton("📊 Reporting Center", callback_data="v91_reports_home")],
        [
            bot.InlineKeyboardButton("💬 Customers & Business", callback_data="v91_admincat:customers"),
            bot.InlineKeyboardButton("📣 Campaigns & Tracking", callback_data="v91_admincat:campaigns"),
        ],
        [
            bot.InlineKeyboardButton("🎁 Rewards & Engagement", callback_data="v91_admincat:rewards"),
            bot.InlineKeyboardButton("👥 Affiliates & Referrals", callback_data="v91_admincat:affiliates"),
        ],
        [bot.InlineKeyboardButton("⚙️ System & Tools", callback_data="v91_admincat:system")],
        [bot.InlineKeyboardButton("⬅️ Customer Menu", callback_data="home")],
    ])


def _legacy_admin_buttons():
    markup = v90.v90_admin_menu()
    return [b for row in markup.inline_keyboard for b in row]


def _button_category(btn):
    text = str(getattr(btn, "text", "") or "").lower()
    cb = str(getattr(btn, "callback_data", "") or "").lower()
    hay = text + " " + cb
    if cb in {"intel_home", "v89_mobile_report"} or "report" in hay or "analytics" in hay or "intelligence" in hay:
        return "reporting"
    if any(k in hay for k in ("business", "customer", "inbox", "enquir", "support")):
        return "customers"
    if any(k in hay for k in ("campaign", "instagram", "pixel", "landing", "creator", "checker", "theme", "verification")):
        return "campaigns"
    if any(k in hay for k in ("reward", "engagement", "autopilot", "quiz", "sports update", "promotion")):
        return "rewards"
    if any(k in hay for k in ("affiliate", "agent", "referral")):
        return "affiliates"
    if cb in {"home", "admin_home"} or "main menu" in text or "customer menu" in text:
        return "skip"
    return "system"


def _category_markup(category):
    rows = []
    for b in _legacy_admin_buttons():
        if _button_category(b) == category:
            rows.append([b])
    if category == "rewards" and not any(any(getattr(x, "callback_data", None) == "v89_reward_center" for x in r) for r in rows):
        rows.insert(0, [bot.InlineKeyboardButton("🎁 Reward Center", callback_data="v89_reward_center")])
    rows.append([bot.InlineKeyboardButton("⬅️ Admin Home", callback_data="admin_home")])
    return bot.InlineKeyboardMarkup(rows)


CATEGORY_TITLES = {
    "customers": ("💬 CUSTOMERS & BUSINESS", "Business inbox, customer conversations and customer-facing administration."),
    "campaigns": ("📣 CAMPAIGNS & TRACKING", "Campaign links, landing pages, Instagram checks, pixels and promotion tools."),
    "rewards": ("🎁 REWARDS & ENGAGEMENT", "Quiz, rewards, engagement automation and communication controls."),
    "affiliates": ("👥 AFFILIATES & REFERRALS", "Affiliate creation, referral tracking and performance controls."),
    "system": ("⚙️ SYSTEM & TOOLS", "Remaining administration, configuration and maintenance tools."),
}


# ============================================================
# REPORT LIBRARY
# ============================================================

def reports_home_markup():
    return bot.InlineKeyboardMarkup([
        [
            bot.InlineKeyboardButton("📊 Executive & Funnel", callback_data="v91_reports:exec"),
            bot.InlineKeyboardButton("🔗 URL / Campaign", callback_data="v91_reports:url"),
        ],
        [
            bot.InlineKeyboardButton("👥 Users & Customers", callback_data="v91_reports:users"),
            bot.InlineKeyboardButton("🎁 Engagement & Rewards", callback_data="v91_reports:engage"),
        ],
        [
            bot.InlineKeyboardButton("📋 Instagram Compliance", callback_data="reports_instagram"),
            bot.InlineKeyboardButton("📈 Landing Reports", callback_data="reports_landing"),
        ],
        [bot.InlineKeyboardButton("🛡 System & Exports", callback_data="v91_reports:system")],
        [bot.InlineKeyboardButton("📦 Download Management Summary PDF", callback_data="v91_summary_pack")],
        [bot.InlineKeyboardButton("⬅️ Admin Home", callback_data="admin_home")],
    ])


def _view_pdf_row(label, key, csv_enabled=False):
    row = [
        bot.InlineKeyboardButton(f"👁 {label}", callback_data=f"v91_rpt:view:{key}"),
        bot.InlineKeyboardButton("⬇️ PDF", callback_data=f"v91_rpt:pdf:{key}"),
    ]
    if csv_enabled:
        row.append(bot.InlineKeyboardButton("CSV", callback_data=f"v91_rpt:csv:{key}"))
    return row


def report_category_markup(category):
    rows = []
    if category == "exec":
        rows += [
            _view_pdf_row("Executive Overview", "overview"),
            _view_pdf_row("30-Day Funnel", "funnel"),
            _view_pdf_row("Lead Sources", "sources"),
        ]
    elif category == "url":
        rows += [
            _view_pdf_row("URL-wise Today", "url_today", True),
            _view_pdf_row("URL-wise 7 Days", "url_7d", True),
            _view_pdf_row("URL-wise 30 Days", "url_30d", True),
            _view_pdf_row("URL-wise Lifetime", "url_lifetime", True),
            _view_pdf_row("Campaign Summary", "campaign"),
        ]
    elif category == "users":
        rows += [
            _view_pdf_row("Leads & User 360 Summary", "leads", True),
            _view_pdf_row("Telegram Business", "business"),
            _view_pdf_row("Bot Activity", "bot"),
            [bot.InlineKeyboardButton("📱 User Mobile Report (PDF)", callback_data="v89_mobile_report")],
        ]
    elif category == "engage":
        rows += [
            _view_pdf_row("Engagement Engine", "engagement"),
            _view_pdf_row("Reward Center", "rewards"),
        ]
    elif category == "system":
        rows += [
            _view_pdf_row("System & Audit", "system"),
            [bot.InlineKeyboardButton("📥 Export Leads CSV", callback_data="intel_export_leads")],
            [bot.InlineKeyboardButton("📦 Management Summary PDF", callback_data="v91_summary_pack")],
        ]
    rows.append([bot.InlineKeyboardButton("⬅️ Reporting Center", callback_data="v91_reports_home")])
    return bot.InlineKeyboardMarkup(rows)


REPORT_CATEGORY_TEXT = {
    "exec": "📊 <b>EXECUTIVE & FUNNEL REPORTS</b>\n\nHigh-level management, funnel and source reporting. Every report can be viewed in Telegram or downloaded as PDF.",
    "url": "🔗 <b>URL / CAMPAIGN PERFORMANCE</b>\n\nURL-wise landing visits, unique users, outbound clicks, registrations and deposits. Choose the required time period and view, PDF or CSV.",
    "users": "👥 <b>USERS & CUSTOMER REPORTS</b>\n\nLead, Telegram Business, bot activity and explicitly shared mobile-number reports.",
    "engage": "🎁 <b>ENGAGEMENT & REWARDS REPORTS</b>\n\nSubscription, engagement-engine and INR reward reporting.",
    "system": "🛡 <b>SYSTEM & EXPORTS</b>\n\nSystem/audit status plus downloadable exports.",
}


def _report_text(key):
    mapping = {
        "overview": v82.overview_text,
        "funnel": v82.funnel_text,
        "sources": v82.sources_text,
        "leads": v82.leads_text,
        "business": v82.business_text,
        "bot": v82.bot_activity_text,
        "campaign": v82.campaign_text,
        "engagement": v82.engagement_text,
        "rewards": v89._reward_center_text,
        "system": v82.system_text,
    }
    fn = mapping.get(key)
    return fn() if fn else None


def _period_config(key):
    return {
        "url_today": ("Today", "e.created_at >= DATE_TRUNC('day', NOW())"),
        "url_7d": ("Last 7 Days", "e.created_at >= NOW()-INTERVAL '7 days'"),
        "url_30d": ("Last 30 Days", "e.created_at >= NOW()-INTERVAL '30 days'"),
        "url_lifetime": ("Lifetime", "TRUE"),
    }.get(key)


def _url_rows(key):
    cfg = _period_config(key)
    if not cfg:
        return [], ""
    label, pred = cfg
    sql = f"""
        SELECT cl.slug, cl.agent_code, cl.instagram_username,
               COALESCE(cl.source_type,'instagram') AS source_type,
               cl.is_active,
               (SELECT COUNT(*) FROM landing_events e WHERE e.agent_code=cl.agent_code AND {pred}) AS visits,
               (SELECT COUNT(DISTINCT e.visitor_hash) FROM landing_events e WHERE e.agent_code=cl.agent_code AND e.visitor_hash IS NOT NULL AND {pred}) AS unique_visitors,
               (SELECT COUNT(*) FROM outbound_events e WHERE e.agent_code=cl.agent_code AND {pred}) AS outbound_clicks,
               (SELECT COUNT(*) FROM outbound_events e WHERE e.agent_code=cl.agent_code AND e.destination='telegram' AND {pred}) AS telegram_clicks,
               (SELECT COUNT(*) FROM outbound_events e WHERE e.agent_code=cl.agent_code AND e.destination='website' AND {pred}) AS website_clicks,
               (SELECT COUNT(*) FROM conversion_events e WHERE e.agent_code=cl.agent_code AND e.event_type='registration' AND {pred}) AS registrations,
               (SELECT COUNT(*) FROM conversion_events e WHERE e.agent_code=cl.agent_code AND e.event_type='deposit' AND {pred}) AS deposits,
               (SELECT COALESCE(SUM(e.amount),0) FROM conversion_events e WHERE e.agent_code=cl.agent_code AND e.event_type='deposit' AND {pred}) AS deposit_amount
        FROM campaign_links cl
        ORDER BY visits DESC, outbound_clicks DESC, cl.slug
    """
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
    return rows, label


def _url_report_text(key):
    rows, label = _url_rows(key)
    totals = {
        "visits": sum(int(r.get("visits") or 0) for r in rows),
        "unique": sum(int(r.get("unique_visitors") or 0) for r in rows),
        "out": sum(int(r.get("outbound_clicks") or 0) for r in rows),
        "regs": sum(int(r.get("registrations") or 0) for r in rows),
        "deps": sum(int(r.get("deposits") or 0) for r in rows),
        "amt": sum(float(r.get("deposit_amount") or 0) for r in rows),
    }
    lines = [
        f"🔗 <b>URL-WISE PERFORMANCE — {html_lib.escape(label.upper())}</b>", "",
        f"Known URLs: <b>{len(rows)}</b>",
        f"Landing visits: <b>{totals['visits']:,}</b>",
        f"Unique visitors*: <b>{totals['unique']:,}</b>",
        f"Outbound clicks: <b>{totals['out']:,}</b>",
        f"Registrations: <b>{totals['regs']:,}</b>",
        f"Deposits: <b>{totals['deps']:,}</b>",
        f"Deposit amount: <b>{totals['amt']:,.2f}</b>", "",
        "<b>URL breakdown</b>"
    ]
    if not rows:
        lines.append("No campaign URLs found.")
    for r in rows[:25]:
        status = "ON" if r.get("is_active") else "OFF"
        lines.append(
            f"• <code>{html_lib.escape(str(r.get('slug') or '-'))}</code> [{status}] — "
            f"V {int(r.get('visits') or 0)} | U {int(r.get('unique_visitors') or 0)} | "
            f"C {int(r.get('outbound_clicks') or 0)} | R {int(r.get('registrations') or 0)} | D {int(r.get('deposits') or 0)}"
        )
    if len(rows) > 25:
        lines.append(f"…and {len(rows)-25} more. Download PDF/CSV for the full report.")
    lines.append("\n*Per-URL unique counts can include the same visitor on more than one URL.")
    return "\n".join(lines)


def _ascii_text(value):
    value = re.sub(r"<[^>]+>", "", str(value or ""))
    value = html_lib.unescape(value).replace("₹", "INR ")
    value = unicodedata.normalize("NFKD", value)
    return value.encode("ascii", "ignore").decode("ascii")


def _text_pdf(key):
    text = _report_text(key)
    if text is None:
        raise ValueError("Unknown report")
    names = {
        "overview": "Executive Overview", "funnel": "30-Day Funnel", "sources": "Lead Sources",
        "leads": "Leads & User 360 Summary", "business": "Telegram Business", "bot": "Bot Activity",
        "campaign": "Campaign Summary", "engagement": "Engagement Engine", "rewards": "Reward Center",
        "system": "System & Audit",
    }
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    styles = getSampleStyleSheet()
    title = ParagraphStyle("v91title", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=18, leading=22, spaceAfter=16)
    body = ParagraphStyle("v91body", parent=styles["BodyText"], fontName="Helvetica", fontSize=9.5, leading=14, spaceAfter=4)
    story = [Paragraph(f"BETROXY - {html_lib.escape(names.get(key,key.title()))}", title),
             Paragraph("Generated: " + datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC"), body), Spacer(1, 8)]
    for line in _ascii_text(text).splitlines():
        if line.strip():
            story.append(Paragraph(html_lib.escape(line), body))
        else:
            story.append(Spacer(1, 5))
    doc.build(story)
    buf.seek(0)
    buf.name = f"BETROXY_{key}_report_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.pdf"
    return buf


def _url_pdf(key):
    rows, label = _url_rows(key)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), rightMargin=24, leftMargin=24, topMargin=28, bottomMargin=28)
    styles = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=styles["BodyText"], fontName="Helvetica", fontSize=6.5, leading=8)
    title = ParagraphStyle("title", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=16, leading=19)
    story = [Paragraph(f"BETROXY - URL-wise Performance ({html_lib.escape(label)})", title),
             Paragraph("Generated: " + datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC"), small), Spacer(1, 8)]
    data = [["#", "URL / Slug", "Source", "Status", "Visits", "Unique", "Clicks", "TG", "Web", "Regs", "Deps", "Dep Amt"]]
    for i, r in enumerate(rows, 1):
        data.append([
            str(i), Paragraph(html_lib.escape(str(r.get("slug") or "-"))[:80], small),
            Paragraph(html_lib.escape(str(r.get("source_type") or "-"))[:30], small),
            "Active" if r.get("is_active") else "Off", str(int(r.get("visits") or 0)),
            str(int(r.get("unique_visitors") or 0)), str(int(r.get("outbound_clicks") or 0)),
            str(int(r.get("telegram_clicks") or 0)), str(int(r.get("website_clicks") or 0)),
            str(int(r.get("registrations") or 0)), str(int(r.get("deposits") or 0)),
            f"{float(r.get('deposit_amount') or 0):,.2f}",
        ])
    table = LongTable(data, repeatRows=1, colWidths=[22, 125, 62, 42, 42, 42, 42, 34, 34, 34, 34, 62])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1F2937")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,-1), 6.5),
        ("GRID", (0,0), (-1,-1), 0.25, colors.HexColor("#D1D5DB")),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#F7F7F7")]),
        ("LEFTPADDING", (0,0), (-1,-1), 3), ("RIGHTPADDING", (0,0), (-1,-1), 3),
        ("TOPPADDING", (0,0), (-1,-1), 3), ("BOTTOMPADDING", (0,0), (-1,-1), 3),
    ]))
    story.append(table)
    doc.build(story)
    buf.seek(0)
    safe = key.replace("url_", "")
    buf.name = f"BETROXY_URL_Wise_{safe}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.pdf"
    return buf


def _url_csv(key):
    rows, label = _url_rows(key)
    out = io.StringIO()
    w = csv.writer(out)
    headers = ["slug","agent_code","source","source_type","active","visits","unique_visitors","outbound_clicks","telegram_clicks","website_clicks","registrations","deposits","deposit_amount"]
    w.writerow(headers)
    for r in rows:
        w.writerow([
            r.get("slug"), r.get("agent_code"), r.get("instagram_username"), r.get("source_type"), r.get("is_active"),
            r.get("visits"), r.get("unique_visitors"), r.get("outbound_clicks"), r.get("telegram_clicks"),
            r.get("website_clicks"), r.get("registrations"), r.get("deposits"), r.get("deposit_amount"),
        ])
    buf = io.BytesIO(out.getvalue().encode("utf-8-sig"))
    buf.name = f"BETROXY_URL_Wise_{key.replace('url_','')}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.csv"
    return buf


def _leads_csv():
    rows = v82._rows("SELECT telegram_user_id, telegram_username, first_name, last_name, source, lifecycle_stage, lead_score, opt_out, first_seen_at, last_seen_at, last_event FROM intelligence_leads ORDER BY last_seen_at DESC")
    out = io.StringIO()
    w = csv.writer(out)
    headers = ["telegram_user_id","telegram_username","first_name","last_name","source","lifecycle_stage","lead_score","opt_out","first_seen_at","last_seen_at","last_event"]
    w.writerow(headers)
    for r in rows:
        w.writerow([r.get(k) for k in headers])
    buf = io.BytesIO(out.getvalue().encode("utf-8-sig"))
    buf.name = f"BETROXY_Leads_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.csv"
    return buf


def _summary_pack_pdf():
    keys = ["overview", "funnel", "sources", "business", "bot", "campaign", "engagement", "rewards", "system"]
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=34, leftMargin=34, topMargin=34, bottomMargin=34)
    styles = getSampleStyleSheet()
    title = ParagraphStyle("sumtitle", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=18, leading=22, spaceAfter=12)
    heading = ParagraphStyle("sumhead", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=13, leading=16, spaceBefore=8, spaceAfter=6)
    body = ParagraphStyle("sumbody", parent=styles["BodyText"], fontName="Helvetica", fontSize=8.5, leading=12, spaceAfter=3)
    story = [Paragraph("BETROXY - Management Reporting Summary", title),
             Paragraph("Generated: " + datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC"), body), Spacer(1, 8)]
    for idx, key in enumerate(keys):
        text = _ascii_text(_report_text(key) or "")
        lines = [x for x in text.splitlines() if x.strip()]
        if lines:
            story.append(Paragraph(html_lib.escape(lines[0]), heading))
            for line in lines[1:]:
                story.append(Paragraph(html_lib.escape(line), body))
        if idx in {2,5}:
            story.append(PageBreak())
    doc.build(story)
    buf.seek(0)
    buf.name = f"BETROXY_Management_Summary_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.pdf"
    return buf


def _report_back_markup(key):
    cat = "exec"
    if key.startswith("url_") or key == "campaign": cat = "url"
    elif key in {"leads","business","bot"}: cat = "users"
    elif key in {"engagement","rewards"}: cat = "engage"
    elif key == "system": cat = "system"
    rows = []
    if key.startswith("url_"):
        rows.append([
            bot.InlineKeyboardButton("⬇️ PDF", callback_data=f"v91_rpt:pdf:{key}"),
            bot.InlineKeyboardButton("CSV", callback_data=f"v91_rpt:csv:{key}"),
        ])
    else:
        rows.append([bot.InlineKeyboardButton("⬇️ Download PDF", callback_data=f"v91_rpt:pdf:{key}")])
        if key == "leads":
            rows[0].append(bot.InlineKeyboardButton("CSV", callback_data="v91_rpt:csv:leads"))
    rows.append([bot.InlineKeyboardButton("⬅️ Reports", callback_data=f"v91_reports:{cat}")])
    return bot.InlineKeyboardMarkup(rows)


# ============================================================
# NATURAL-LANGUAGE REPORT ROUTING FOR ADMIN AI
# ============================================================

def _match_report_intent(text):
    low = " ".join(str(text or "").lower().split())
    if not low:
        return None
    wants_report = any(k in low for k in ("report", "analytics", "stats", "performance", "summary", "data"))
    if not wants_report:
        return None
    if any(k in low for k in ("url wise", "url-wise", "per url", "by url", "link wise", "link-wise", "creator wise", "landing wise")):
        if "today" in low: return "url_today"
        if any(k in low for k in ("7 day", "7d", "week")): return "url_7d"
        if any(k in low for k in ("30 day", "30d", "month")): return "url_30d"
        return "url_lifetime"
    if any(k in low for k in ("mobile", "phone", "contact number")): return "mobile"
    if "funnel" in low: return "funnel"
    if any(k in low for k in ("executive", "overview", "management")): return "overview"
    if "source" in low: return "sources"
    if any(k in low for k in ("business", "inbox", "enquiry", "enquiries")): return "business"
    if any(k in low for k in ("lead", "user 360", "users")): return "leads"
    if "bot" in low and any(k in low for k in ("activity", "usage", "user")): return "bot"
    if any(k in low for k in ("reward", "giftport", "voucher")): return "rewards"
    if any(k in low for k in ("engagement", "autopilot", "subscription")): return "engagement"
    if "campaign" in low: return "campaign"
    if any(k in low for k in ("system", "audit")): return "system"
    return None


async def _deliver_ai_report(update, context, raw):
    msg = update.effective_message
    key = _match_report_intent(raw)
    if not key:
        return False
    low = str(raw or "").lower()
    wants_download = any(k in low for k in ("download", "pdf", "file", "export"))
    wants_csv = "csv" in low or "excel" in low
    if key == "mobile":
        await v89._send_mobile_report(msg)
        return True
    if key.startswith("url_"):
        if wants_csv:
            f = _url_csv(key)
            await msg.reply_document(document=f, filename=f.name, caption="🔗 BETROXY URL-wise report")
        elif wants_download:
            f = _url_pdf(key)
            await msg.reply_document(document=f, filename=f.name, caption="🔗 BETROXY URL-wise report")
        else:
            await msg.reply_text(_url_report_text(key), parse_mode=bot.ParseMode.HTML, reply_markup=_report_back_markup(key), disable_web_page_preview=True)
        return True
    if wants_csv and key == "leads":
        f = _leads_csv()
        await msg.reply_document(document=f, filename=f.name, caption="👥 BETROXY Leads export")
        return True
    if wants_download or wants_csv:
        f = _text_pdf(key)
        await msg.reply_document(document=f, filename=f.name, caption=f"BETROXY {key.replace('_',' ').title()} report")
    else:
        await msg.reply_text(_report_text(key), parse_mode=bot.ParseMode.HTML, reply_markup=_report_back_markup(key), disable_web_page_preview=True)
    return True


async def v91_ai_command(update, context):
    user = update.effective_user
    msg = update.effective_message
    if not user or not msg:
        return
    if not bot.is_admin(user.id):
        return await v89._ai_command(update, context)
    raw = " ".join(getattr(context, "args", []) or []).strip()
    if raw and await _deliver_ai_report(update, context, raw):
        bot.logger.warning("V91_AI_REPORT uid=%s text=%s", user.id, raw[:180])
        return
    return await v89._ai_command(update, context)


async def v91_chat_handler(update, context):
    user = update.effective_user
    msg = update.effective_message
    raw = str(getattr(msg, "text", "") or "").strip() if msg else ""
    if user and msg and bot.is_admin(user.id) and raw:
        if await _deliver_ai_report(update, context, raw):
            bot.logger.warning("V91_AI_TEXT_REPORT uid=%s text=%s", user.id, raw[:180])
            return
    return await _old_chat_handler(update, context)


# ============================================================
# CALLBACKS
# ============================================================

async def _show(q, text, markup):
    await q.answer()
    try:
        await q.message.edit_text(text, parse_mode=bot.ParseMode.HTML, reply_markup=markup, disable_web_page_preview=True)
    except Exception:
        await q.message.reply_text(text, parse_mode=bot.ParseMode.HTML, reply_markup=markup, disable_web_page_preview=True)


async def v91_callback_handler(update, context):
    q = update.callback_query
    data = str(q.data or "") if q else ""
    if not q or not (data.startswith("v91_") or data.startswith("v91_rpt:")):
        return await _old_callback_handler(update, context)
    if not bot.is_admin(q.from_user.id):
        await q.answer("Admin only", show_alert=True)
        return

    if data == "v91_reports_home":
        await _show(q,
            "📊 <b>BETROXY REPORTING CENTER</b>\n\nReports are grouped by purpose so you do not have to search through the bot. Open any category, view the report instantly, or download it as PDF/CSV where available.",
            reports_home_markup())
        return

    if data.startswith("v91_admincat:"):
        cat = data.split(":",1)[1]
        title, desc = CATEGORY_TITLES.get(cat, ("ADMIN TOOLS", ""))
        await _show(q, f"<b>{title}</b>\n\n{desc}\n\nChoose an option:", _category_markup(cat))
        return

    if data.startswith("v91_reports:"):
        cat = data.split(":",1)[1]
        await _show(q, REPORT_CATEGORY_TEXT.get(cat, "📊 <b>REPORTS</b>"), report_category_markup(cat))
        return

    if data == "v91_summary_pack":
        await q.answer("Preparing PDF...")
        f = _summary_pack_pdf()
        await q.message.reply_document(document=f, filename=f.name, caption="📦 BETROXY Management Reporting Summary")
        return

    if data.startswith("v91_rpt:"):
        _, action, key = data.split(":",2)
        if action == "view":
            text = _url_report_text(key) if key.startswith("url_") else _report_text(key)
            await _show(q, text or "Report unavailable.", _report_back_markup(key))
            return
        if action == "pdf":
            await q.answer("Preparing PDF...")
            f = _url_pdf(key) if key.startswith("url_") else _text_pdf(key)
            await q.message.reply_document(document=f, filename=f.name, caption=f"BETROXY {key.replace('_',' ').title()} report")
            return
        if action == "csv":
            await q.answer("Preparing CSV...")
            if key.startswith("url_"):
                f = _url_csv(key)
            elif key == "leads":
                f = _leads_csv()
            else:
                await q.message.reply_text("CSV is not required for this summary report; use PDF download.")
                return
            await q.message.reply_document(document=f, filename=f.name, caption=f"BETROXY {key.replace('_',' ').title()} export")
            return

    return await _old_callback_handler(update, context)


# Apply category-first admin and report-aware AI routing.
bot.admin_menu = v91_admin_menu
v82.intelligence_menu = reports_home_markup
bot.callback_handler = v91_callback_handler
bot.chat_handler = v91_chat_handler
# V89's Application.add_handler wrapper resolves this module attribute at runtime.
v89._ai_command = v91_ai_command

bot.logger.warning(
    "V91_ADMIN_CATEGORIES_REPORTING_HUB active=on admin_category_first=on reporting_library=on "
    "mobile_report_under_reporting=on urlwise=view+pdf+csv ai_report_router=on management_pack=on"
)


if __name__ == "__main__":
    v88.v63.apply_signup_cta()
    v85._enable_smart_reply_without_reset()
    threading.Thread(target=v83._worker_loop, name="betroxy-engagement-worker", daemon=True).start()
    bot.logger.warning("V91 polling handover delay=12s")
    time.sleep(12)
    bot.main()
