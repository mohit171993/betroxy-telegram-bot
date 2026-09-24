"""Transparent Batraxy homepage overlay.

Only replaces the public "/" Flask view at startup. Existing creator landing
routes, bot flows, CRM, quizzes, rewards and admin functions remain unchanged.
"""
from flask import Response

SIGNUP_URL = (
    "https://app.affiliar.co/api/r/SNLINK?"
    "to=https%3A%2F%2Fbetroxy.com%2F%3Fmodal%3Dauth%26tab%3Dregister"
)

HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="Batraxy information page for Betroxy registration.">
  <title>Batraxy | Continue to Betroxy</title>
  <style>
    *{box-sizing:border-box}
    body{margin:0;font-family:Arial,Helvetica,sans-serif;background:#0c0f14;color:#f4f6f8}
    .wrap{max-width:980px;margin:0 auto;padding:28px 20px 48px}
    .nav{display:flex;align-items:center;justify-content:space-between;padding:8px 0 26px}
    .brand{font-size:25px;font-weight:800;letter-spacing:.8px}
    .badge{font-size:13px;padding:8px 12px;border:1px solid #39414d;border-radius:999px;color:#cbd1d8}
    .hero{border:1px solid #2b323d;border-radius:24px;padding:48px 38px;background:#141922;box-shadow:0 18px 50px rgba(0,0,0,.28)}
    h1{font-size:46px;line-height:1.08;margin:0 0 18px;max-width:760px}
    .lead{font-size:19px;line-height:1.6;color:#c9cfd6;max-width:760px;margin:0 0 28px}
    .notice{padding:18px 20px;border-radius:14px;background:#1d2430;border:1px solid #39414d;line-height:1.55;color:#e6e9ed;margin:0 0 28px}
    .cta{display:inline-block;text-decoration:none;font-weight:800;font-size:17px;padding:15px 24px;border-radius:12px;background:#fff;color:#111}
    .sub{font-size:13px;color:#9da6b2;margin-top:14px;max-width:680px;line-height:1.5}
    .grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-top:24px}
    .card{border:1px solid #2b323d;border-radius:16px;padding:18px;background:#11161e}
    .card strong{display:block;margin-bottom:7px}
    .card span{font-size:14px;color:#aeb6c0;line-height:1.45}
    footer{margin-top:30px;color:#8f98a4;font-size:13px;line-height:1.6}
    @media(max-width:720px){h1{font-size:36px}.hero{padding:34px 22px}.grid{grid-template-columns:1fr}}
  </style>
</head>
<body>
  <main class="wrap">
    <div class="nav">
      <div class="brand">BATRAXY</div>
      <div class="badge">18+ only</div>
    </div>
    <section class="hero">
      <h1>Continue to Betroxy registration</h1>
      <p class="lead">
        Batraxy is an information and referral page. If you choose to continue,
        you will leave Batraxy and open the Betroxy registration flow.
      </p>
      <div class="notice">
        <strong>Transparent redirect:</strong> the signup button below uses an affiliate tracking link
        and ultimately directs you to <strong>betroxy.com</strong> for account registration.
      </div>
      <a class="cta" href="__SIGNUP_URL__" rel="sponsored nofollow noopener">Continue to Betroxy Signup</a>
      <div class="sub">
        By continuing, you acknowledge that Betroxy is a separate third-party service and that
        registration, eligibility, terms, payments and any gaming activity are governed by Betroxy.
        Availability may vary by location.
      </div>
      <div class="grid">
        <div class="card"><strong>Clear destination</strong><span>The button explicitly continues to Betroxy registration.</span></div>
        <div class="card"><strong>Affiliate disclosure</strong><span>The outbound signup link contains affiliate tracking.</span></div>
        <div class="card"><strong>Responsible use</strong><span>Adults 18+ only. Please follow local laws and play responsibly.</span></div>
      </div>
    </section>
    <footer>
      Batraxy is not presented as an independent cricket-score or live-line product on this page.
      The purpose of this page is to explain the outbound Betroxy registration destination before you continue.
    </footer>
  </main>
</body>
</html>""".replace("__SIGNUP_URL__", SIGNUP_URL)


def prepare(production):
    bot = production.bot
    if getattr(bot, "_batraxy_transparent_landing_prepared", False):
        return

    app = bot.tracker_api

    def transparent_landing_root():
        return Response(HTML, mimetype="text/html")

    # Replace only the existing Flask endpoint bound to "/".
    app.view_functions["landing_root"] = transparent_landing_root
    bot._batraxy_transparent_landing_prepared = True
    bot.logger.warning(
        "BATRAXY_TRANSPARENT_LANDING active=on root_only=on "
        "affiliate_disclosure=on betroxy_destination_disclosed=on"
    )
