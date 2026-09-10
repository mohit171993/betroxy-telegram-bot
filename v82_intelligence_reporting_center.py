import csv
import io
import json
import time
from datetime import datetime, timezone

import bot
import v81_promotions_menu_visibility_fix as v81

# V82 - admin-only Reporting & Intelligence Center.
# Preserves V81 public/business UX and begins collecting richer bot events from now on.

v63 = v81.v63
_previous_callback_handler = bot.callback_handler
_previous_start = bot.start
_previous_admin_menu = bot.admin_menu


def _safe_json(value):
    try:
        return json.dumps(value or {}, ensure_ascii=False, default=str)[:8000]
    except Exception:
        return "{}"


def _ensure_intelligence_tables():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS intelligence_leads (
                    telegram_user_id BIGINT PRIMARY KEY,
                    telegram_username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    source TEXT NOT NULL DEFAULT 'unknown',
                    lifecycle_stage TEXT NOT NULL DEFAULT 'new',
                    lead_score INTEGER NOT NULL DEFAULT 20,
                    opt_out BOOLEAN NOT NULL DEFAULT FALSE,
                    ignored_count INTEGER NOT NULL DEFAULT 0,
                    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_event TEXT,
                    last_contact_at TIMESTAMPTZ
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_events (
                    id BIGSERIAL PRIMARY KEY,
                    telegram_user_id BIGINT,
                    chat_id BIGINT,
                    source_bot TEXT NOT NULL DEFAULT 'officialbot',
                    event_type TEXT NOT NULL,
                    event_name TEXT NOT NULL,
                    event_data TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_bot_events_user_time ON bot_events(telegram_user_id, created_at DESC)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_bot_events_type_time ON bot_events(event_type, event_name, created_at DESC)")

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS engagement_subscriptions (
                    telegram_user_id BIGINT PRIMARY KEY,
                    sports_updates BOOLEAN NOT NULL DEFAULT FALSE,
                    promotions BOOLEAN NOT NULL DEFAULT FALSE,
                    quiz_rewards BOOLEAN NOT NULL DEFAULT FALSE,
                    master_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS engagement_log (
                    id BIGSERIAL PRIMARY KEY,
                    telegram_user_id BIGINT,
                    channel TEXT,
                    action_type TEXT NOT NULL,
                    campaign_key TEXT,
                    status TEXT NOT NULL DEFAULT 'queued',
                    detail TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_engagement_log_time ON engagement_log(created_at DESC)")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS intelligence_audit_log (
                    id BIGSERIAL PRIMARY KEY,
                    actor_user_id BIGINT,
                    action TEXT NOT NULL,
                    detail TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

            # Backfill known OfficialBot users.
            cur.execute(
                """
                INSERT INTO intelligence_leads (
                    telegram_user_id, telegram_username, first_name, last_name,
                    source, lifecycle_stage, lead_score, first_seen_at, last_seen_at, last_event
                )
                SELECT telegram_user_id, telegram_username, first_name, last_name,
                       'officialbot', 'engaged', 35, joined_at, joined_at, 'historical_bot_start'
                FROM referrals
                WHERE telegram_user_id IS NOT NULL
                ON CONFLICT (telegram_user_id) DO UPDATE SET
                    telegram_username=COALESCE(EXCLUDED.telegram_username, intelligence_leads.telegram_username),
                    first_name=COALESCE(EXCLUDED.first_name, intelligence_leads.first_name),
                    last_name=COALESCE(EXCLUDED.last_name, intelligence_leads.last_name),
                    last_seen_at=GREATEST(intelligence_leads.last_seen_at, EXCLUDED.last_seen_at)
                """
            )

            # Backfill Telegram Business leads when Business tables exist.
            cur.execute("SELECT to_regclass('public.telegram_business_enquiries') AS t")
            if (cur.fetchone() or {}).get('t'):
                cur.execute(
                    """
                    INSERT INTO intelligence_leads (
                        telegram_user_id, telegram_username, first_name, last_name,
                        source, lifecycle_stage, lead_score, first_seen_at, last_seen_at, last_event
                    )
                    SELECT customer_user_id, customer_username, customer_first_name, customer_last_name,
                           'business', COALESCE(lead_stage, 'engaged'),
                           LEAST(80, 30 + COALESCE(message_count,0) * 2),
                           first_message_at, last_message_at, 'business_enquiry'
                    FROM telegram_business_enquiries
                    WHERE customer_user_id IS NOT NULL
                    ON CONFLICT (telegram_user_id) DO UPDATE SET
                        telegram_username=COALESCE(EXCLUDED.telegram_username, intelligence_leads.telegram_username),
                        first_name=COALESCE(EXCLUDED.first_name, intelligence_leads.first_name),
                        last_name=COALESCE(EXCLUDED.last_name, intelligence_leads.last_name),
                        last_seen_at=GREATEST(intelligence_leads.last_seen_at, EXCLUDED.last_seen_at),
                        lead_score=GREATEST(intelligence_leads.lead_score, EXCLUDED.lead_score),
                        lifecycle_stage=CASE
                            WHEN intelligence_leads.lifecycle_stage='new' THEN EXCLUDED.lifecycle_stage
                            ELSE intelligence_leads.lifecycle_stage END
                    """
                )
        conn.commit()


def _upsert_lead(user, source='officialbot', event='interaction', score_delta=1):
    if not user or int(getattr(user, 'id', 0) or 0) == int(bot.ADMIN_ID):
        return
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO intelligence_leads (
                    telegram_user_id, telegram_username, first_name, last_name,
                    source, lifecycle_stage, lead_score, first_seen_at, last_seen_at, last_event
                ) VALUES (%s,%s,%s,%s,%s,'engaged',%s,NOW(),NOW(),%s)
                ON CONFLICT (telegram_user_id) DO UPDATE SET
                    telegram_username=COALESCE(EXCLUDED.telegram_username, intelligence_leads.telegram_username),
                    first_name=COALESCE(EXCLUDED.first_name, intelligence_leads.first_name),
                    last_name=COALESCE(EXCLUDED.last_name, intelligence_leads.last_name),
                    last_seen_at=NOW(),
                    last_event=EXCLUDED.last_event,
                    lead_score=LEAST(100, intelligence_leads.lead_score + %s)
                """,
                (
                    int(user.id), getattr(user, 'username', None), getattr(user, 'first_name', None),
                    getattr(user, 'last_name', None), source, max(20, 20 + score_delta), event, max(0, score_delta)
                ),
            )
        conn.commit()


def _record_event(user, chat_id, event_type, event_name, data=None, source='officialbot'):
    if not user or int(getattr(user, 'id', 0) or 0) == int(bot.ADMIN_ID):
        return
    try:
        _upsert_lead(user, source=source, event=event_name, score_delta=5 if event_type == 'start' else 1)
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO bot_events
                        (telegram_user_id, chat_id, source_bot, event_type, event_name, event_data)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    """,
                    (int(user.id), int(chat_id) if chat_id else None, source, event_type, str(event_name)[:200], _safe_json(data)),
                )
            conn.commit()
    except Exception:
        bot.logger.exception('V82_EVENT_RECORD_FAILED')


def _scalar(sql, params=()):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone() or {}
            return int(next(iter(row.values())) or 0)


def _row(sql, params=()):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone() or {}


def _rows(sql, params=()):
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def _money(v):
    try:
        return f"{float(v or 0):,.2f}"
    except Exception:
        return "0.00"


def intelligence_menu():
    return bot.InlineKeyboardMarkup([
        [
            bot.InlineKeyboardButton('📊 Executive Overview', callback_data='intel_overview'),
            bot.InlineKeyboardButton('📈 Funnel', callback_data='intel_funnel'),
        ],
        [
            bot.InlineKeyboardButton('👥 Leads & User 360', callback_data='intel_leads'),
            bot.InlineKeyboardButton('🧭 Sources', callback_data='intel_sources'),
        ],
        [
            bot.InlineKeyboardButton('💬 Business Inbox', callback_data='intel_business'),
            bot.InlineKeyboardButton('🤖 Bot Activity', callback_data='intel_bot'),
        ],
        [
            bot.InlineKeyboardButton('📣 Campaigns', callback_data='intel_campaigns'),
            bot.InlineKeyboardButton('📱 Mini App', callback_data='intel_miniapp'),
        ],
        [
            bot.InlineKeyboardButton('🧠 Engagement', callback_data='intel_engagement'),
            bot.InlineKeyboardButton('🛡 System / Audit', callback_data='intel_system'),
        ],
        [bot.InlineKeyboardButton('📥 Export Leads CSV', callback_data='intel_export_leads')],
        [
            bot.InlineKeyboardButton('🔄 Refresh', callback_data='intel_home'),
            bot.InlineKeyboardButton('⬅️ Admin Panel', callback_data='admin_home'),
        ],
    ])


def intelligence_home_text():
    leads = _scalar("SELECT COUNT(*) AS n FROM intelligence_leads")
    today = _scalar("SELECT COUNT(*) AS n FROM intelligence_leads WHERE last_seen_at >= DATE_TRUNC('day', NOW())")
    events = _scalar("SELECT COUNT(*) AS n FROM bot_events WHERE created_at >= DATE_TRUNC('day', NOW())")
    return (
        "📊 <b>BETROXY REPORTING & INTELLIGENCE CENTER</b>\n\n"
        f"Known unique leads: <b>{leads:,}</b>\n"
        f"Active today: <b>{today:,}</b>\n"
        f"Tracked bot actions today: <b>{events:,}</b>\n\n"
        "Every section below is live and admin-only. New bot interactions are now recorded automatically."
    )


def overview_text():
    r = _row(
        """
        SELECT
          (SELECT COUNT(*) FROM intelligence_leads) AS leads,
          (SELECT COUNT(*) FROM intelligence_leads WHERE last_seen_at >= NOW()-INTERVAL '7 days') AS active7,
          (SELECT COUNT(*) FROM intelligence_leads WHERE lead_score >= 70 AND opt_out=FALSE) AS hot,
          (SELECT COUNT(*) FROM intelligence_leads WHERE opt_out=TRUE) AS optouts,
          (SELECT COUNT(*) FROM referrals WHERE joined_at >= DATE_TRUNC('day',NOW())) AS bot_today,
          (SELECT COUNT(*) FROM landing_events WHERE created_at >= DATE_TRUNC('day',NOW())) AS land_today,
          (SELECT COUNT(*) FROM outbound_events WHERE created_at >= DATE_TRUNC('day',NOW())) AS outbound_today,
          (SELECT COUNT(*) FROM conversion_events WHERE event_type='registration' AND created_at >= DATE_TRUNC('day',NOW())) AS regs_today,
          (SELECT COUNT(*) FROM conversion_events WHERE event_type='deposit' AND created_at >= DATE_TRUNC('day',NOW())) AS deps_today,
          (SELECT COALESCE(SUM(amount),0) FROM conversion_events WHERE event_type='deposit' AND created_at >= DATE_TRUNC('day',NOW())) AS dep_amount_today
        """
    )
    return (
        "📊 <b>EXECUTIVE OVERVIEW</b>\n\n"
        f"👥 Unique leads: <b>{int(r.get('leads') or 0):,}</b>\n"
        f"🟢 Active last 7d: <b>{int(r.get('active7') or 0):,}</b>\n"
        f"🔥 Hot leads (70+): <b>{int(r.get('hot') or 0):,}</b>\n"
        f"🔕 Opt-outs: <b>{int(r.get('optouts') or 0):,}</b>\n\n"
        f"🤖 New OfficialBot users today: <b>{int(r.get('bot_today') or 0):,}</b>\n"
        f"🌐 Landing visits today: <b>{int(r.get('land_today') or 0):,}</b>\n"
        f"➡️ Outbound clicks today: <b>{int(r.get('outbound_today') or 0):,}</b>\n"
        f"✅ Registrations today: <b>{int(r.get('regs_today') or 0):,}</b>\n"
        f"💰 Deposits today: <b>{int(r.get('deps_today') or 0):,}</b>\n"
        f"💵 Deposit amount today: <b>{_money(r.get('dep_amount_today'))}</b>"
    )


def funnel_text():
    r = _row(
        """
        SELECT
          (SELECT COUNT(DISTINCT visitor_hash) FROM landing_events WHERE created_at >= NOW()-INTERVAL '30 days' AND visitor_hash IS NOT NULL) AS visitors,
          (SELECT COUNT(*) FROM outbound_events WHERE created_at >= NOW()-INTERVAL '30 days' AND destination='telegram') AS tgclicks,
          (SELECT COUNT(*) FROM referrals WHERE joined_at >= NOW()-INTERVAL '30 days') AS starts,
          (SELECT COUNT(*) FROM conversion_events WHERE created_at >= NOW()-INTERVAL '30 days' AND event_type='registration') AS regs,
          (SELECT COUNT(*) FROM conversion_events WHERE created_at >= NOW()-INTERVAL '30 days' AND event_type='deposit') AS deps
        """
    )
    v = int(r.get('visitors') or 0); c = int(r.get('tgclicks') or 0); s = int(r.get('starts') or 0); reg = int(r.get('regs') or 0); dep = int(r.get('deps') or 0)
    pct = lambda a,b: (a / b * 100.0) if b else 0.0
    return (
        "📈 <b>30-DAY ACQUISITION FUNNEL</b>\n\n"
        f"1️⃣ Unique landing visitors: <b>{v:,}</b>\n"
        f"2️⃣ Telegram clicks: <b>{c:,}</b> ({pct(c,v):.1f}% of visitors)\n"
        f"3️⃣ Bot starts: <b>{s:,}</b> ({pct(s,c):.1f}% of TG clicks)\n"
        f"4️⃣ Registrations: <b>{reg:,}</b> ({pct(reg,s):.1f}% of starts)\n"
        f"5️⃣ Deposits: <b>{dep:,}</b> ({pct(dep,reg):.1f}% of registrations)\n\n"
        "Attribution depends on the tracking events supplied to the current campaign links."
    )


def sources_text():
    rows = _rows(
        """
        SELECT COALESCE(source,'unknown') AS source, COUNT(*) AS n,
               COUNT(*) FILTER (WHERE last_seen_at >= NOW()-INTERVAL '7 days') AS active7,
               ROUND(AVG(lead_score),1) AS avg_score
        FROM intelligence_leads
        GROUP BY COALESCE(source,'unknown')
        ORDER BY n DESC
        LIMIT 12
        """
    )
    lines = ["🧭 <b>LEAD SOURCES</b>", ""]
    for r in rows:
        lines.append(f"• {str(r['source']).title()}: <b>{int(r['n']):,}</b> • 7d {int(r['active7']):,} • score {r['avg_score']}")
    return "\n".join(lines)


def business_text():
    try:
        r = _row(
            """
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE status='open') AS open,
                   COUNT(*) FILTER (WHERE status='resolved') AS resolved,
                   COUNT(*) FILTER (WHERE last_message_at >= DATE_TRUNC('day',NOW())) AS today,
                   COALESCE(SUM(message_count),0) AS messages,
                   COUNT(*) FILTER (WHERE COALESCE(auto_reply_count,0)>0) AS auto_replied
            FROM telegram_business_enquiries
            """
        )
        msg = _row(
            """
            SELECT COUNT(*) FILTER (WHERE direction='inbound') AS inbound,
                   COUNT(*) FILTER (WHERE direction='outbound') AS outbound
            FROM telegram_business_messages
            WHERE created_at >= NOW()-INTERVAL '7 days'
            """
        )
        return (
            "💬 <b>TELEGRAM BUSINESS REPORT</b>\n\n"
            f"Total enquiries: <b>{int(r.get('total') or 0):,}</b>\n"
            f"Open: <b>{int(r.get('open') or 0):,}</b>\n"
            f"Resolved: <b>{int(r.get('resolved') or 0):,}</b>\n"
            f"Active today: <b>{int(r.get('today') or 0):,}</b>\n"
            f"Auto-replied enquiries: <b>{int(r.get('auto_replied') or 0):,}</b>\n\n"
            f"7d inbound messages: <b>{int(msg.get('inbound') or 0):,}</b>\n"
            f"7d outbound messages: <b>{int(msg.get('outbound') or 0):,}</b>"
        )
    except Exception as exc:
        return f"💬 <b>TELEGRAM BUSINESS REPORT</b>\n\nData unavailable: {str(exc)[:180]}"


def bot_activity_text():
    r = _row(
        """
        SELECT COUNT(*) FILTER (WHERE created_at >= DATE_TRUNC('day',NOW())) AS today,
               COUNT(DISTINCT telegram_user_id) FILTER (WHERE created_at >= DATE_TRUNC('day',NOW())) AS users_today,
               COUNT(*) FILTER (WHERE created_at >= NOW()-INTERVAL '7 days') AS week,
               COUNT(DISTINCT telegram_user_id) FILTER (WHERE created_at >= NOW()-INTERVAL '7 days') AS users_week
        FROM bot_events
        """
    )
    top = _rows(
        """
        SELECT event_name, COUNT(*) AS n FROM bot_events
        WHERE created_at >= NOW()-INTERVAL '7 days'
        GROUP BY event_name ORDER BY n DESC LIMIT 8
        """
    )
    lines = [
        "🤖 <b>BOT ACTIVITY</b>", "",
        f"Actions today: <b>{int(r.get('today') or 0):,}</b>",
        f"Users today: <b>{int(r.get('users_today') or 0):,}</b>",
        f"Actions 7d: <b>{int(r.get('week') or 0):,}</b>",
        f"Users 7d: <b>{int(r.get('users_week') or 0):,}</b>", "", "<b>Top tracked actions (7d)</b>"
    ]
    if top:
        lines.extend([f"• {str(x['event_name'])[:45]} — <b>{int(x['n'])}</b>" for x in top])
    else:
        lines.append("• Tracking has just started; new interactions will populate this section.")
    return "\n".join(lines)


def campaign_text():
    r = _row(
        """
        SELECT
          (SELECT COUNT(*) FROM campaign_links WHERE is_active=TRUE) AS active_links,
          (SELECT COUNT(*) FROM landing_events WHERE created_at >= NOW()-INTERVAL '7 days') AS visits7,
          (SELECT COUNT(*) FROM outbound_events WHERE created_at >= NOW()-INTERVAL '7 days') AS out7,
          (SELECT COUNT(*) FROM conversion_events WHERE event_type='registration' AND created_at >= NOW()-INTERVAL '7 days') AS regs7,
          (SELECT COUNT(*) FROM conversion_events WHERE event_type='deposit' AND created_at >= NOW()-INTERVAL '7 days') AS deps7,
          (SELECT COALESCE(SUM(amount),0) FROM conversion_events WHERE event_type='deposit' AND created_at >= NOW()-INTERVAL '7 days') AS amount7
        """
    )
    src = _rows("SELECT COALESCE(source_type,'instagram') AS s, COUNT(*) AS n FROM campaign_links WHERE is_active=TRUE GROUP BY 1 ORDER BY n DESC")
    src_text = " • ".join(f"{str(x['s']).replace('_',' ').title()} {int(x['n'])}" for x in src) or "No active links"
    return (
        "📣 <b>CAMPAIGN PERFORMANCE</b>\n\n"
        f"Active tracking links: <b>{int(r.get('active_links') or 0):,}</b>\n"
        f"Sources: {src_text}\n\n"
        f"7d landing visits: <b>{int(r.get('visits7') or 0):,}</b>\n"
        f"7d outbound clicks: <b>{int(r.get('out7') or 0):,}</b>\n"
        f"7d registrations: <b>{int(r.get('regs7') or 0):,}</b>\n"
        f"7d deposits: <b>{int(r.get('deps7') or 0):,}</b>\n"
        f"7d deposit amount: <b>{_money(r.get('amount7'))}</b>"
    )


def engagement_text():
    r = _row(
        """
        SELECT
          COUNT(*) FILTER (WHERE master_enabled=TRUE) AS enabled,
          COUNT(*) FILTER (WHERE sports_updates=TRUE AND master_enabled=TRUE) AS sports,
          COUNT(*) FILTER (WHERE promotions=TRUE AND master_enabled=TRUE) AS promos,
          COUNT(*) FILTER (WHERE quiz_rewards=TRUE AND master_enabled=TRUE) AS quiz
        FROM engagement_subscriptions
        """
    )
    logs = _row("SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE status='sent') AS sent FROM engagement_log WHERE created_at >= NOW()-INTERVAL '7 days'")
    return (
        "🧠 <b>ENGAGEMENT ENGINE REPORT</b>\n\n"
        f"Enabled subscribers: <b>{int(r.get('enabled') or 0):,}</b>\n"
        f"Sports updates: <b>{int(r.get('sports') or 0):,}</b>\n"
        f"Promotions: <b>{int(r.get('promos') or 0):,}</b>\n"
        f"Quiz & rewards: <b>{int(r.get('quiz') or 0):,}</b>\n\n"
        f"7d engagement jobs: <b>{int(logs.get('total') or 0):,}</b>\n"
        f"7d sent: <b>{int(logs.get('sent') or 0):,}</b>\n\n"
        "The reporting foundation is live. The autonomous engagement worker can use these same tables without changing this dashboard."
    )


def miniapp_text():
    promo = _scalar("SELECT COUNT(*) AS n FROM outbound_events WHERE destination='promotions' AND created_at >= NOW()-INTERVAL '7 days'")
    casino = _scalar("SELECT COUNT(*) AS n FROM outbound_events WHERE destination='casino' AND created_at >= NOW()-INTERVAL '7 days'")
    sports = _scalar("SELECT COUNT(*) AS n FROM outbound_events WHERE destination='sportsbook' AND created_at >= NOW()-INTERVAL '7 days'")
    popular = _scalar("SELECT COUNT(*) AS n FROM outbound_events WHERE destination='popular' AND created_at >= NOW()-INTERVAL '7 days'")
    return (
        "📱 <b>MINI APP / PRODUCT ROUTES</b>\n\n"
        f"Tracked 7d Promotions clicks: <b>{promo:,}</b>\n"
        f"Tracked 7d Casino clicks: <b>{casino:,}</b>\n"
        f"Tracked 7d Sportsbook clicks: <b>{sports:,}</b>\n"
        f"Tracked 7d Popular Games clicks: <b>{popular:,}</b>\n\n"
        "Deep-link opens inside Telegram are only measurable when the Mini App itself sends an event back. Current landing-route events are shown above; richer screen/session analytics can plug into the same Intelligence Center."
    )


def leads_text():
    stats = _row(
        """
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE lead_score>=70 AND opt_out=FALSE) AS hot,
               COUNT(*) FILTER (WHERE lead_score BETWEEN 40 AND 69 AND opt_out=FALSE) AS warm,
               COUNT(*) FILTER (WHERE lead_score<40 AND opt_out=FALSE) AS cold,
               COUNT(*) FILTER (WHERE last_seen_at>=NOW()-INTERVAL '24 hours') AS active24
        FROM intelligence_leads
        """
    )
    recent = _rows("SELECT telegram_user_id, COALESCE(NULLIF(first_name,''), NULLIF(telegram_username,''), telegram_user_id::text) AS name, source, lead_score FROM intelligence_leads ORDER BY last_seen_at DESC LIMIT 8")
    lines = [
        "👥 <b>LEADS & USER 360</b>", "",
        f"Total: <b>{int(stats.get('total') or 0):,}</b>",
        f"🔥 Hot 70+: <b>{int(stats.get('hot') or 0):,}</b>",
        f"🟡 Warm 40–69: <b>{int(stats.get('warm') or 0):,}</b>",
        f"⚪ Cold &lt;40: <b>{int(stats.get('cold') or 0):,}</b>",
        f"🟢 Active 24h: <b>{int(stats.get('active24') or 0):,}</b>", "", "<b>Recent users</b>"
    ]
    for x in recent:
        lines.append(f"• {str(x['name'])[:28]} — {str(x['source'])} — score <b>{int(x['lead_score'])}</b> — <code>{int(x['telegram_user_id'])}</code>")
    return "\n".join(lines)


def user360_text(uid):
    lead = _row("SELECT * FROM intelligence_leads WHERE telegram_user_id=%s", (int(uid),))
    if not lead:
        return "👤 User not found."
    events = _rows("SELECT event_name, created_at FROM bot_events WHERE telegram_user_id=%s ORDER BY created_at DESC LIMIT 6", (int(uid),))
    ev = "\n".join(f"• {str(x['event_name'])[:50]} — {x['created_at'].strftime('%d %b %H:%M')}" for x in events) or "• No tracked bot events yet"
    return (
        "👤 <b>USER 360</b>\n\n"
        f"User ID: <code>{int(uid)}</code>\n"
        f"Name: <b>{lead.get('first_name') or '-'} {lead.get('last_name') or ''}</b>\n"
        f"Username: <b>@{lead.get('telegram_username') or '-'}</b>\n"
        f"Source: <b>{lead.get('source')}</b>\n"
        f"Stage: <b>{lead.get('lifecycle_stage')}</b>\n"
        f"Score: <b>{int(lead.get('lead_score') or 0)}</b>\n"
        f"Opt-out: <b>{'YES' if lead.get('opt_out') else 'NO'}</b>\n"
        f"First seen: <b>{lead.get('first_seen_at')}</b>\n"
        f"Last seen: <b>{lead.get('last_seen_at')}</b>\n\n"
        f"<b>Recent events</b>\n{ev}"
    )


def system_text():
    ctl = _row("SELECT * FROM verifier_control WHERE id=1")
    audit = _scalar("SELECT COUNT(*) AS n FROM intelligence_audit_log WHERE created_at>=NOW()-INTERVAL '7 days'")
    events = _scalar("SELECT COUNT(*) AS n FROM bot_events")
    return (
        "🛡 <b>SYSTEM & AUDIT</b>\n\n"
        "Database: <b>Connected ✅</b>\n"
        f"Analytics events stored: <b>{events:,}</b>\n"
        f"Audit actions 7d: <b>{audit:,}</b>\n"
        f"Instagram verifier state: <b>{ctl.get('status') or 'unknown'}</b>\n"
        f"Last verifier completion: <b>{ctl.get('completed_at') or 'Not available'}</b>\n\n"
        "Railway runtime health is monitored at infrastructure level; this screen shows application/database health available to the bot."
    )


def _admin_menu_with_intelligence():
    markup = _previous_admin_menu()
    rows = [list(r) for r in markup.inline_keyboard]
    if not any(any(getattr(b, 'callback_data', None) == 'intel_home' for b in r) for r in rows):
        rows.insert(0, [bot.InlineKeyboardButton('📊 Reporting & Intelligence Center', callback_data='intel_home')])
    return bot.InlineKeyboardMarkup(rows)


async def v82_start(update, context):
    user = update.effective_user
    chat = update.effective_chat
    try:
        _record_event(user, getattr(chat, 'id', None), 'start', 'bot_start', {'args': list(getattr(context, 'args', []) or [])})
    except Exception:
        pass
    return await _previous_start(update, context)


async def _send_admin_report(q, text, keyboard=None):
    await q.answer()
    if not bot.is_admin(q.from_user.id):
        return
    await q.message.reply_text(
        text,
        parse_mode=bot.ParseMode.HTML,
        reply_markup=keyboard or intelligence_menu(),
        disable_web_page_preview=True,
    )


async def v82_callback_handler(update, context):
    q = update.callback_query
    data = (q.data or '') if q else ''

    # Track non-admin user button activity automatically.
    if q and not bot.is_admin(q.from_user.id):
        try:
            _record_event(q.from_user, q.message.chat_id if q.message else None, 'callback', data, source='officialbot')
        except Exception:
            pass

    if not q or not data.startswith('intel_'):
        return await _previous_callback_handler(update, context)

    if not bot.is_admin(q.from_user.id):
        await q.answer('Admin only', show_alert=True)
        return

    try:
        if data == 'intel_home':
            await _send_admin_report(q, intelligence_home_text())
        elif data == 'intel_overview':
            await _send_admin_report(q, overview_text())
        elif data == 'intel_funnel':
            await _send_admin_report(q, funnel_text())
        elif data == 'intel_leads':
            recent = _rows("SELECT telegram_user_id, COALESCE(NULLIF(first_name,''), NULLIF(telegram_username,''), telegram_user_id::text) AS name FROM intelligence_leads ORDER BY last_seen_at DESC LIMIT 8")
            kb = [[bot.InlineKeyboardButton(f"👤 {str(x['name'])[:32]}", callback_data=f"intel_user:{int(x['telegram_user_id'])}")] for x in recent]
            kb.append([bot.InlineKeyboardButton('⬅️ Intelligence Center', callback_data='intel_home')])
            await _send_admin_report(q, leads_text(), bot.InlineKeyboardMarkup(kb))
        elif data.startswith('intel_user:'):
            uid = int(data.split(':',1)[1])
            await _send_admin_report(q, user360_text(uid), bot.InlineKeyboardMarkup([[bot.InlineKeyboardButton('⬅️ Leads', callback_data='intel_leads')]]))
        elif data == 'intel_sources':
            await _send_admin_report(q, sources_text())
        elif data == 'intel_business':
            await _send_admin_report(q, business_text())
        elif data == 'intel_bot':
            await _send_admin_report(q, bot_activity_text())
        elif data == 'intel_campaigns':
            await _send_admin_report(q, campaign_text())
        elif data == 'intel_miniapp':
            await _send_admin_report(q, miniapp_text())
        elif data == 'intel_engagement':
            await _send_admin_report(q, engagement_text())
        elif data == 'intel_system':
            await _send_admin_report(q, system_text())
        elif data == 'intel_export_leads':
            await q.answer('Preparing CSV…')
            rows = _rows("SELECT telegram_user_id, telegram_username, first_name, last_name, source, lifecycle_stage, lead_score, opt_out, first_seen_at, last_seen_at, last_event FROM intelligence_leads ORDER BY last_seen_at DESC")
            out = io.StringIO()
            w = csv.writer(out)
            w.writerow(['telegram_user_id','telegram_username','first_name','last_name','source','lifecycle_stage','lead_score','opt_out','first_seen_at','last_seen_at','last_event'])
            for r in rows:
                w.writerow([r.get(k) for k in ['telegram_user_id','telegram_username','first_name','last_name','source','lifecycle_stage','lead_score','opt_out','first_seen_at','last_seen_at','last_event']])
            buf = io.BytesIO(out.getvalue().encode('utf-8-sig'))
            buf.name = f"betroxy_leads_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.csv"
            await q.message.reply_document(document=buf, caption='📥 BETROXY Leads & Intelligence export')
            with bot.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("INSERT INTO intelligence_audit_log(actor_user_id, action, detail) VALUES (%s,'export_leads','CSV export')", (q.from_user.id,))
                conn.commit()
        else:
            await q.answer()
    except Exception as exc:
        bot.logger.exception('V82_INTELLIGENCE_CALLBACK_FAILED data=%s', data)
        try:
            await q.message.reply_text(f"⚠️ Report temporarily unavailable.\n<code>{str(exc)[:250]}</code>", parse_mode=bot.ParseMode.HTML)
        except Exception:
            pass


# Apply schema before serving callbacks so the dashboard is immediately usable.
try:
    _ensure_intelligence_tables()
    bot.logger.warning('V82_INTELLIGENCE_SCHEMA ready=on backfill=on')
except Exception:
    bot.logger.exception('V82_INTELLIGENCE_SCHEMA_FAILED')

bot.admin_menu = _admin_menu_with_intelligence
bot.start = v82_start
bot.callback_handler = v82_callback_handler

bot.logger.warning(
    'V82_INTELLIGENCE_REPORTING_CENTER active=on admin_only=on '
    'overview=on funnel=on leads360=on sources=on business=on bot=on '
    'campaigns=on miniapp=on engagement=on system=on csv_export=on event_tracking=on'
)

if __name__ == '__main__':
    v63.apply_signup_cta()
    v63.enable_business_smart_auto_reply()
    bot.logger.warning('V82 polling handover delay=12s')
    time.sleep(12)
    bot.main()
