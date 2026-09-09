import html

import bot
import v63_clean_public_banner_bootstrap as v63

# V64 - @BetroxyOfficialBot admin analytics.
# Counts unique Telegram users recorded in referrals, including direct starts.


def _analytics_stats():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(DISTINCT telegram_user_id) AS n FROM referrals")
            total = int(cur.fetchone()["n"] or 0)
            cur.execute("SELECT COUNT(DISTINCT telegram_user_id) AS n FROM referrals WHERE joined_at >= DATE_TRUNC('day', NOW())")
            today = int(cur.fetchone()["n"] or 0)
            cur.execute("SELECT COUNT(DISTINCT telegram_user_id) AS n FROM referrals WHERE joined_at >= NOW() - INTERVAL '7 days'")
            week = int(cur.fetchone()["n"] or 0)
            cur.execute("SELECT COUNT(DISTINCT telegram_user_id) AS n FROM referrals WHERE joined_at >= NOW() - INTERVAL '30 days'")
            month = int(cur.fetchone()["n"] or 0)
            cur.execute("SELECT COUNT(DISTINCT telegram_user_id) AS n FROM referrals WHERE agent_id IS NULL")
            direct = int(cur.fetchone()["n"] or 0)
            cur.execute("SELECT COUNT(DISTINCT telegram_user_id) AS n FROM referrals WHERE agent_id IS NOT NULL")
            referred = int(cur.fetchone()["n"] or 0)
            cur.execute("""
                SELECT COALESCE(a.code, 'direct') AS source, COUNT(DISTINCT r.telegram_user_id) AS n
                FROM referrals r LEFT JOIN agents a ON a.id=r.agent_id
                GROUP BY COALESCE(a.code, 'direct') ORDER BY n DESC LIMIT 8
            """)
            sources = cur.fetchall()
    return total, today, week, month, direct, referred, sources


def _analytics_text():
    total, today, week, month, direct, referred, sources = _analytics_stats()
    lines = [
        "📊 <b>BETROXY BOT ANALYTICS</b>", "",
        f"👥 Total unique users: <b>{total:,}</b>",
        f"🟢 Today: <b>{today:,}</b>",
        f"📅 Last 7 days: <b>{week:,}</b>",
        f"🗓 Last 30 days: <b>{month:,}</b>", "",
        f"🔗 Direct starts: <b>{direct:,}</b>",
        f"🤝 Referral starts: <b>{referred:,}</b>",
    ]
    if sources:
        lines += ["", "<b>Top sources</b>"]
        for row in sources:
            label = "Direct / no referral" if row["source"] == "direct" else str(row["source"])
            lines.append(f"• {html.escape(label)} — <b>{int(row['n']):,}</b>")
    lines += ["", "<i>Unique user = one Telegram user recorded when they first started/interacted with @BetroxyOfficialBot.</i>"]
    return "\n".join(lines)


def _analytics_menu():
    return bot.InlineKeyboardMarkup([
        [bot.InlineKeyboardButton("🔄 Refresh Analytics", callback_data="bot_analytics")],
        [bot.InlineKeyboardButton("⬅️ Admin Panel", callback_data="admin_home")],
    ])


# Add analytics to the existing admin panel without removing any current controls.
_original_admin_menu = bot.admin_menu

def v64_admin_menu():
    old = _original_admin_menu()
    rows = list(old.inline_keyboard)
    rows.insert(1, [bot.InlineKeyboardButton("📊 Bot Analytics", callback_data="bot_analytics")])
    return bot.InlineKeyboardMarkup(rows)

bot.admin_menu = v64_admin_menu


async def bot_analytics_callback(update, context):
    q = update.callback_query
    await q.answer()
    if not bot.is_admin(q.from_user.id):
        return
    await q.message.reply_text(
        _analytics_text(),
        parse_mode=bot.ParseMode.HTML,
        reply_markup=_analytics_menu(),
    )


# Register with a high-priority dedicated callback handler before bot.main builds polling.
_original_main = bot.main

def v64_main():
    original_build = bot.Application.builder
    # bot.main owns application creation; patching add_handler there is unnecessarily risky.
    # Instead expose callback for v63/bot bootstrap registration below.
    return _original_main()

# Extend callback routing through the existing callback handler wrapper if present.
_previous_callback_handler = getattr(bot, "callback_handler", None)
if _previous_callback_handler:
    async def v64_callback_handler(update, context):
        q = update.callback_query
        if q and q.data == "bot_analytics":
            return await bot_analytics_callback(update, context)
        return await _previous_callback_handler(update, context)
    bot.callback_handler = v64_callback_handler

bot.logger.warning("V64_BOT_ANALYTICS active=on unique_users=referrals")

if __name__ == "__main__":
    bot.main()
