"""Premium transparent Batraxy homepage overlay.

Only replaces the public "/" Flask view at startup. Existing creator landing
routes, bot flows, CRM, quizzes, rewards and admin functions remain unchanged.
"""
import hashlib
import hmac
import os

from flask import Response, jsonify, redirect, request, send_file

AFFILIATE_DESTINATION = (
    "https://app.affiliar.co/api/r/SNLINK?"
    "to=https%3A%2F%2Fbetroxy.com%2F%3Fmodal%3Dauth%26tab%3Dregister"
)
TELEGRAM_DESTINATION = "https://t.me/BetroxyBot"

# Keep public buttons on batraxy.com so every outbound choice is counted before
# redirecting to the disclosed destination.
SIGNUP_URL = "/batraxy/go/affiliate"
TELEGRAM_URL = "/batraxy/go/telegram"
ANALYTICS_SLUG = "batraxy-home"
ANALYTICS_CODE = "batraxy_home"

HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Batraxy | Continue to Betroxy</title>
  <meta
    name="description"
    content="Batraxy is a transparent referral page. Continue to Betroxy registration through a clearly disclosed affiliate link."
  />
  <style>
    :root{
      --bg:#071018;
      --bg2:#0d1824;
      --card:rgba(255,255,255,0.08);
      --card-border:rgba(255,255,255,0.12);
      --text:#f5f7fb;
      --muted:#b9c2cf;
      --accent:#d4af37;
      --white:#ffffff;
      --shadow:0 20px 60px rgba(0,0,0,0.35);
      --radius:22px;
    }
    *{box-sizing:border-box}
    html,body{margin:0;padding:0}
    body{
      font-family:Inter,Arial,Helvetica,sans-serif;
      color:var(--text);
      background:
        radial-gradient(circle at top right, rgba(37,211,102,0.14), transparent 28%),
        radial-gradient(circle at top left, rgba(212,175,55,0.14), transparent 25%),
        linear-gradient(180deg,var(--bg),var(--bg2));
      min-height:100vh;
    }
    a{text-decoration:none}
    .container{width:min(1180px,calc(100% - 32px));margin:0 auto}
    .topbar{display:flex;justify-content:space-between;align-items:center;padding:22px 0}
    .brand{font-size:28px;font-weight:800;letter-spacing:.08em;color:var(--white)}
    .pill{
      padding:10px 16px;border:1px solid var(--card-border);border-radius:999px;
      background:rgba(255,255,255,0.05);color:var(--muted);font-size:13px;
      backdrop-filter:blur(12px)
    }
    .hero{
      display:grid;grid-template-columns:1.15fr .85fr;gap:24px;align-items:stretch;
      padding:24px 0
    }
    .hero-main,.hero-side,.info-box,.feature,.trust-item{
      background:var(--card);border:1px solid var(--card-border);border-radius:var(--radius);
      backdrop-filter:blur(16px);box-shadow:var(--shadow)
    }
    .hero-main{padding:52px 46px;position:relative;overflow:hidden}
    .hero-main::before{
      content:"";position:absolute;inset:auto -80px -80px auto;width:240px;height:240px;
      background:radial-gradient(circle,rgba(212,175,55,0.22),transparent 70%);
      border-radius:50%;pointer-events:none
    }
    .eyebrow{
      display:inline-flex;align-items:center;gap:8px;padding:8px 14px;border-radius:999px;
      font-size:13px;color:#e7eaf0;background:rgba(255,255,255,0.07);
      border:1px solid rgba(255,255,255,0.12);margin-bottom:18px
    }
    h1{margin:0 0 18px;font-size:56px;line-height:1.02;letter-spacing:-.03em;max-width:760px}
    .accent{color:var(--accent)}
    .hero p{margin:0;color:var(--muted);font-size:18px;line-height:1.7}
    .cta-wrap{margin-top:30px}
    .cta-row{display:flex;gap:12px;flex-wrap:wrap}
    .cta{
      display:inline-flex;align-items:center;justify-content:center;gap:10px;padding:17px 28px;
      border-radius:16px;background:linear-gradient(135deg,var(--accent),#f3d46d);color:#111;
      font-weight:800;font-size:17px;box-shadow:0 12px 30px rgba(212,175,55,.28);
      transition:transform .18s ease,box-shadow .18s ease
    }
    .cta.telegram{
      background:linear-gradient(135deg,#2AABEE,#168ACD);color:#fff;
      box-shadow:0 12px 30px rgba(42,171,238,.24)
    }
    .cta:hover{transform:translateY(-2px);box-shadow:0 16px 36px rgba(212,175,55,.35)}
    .cta.telegram:hover{box-shadow:0 16px 36px rgba(42,171,238,.34)}
    .cta-note{margin-top:12px;color:#d8dee7;font-size:13px;line-height:1.55}
    .hero-tagline{margin:0;color:#d8dee7;font-size:20px;line-height:1.55;max-width:620px}
    .hero-graphic-cards{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:28px}
    .graphic-card{
      background:rgba(255,255,255,.065);border:1px solid rgba(255,255,255,.1);
      border-radius:18px;padding:17px 15px;min-height:132px
    }
    .graphic-icon{
      width:44px;height:44px;border-radius:14px;display:flex;align-items:center;
      justify-content:center;background:rgba(255,255,255,.08);font-size:21px;margin-bottom:12px
    }
    .graphic-title{font-size:15px;font-weight:800;margin-bottom:6px}
    .graphic-text{font-size:13px;line-height:1.45;color:var(--muted)}
    .hero-side{padding:28px;display:flex;flex-direction:column;justify-content:space-between;gap:18px}
    .hero-card-title{font-size:20px;font-weight:700;margin-bottom:10px}
    .hero-card-text{color:var(--muted);font-size:15px;line-height:1.7}
    .mini-grid{display:grid;gap:14px}
    .trust-strip{margin-top:8px;display:grid;grid-template-columns:repeat(3,1fr);gap:16px}
    .trust-item{padding:22px 18px}
    .trust-item h3{margin:0 0 8px;font-size:17px}
    .trust-item p{margin:0;font-size:14px;color:var(--muted);line-height:1.6}
    .section{margin-top:34px}
    .info-box{padding:28px 26px}
    .info-box h2{margin:0 0 10px;font-size:24px;color:var(--white)}
    .info-box p{margin:0;color:var(--muted);line-height:1.75;font-size:16px}
    .features{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;margin-top:18px}
    .feature{padding:24px 22px}
    .feature .icon{
      width:48px;height:48px;border-radius:14px;display:flex;align-items:center;
      justify-content:center;background:rgba(255,255,255,0.08);font-size:22px;margin-bottom:14px
    }
    .feature h3{margin:0 0 10px;font-size:18px}
    .feature p{margin:0;color:var(--muted);font-size:15px;line-height:1.7}
    .footer{
      margin:38px 0 28px;padding:22px 0 10px;border-top:1px solid rgba(255,255,255,.08);
      color:#9aa6b5;font-size:13px;line-height:1.8
    }
    .footer strong{color:#dbe2eb}
    @media(max-width:980px){
      .hero{grid-template-columns:1fr}
      .trust-strip,.features,.hero-graphic-cards{grid-template-columns:1fr}
      h1{font-size:42px}
      .hero-main{padding:38px 26px}
    }
    @media(max-width:640px){
      .topbar{gap:12px;flex-direction:column;align-items:flex-start}
      h1{font-size:34px}
      .hero p{font-size:16px}
      .cta-row{flex-direction:column}
      .cta{width:100%}
    }
  </style>
</head>
<body>
  <div class="container">
    <div class="topbar">
      <div class="brand">BATRAXY</div>
      <div class="pill">18+ Only • Transparent Referral Page</div>
    </div>

    <section class="hero">
      <div class="hero-main">
        <div class="eyebrow">Premium Access • Two Ways to Continue</div>
        <h1>Start Your Journey with <span class="accent">Batraxy</span></h1>
        <div class="hero-tagline">Premium access. Smooth experience. One clear next step.</div>

        <div class="cta-wrap">
          <div class="cta-row">
            <a class="cta" href="__SIGNUP_URL__" rel="sponsored nofollow noopener">
              Continue to Betroxy
            </a>
            <a class="cta telegram" href="__TELEGRAM_URL__" rel="noopener">
              Play on Telegram Bot
            </a>
          </div>
          <div class="cta-note">
            Website signup continues to <strong>Betroxy.com</strong> • Telegram opens <strong>@BetroxyBot</strong>
          </div>
        </div>

        <div class="hero-graphic-cards">
          <div class="graphic-card">
            <div class="graphic-icon">⚡</div>
            <div class="graphic-title">Fast Access</div>
            <div class="graphic-text">Choose web signup or Telegram in one tap.</div>
          </div>
          <div class="graphic-card">
            <div class="graphic-icon">✦</div>
            <div class="graphic-title">Premium Experience</div>
            <div class="graphic-text">Clean, modern and mobile-first interface.</div>
          </div>
          <div class="graphic-card">
            <div class="graphic-icon">↗</div>
            <div class="graphic-title">Clear Destination</div>
            <div class="graphic-text">Continue to Betroxy or open @BetroxyBot.</div>
          </div>
        </div>
      </div>

      <div class="hero-side">
        <div>
          <div class="hero-card-title">Important Notice</div>
          <div class="hero-card-text">
            This page does not complete signup itself. Registration, eligibility,
            account access, payments, platform rules, and usage terms are handled
            directly by <strong>Betroxy</strong>.
          </div>
        </div>

        <div class="mini-grid">
          <div class="trust-item">
            <h3>Clear Destination</h3>
            <p>You are clearly informed that the button continues to Betroxy registration.</p>
          </div>
          <div class="trust-item">
            <h3>Affiliate Disclosure</h3>
            <p>The registration button uses an affiliate-tracking link before opening Betroxy.</p>
          </div>
        </div>
      </div>
    </section>

    <section class="trust-strip">
      <div class="trust-item">
        <h3>Direct Path</h3>
        <p>Simple, clean route to Betroxy without hiding the destination.</p>
      </div>
      <div class="trust-item">
        <h3>Transparent Redirect</h3>
        <p>Batraxy is presented as a referral page, not as a separate signup platform.</p>
      </div>
      <div class="trust-item">
        <h3>18+ Only</h3>
        <p>Adults only. Please act responsibly and follow local laws where applicable.</p>
      </div>
    </section>

    <section class="section">
      <div class="info-box">
        <h2>Why continue through Batraxy?</h2>
        <p>
          We keep the path simple and fully disclosed. The purpose of this page is to
          explain exactly where the button goes before you continue. When you click,
          you proceed to Betroxy through a transparent affiliate link.
        </p>
      </div>

      <div class="features">
        <div class="feature">
          <div class="icon">→</div>
          <h3>Simple Signup Path</h3>
          <p>
            No confusion about where you are going. One clear action takes you to the
            Betroxy registration page.
          </p>
        </div>
        <div class="feature">
          <div class="icon">✓</div>
          <h3>Transparent Referral</h3>
          <p>
            The page clearly states that Batraxy is a referral page and that the
            registration process is completed on Betroxy.
          </p>
        </div>
        <div class="feature">
          <div class="icon">★</div>
          <h3>Premium Experience</h3>
          <p>
            A polished layout, premium styling, and a strong call to action create a
            cleaner and more attractive first impression.
          </p>
        </div>
      </div>
    </section>

    <footer class="footer">
      <strong>Batraxy</strong> is a transparent referral page. By clicking the button
      above, you leave this page and continue to <strong>Betroxy</strong>. The signup
      button uses an affiliate-tracking link. <strong>18+ only.</strong> Availability
      may vary by location. Please play responsibly and comply with applicable laws.
    </footer>
  </div>
</body>
</html>""".replace("__SIGNUP_URL__", SIGNUP_URL).replace("__TELEGRAM_URL__", TELEGRAM_URL)


def _visitor_fingerprint():
    forwarded = request.headers.get("X-Forwarded-For", "")
    ip = forwarded.split(",")[0].strip() if forwarded else (request.remote_addr or "")
    ua = request.headers.get("User-Agent", "")[:500]
    raw = f"{ip}|{ua}".encode("utf-8", "ignore")
    return hashlib.sha256(raw).hexdigest(), ua


def _record_home_visit(bot):
    fingerprint, ua = _visitor_fingerprint()
    referer = request.headers.get("Referer", "")[:1000]
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            # Match the existing creator-page semantics: a reload from the same
            # device within 30 minutes is not counted as a new landing visit.
            cur.execute(
                """
                SELECT 1 FROM landing_events
                WHERE slug=%s AND visitor_hash=%s
                  AND created_at >= NOW() - INTERVAL '30 minutes'
                LIMIT 1
                """,
                (ANALYTICS_SLUG, fingerprint),
            )
            if not cur.fetchone():
                cur.execute(
                    """
                    INSERT INTO landing_events
                        (slug, agent_code, visitor_hash, user_agent, referer)
                    VALUES (%s,%s,%s,%s,%s)
                    """,
                    (ANALYTICS_SLUG, ANALYTICS_CODE, fingerprint, ua, referer),
                )
        conn.commit()


def _record_home_click(bot, destination):
    if destination not in {"affiliate", "telegram"}:
        return False
    fingerprint, _ = _visitor_fingerprint()
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            # Ignore accidental double-taps while still preserving intentional
            # repeat clicks later in the same session.
            cur.execute(
                """
                SELECT 1 FROM outbound_events
                WHERE slug=%s AND visitor_hash=%s AND destination=%s
                  AND created_at >= NOW() - INTERVAL '5 seconds'
                LIMIT 1
                """,
                (ANALYTICS_SLUG, fingerprint, destination),
            )
            if not cur.fetchone():
                cur.execute(
                    """
                    INSERT INTO outbound_events
                        (slug, agent_code, destination, visitor_hash)
                    VALUES (%s,%s,%s,%s)
                    """,
                    (ANALYTICS_SLUG, ANALYTICS_CODE, destination, fingerprint),
                )
        conn.commit()
    return True


def _batraxy_period_metrics(bot, interval_sql):
    visit_where = "agent_code=%s"
    click_where = "agent_code=%s"
    if interval_sql:
        visit_where += f" AND created_at >= {interval_sql}"
        click_where += f" AND created_at >= {interval_sql}"

    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    COUNT(*) AS visits,
                    COUNT(DISTINCT visitor_hash)
                        FILTER (WHERE visitor_hash IS NOT NULL) AS unique_visitors,
                    MAX(created_at) AS last_visit_at
                FROM landing_events
                WHERE {visit_where}
                """,
                (ANALYTICS_CODE,),
            )
            visits = cur.fetchone() or {}

            cur.execute(
                f"""
                SELECT
                    COUNT(*) FILTER (WHERE destination='affiliate') AS affiliate_clicks,
                    COUNT(*) FILTER (WHERE destination='telegram') AS telegram_clicks,
                    COUNT(DISTINCT visitor_hash)
                        FILTER (WHERE destination='affiliate' AND visitor_hash IS NOT NULL)
                        AS unique_affiliate_clickers,
                    COUNT(DISTINCT visitor_hash)
                        FILTER (WHERE destination='telegram' AND visitor_hash IS NOT NULL)
                        AS unique_telegram_clickers,
                    COUNT(DISTINCT visitor_hash)
                        FILTER (WHERE destination IN ('affiliate','telegram')
                                AND visitor_hash IS NOT NULL)
                        AS unique_cta_clickers,
                    MAX(created_at) AS last_click_at
                FROM outbound_events
                WHERE {click_where}
                """,
                (ANALYTICS_CODE,),
            )
            clicks = cur.fetchone() or {}

    v = int(visits.get("visits") or 0)
    uv = int(visits.get("unique_visitors") or 0)
    a = int(clicks.get("affiliate_clicks") or 0)
    t = int(clicks.get("telegram_clicks") or 0)
    ua = int(clicks.get("unique_affiliate_clickers") or 0)
    ut = int(clicks.get("unique_telegram_clickers") or 0)
    ucta = int(clicks.get("unique_cta_clickers") or 0)

    def pct(n, d):
        return round((n / d * 100.0), 2) if d else 0.0

    last_visit = visits.get("last_visit_at")
    last_click = clicks.get("last_click_at")
    return {
        "landing_visits": v,
        "unique_visitors": uv,
        "affiliate_clicks": a,
        "telegram_clicks": t,
        "total_cta_clicks": a + t,
        "unique_affiliate_clickers": ua,
        "unique_telegram_clickers": ut,
        "unique_cta_clickers": ucta,
        "affiliate_ctr": pct(ua, uv),
        "telegram_ctr": pct(ut, uv),
        "cta_ctr": pct(ucta, uv),
        "last_visit_at": last_visit.isoformat() if last_visit else None,
        "last_click_at": last_click.isoformat() if last_click else None,
    }


def _batraxy_analytics(bot):
    periods = {
        "24h": _batraxy_period_metrics(bot, "NOW() - INTERVAL '24 hours'"),
        "7d": _batraxy_period_metrics(bot, "NOW() - INTERVAL '7 days'"),
        "30d": _batraxy_period_metrics(bot, "NOW() - INTERVAL '30 days'"),
        "all": _batraxy_period_metrics(bot, None),
    }

    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COALESCE(NULLIF(TRIM(referer),''),'Direct / unknown') AS referer,
                    COUNT(*) AS visits,
                    COUNT(DISTINCT visitor_hash)
                        FILTER (WHERE visitor_hash IS NOT NULL) AS unique_visitors
                FROM landing_events
                WHERE agent_code=%s
                  AND created_at >= NOW() - INTERVAL '30 days'
                GROUP BY 1
                ORDER BY visits DESC
                LIMIT 8
                """,
                (ANALYTICS_CODE,),
            )
            referrers = [
                {
                    "referer": str(row.get("referer") or "Direct / unknown")[:300],
                    "visits": int(row.get("visits") or 0),
                    "unique_visitors": int(row.get("unique_visitors") or 0),
                }
                for row in cur.fetchall()
            ]

    return {
        "ok": True,
        "site": "www.batraxy.com",
        "periods": periods,
        "top_referrers_30d": referrers,
        "tracking": {
            "landing_dedupe_minutes": 30,
            "click_dedupe_seconds": 5,
            "visitor_id": "privacy-safe hash of IP + user agent; raw IP is not stored",
        },
    }


def prepare(production):
    bot = production.bot
    if getattr(bot, "_batraxy_transparent_landing_prepared", False):
        return

    app = bot.tracker_api

    def transparent_landing_root():
        try:
            _record_home_visit(bot)
        except Exception:
            bot.logger.exception("BATRAXY_HOME_VISIT_TRACK_FAILED")
        response = Response(HTML, mimetype="text/html")
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return response

    app.view_functions["landing_root"] = transparent_landing_root

    def batraxy_affiliate_redirect():
        try:
            _record_home_click(bot, "affiliate")
        except Exception:
            bot.logger.exception("BATRAXY_AFFILIATE_CLICK_TRACK_FAILED")
        return redirect(AFFILIATE_DESTINATION, code=302)

    def batraxy_telegram_redirect():
        try:
            _record_home_click(bot, "telegram")
        except Exception:
            bot.logger.exception("BATRAXY_TELEGRAM_CLICK_TRACK_FAILED")
        return redirect(TELEGRAM_DESTINATION, code=302)

    def batraxy_analytics_api():
        expected = os.getenv("BATRAXY_ANALYTICS_KEY", "").strip()
        supplied = request.headers.get("X-Batraxy-Key", "")
        if not expected:
            return jsonify({"ok": False, "error": "analytics_key_not_configured"}), 503
        if not hmac.compare_digest(expected, supplied):
            return jsonify({"ok": False, "error": "unauthorized"}), 401
        try:
            response = jsonify(_batraxy_analytics(bot))
            response.headers["Cache-Control"] = "no-store"
            return response
        except Exception:
            bot.logger.exception("BATRAXY_ANALYTICS_FAILED")
            return jsonify({"ok": False, "error": "analytics_unavailable"}), 500

    if "batraxy_affiliate_redirect" not in app.view_functions:
        app.add_url_rule(
            "/batraxy/go/affiliate",
            endpoint="batraxy_affiliate_redirect",
            view_func=batraxy_affiliate_redirect,
            methods=["GET"],
        )
    if "batraxy_telegram_redirect" not in app.view_functions:
        app.add_url_rule(
            "/batraxy/go/telegram",
            endpoint="batraxy_telegram_redirect",
            view_func=batraxy_telegram_redirect,
            methods=["GET"],
        )
    if "batraxy_analytics_api" not in app.view_functions:
        app.add_url_rule(
            "/api/batraxy/analytics",
            endpoint="batraxy_analytics_api",
            view_func=batraxy_analytics_api,
            methods=["GET"],
        )

    def meta_live_sports_creative():
        return send_file(
            "media/live-sports-hub-meta.jpg",
            mimetype="image/jpeg",
            max_age=3600,
        )

    if "batraxy_meta_live_sports_creative" not in app.view_functions:
        app.add_url_rule(
            "/media/live-sports-hub-meta.jpg",
            endpoint="batraxy_meta_live_sports_creative",
            view_func=meta_live_sports_creative,
            methods=["GET"],
        )

    bot._batraxy_transparent_landing_prepared = True
    bot.logger.warning(
        "BATRAXY_TRANSPARENT_LANDING active=on root_only=on "
        "premium_design=on affiliate_disclosure=on "
        "betroxy_destination_disclosed=on analytics=on "
        "affiliate_clicks=on telegram_clicks=on"
    )
