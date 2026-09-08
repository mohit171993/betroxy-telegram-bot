import html

import bot
import v60_checkout_whatsapp_bootstrap as v60
import v56_checkout_whatsapp_bootstrap as checkout

# ============================================================
# V61 - PREMIUM BETROXY WHATSAPP CHECKOUT
# ============================================================
# Keeps all V60/V59 behavior and upgrades only the per-link checkout UI.
# WhatsApp routing, per-link numbers/messages and tracking remain unchanged.
# ============================================================


def premium_checkout_html(row):
    slug = str(row["slug"])
    message = html.escape(str(row.get("whatsapp_message") or checkout.DEFAULT_CHECKOUT_MESSAGE))
    whatsapp_go = f"{bot.PUBLIC_BASE_URL}/go/{html.escape(slug)}/whatsapp"

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#020b08">
<title>BETROXY | Official WhatsApp Access</title>
<style>
*{{box-sizing:border-box}}
html,body{{margin:0;min-height:100%;font-family:Inter,Arial,Helvetica,sans-serif;background:#020b08;color:#fff}}
body{{
  min-height:100vh;
  background:
    radial-gradient(circle at 75% 8%,rgba(22,226,129,.14),transparent 28%),
    radial-gradient(circle at 10% 82%,rgba(17,181,104,.12),transparent 30%),
    linear-gradient(160deg,#010706 0%,#03140d 48%,#010a07 100%);
}}
.page{{width:min(100%,760px);margin:0 auto;min-height:100vh;position:relative;overflow:hidden}}
.topbar{{
  height:72px;display:flex;align-items:center;justify-content:center;padding:0 18px;
  background:rgba(5,16,12,.92);border-bottom:1px solid rgba(255,255,255,.07);
  backdrop-filter:blur(12px);position:sticky;top:0;z-index:5
}}
.domain{{
  display:flex;align-items:center;gap:9px;padding:11px 18px;border-radius:28px;
  background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.08);
  color:#eef5f1;font-size:14px;font-weight:700
}}
.lock{{font-size:14px;color:#73efb5}}
.hero{{padding:34px 20px 18px;text-align:center;position:relative}}
.hero:before{{
  content:"";position:absolute;left:-85px;top:40px;width:240px;height:2px;background:linear-gradient(90deg,transparent,#18e88c,transparent);transform:rotate(28deg);box-shadow:0 0 18px #18e88c
}}
.brandline{{display:flex;align-items:center;justify-content:center;gap:14px}}
.logo{{font-size:clamp(38px,9vw,64px);font-weight:950;letter-spacing:-2px;font-style:italic;text-shadow:0 14px 45px rgba(0,0,0,.4)}}
.logo .o{{color:#26ef93}}
.crown{{color:#ffd928;font-size:22px;transform:translateY(-19px);margin-left:-57px}}
.tag{{margin-top:5px;color:#c7d7d0;font-size:11px;font-weight:800;letter-spacing:4px;text-transform:uppercase}}
.official{{
  display:inline-flex;align-items:center;gap:7px;margin-top:20px;padding:8px 12px;border-radius:999px;
  background:rgba(33,238,145,.08);border:1px solid rgba(33,238,145,.22);color:#88f5c0;font-size:11px;font-weight:800;letter-spacing:.5px
}}
.hero h1{{font-size:clamp(34px,8.4vw,58px);line-height:.98;margin:27px auto 12px;letter-spacing:-1.5px;text-transform:uppercase;max-width:650px}}
.hero h1 span{{display:block;color:#28ec92;font-size:1.16em}}
.hero p{{margin:0 auto;max-width:570px;color:#a8bbb3;line-height:1.55;font-size:14px}}
.features{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:26px 0 0}}
.feature{{padding:13px 5px;border-radius:16px;background:rgba(255,255,255,.035);border:1px solid rgba(255,255,255,.065)}}
.feature .ico{{font-size:24px;margin-bottom:7px}}
.feature b{{display:block;font-size:10px;line-height:1.2}}
.checkout{{padding:12px 20px 24px}}
.cta{{
  min-height:70px;border-radius:24px;display:flex;align-items:center;justify-content:center;gap:13px;
  background:linear-gradient(135deg,#20ee8d,#09bf63);color:#02100a;text-decoration:none;
  font-size:clamp(18px,4.6vw,25px);font-weight:950;box-shadow:0 15px 44px rgba(23,226,129,.24),inset 0 1px 0 rgba(255,255,255,.35);
  border:1px solid rgba(146,255,204,.45);transition:.16s ease
}}
.cta:active{{transform:scale(.987)}}
.wa{{font-size:29px}}
.arrow{{font-size:28px;margin-left:4px}}
.message-card{{
  margin-top:18px;border-radius:18px;padding:15px 16px;display:flex;align-items:center;gap:13px;text-align:left;
  background:linear-gradient(180deg,rgba(16,30,24,.93),rgba(8,20,15,.94));border:1px solid rgba(255,255,255,.085)
}}
.msg-icon{{width:44px;height:44px;border-radius:12px;display:flex;align-items:center;justify-content:center;background:rgba(33,238,145,.09);font-size:23px}}
.msg-copy{{min-width:0;flex:1}}
.msg-label{{font-size:10px;color:#778e84;margin-bottom:4px;text-transform:uppercase;letter-spacing:1px;font-weight:800}}
.msg-text{{font-size:14px;color:#edf5f1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.visual{{margin-top:18px;border-radius:24px;overflow:hidden;position:relative;min-height:220px;border:1px solid rgba(37,236,143,.14);background:linear-gradient(155deg,#071b13,#04110c)}}
.visual:before{{content:"";position:absolute;inset:0;background:radial-gradient(circle at 18% 70%,rgba(32,235,139,.24),transparent 28%),radial-gradient(circle at 78% 40%,rgba(32,235,139,.11),transparent 30%)}}
.phone{{
  width:190px;height:305px;border-radius:27px;background:#050b0f;border:7px solid #10181d;position:absolute;left:50%;top:40px;transform:translateX(-50%) rotate(-4deg);box-shadow:0 28px 50px rgba(0,0,0,.45);padding:18px 11px
}}
.phone-logo{{font-size:21px;font-weight:950;font-style:italic;margin-bottom:18px}}.phone-logo span{{color:#28ed92}}
.phone-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:7px}}
.phone-grid div{{height:55px;border-radius:10px;background:#0d171b;border:1px solid rgba(255,255,255,.05);display:flex;align-items:center;justify-content:center;text-align:center;font-size:8px;color:#cdd8d3;padding:4px}}
.ball{{position:absolute;left:18px;bottom:22px;font-size:62px;filter:drop-shadow(0 14px 18px rgba(0,0,0,.35))}}
.cards{{position:absolute;right:14px;bottom:24px;font-size:58px;transform:rotate(8deg);filter:drop-shadow(0 14px 18px rgba(0,0,0,.4))}}
.footer{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;padding:18px 15px 24px;border-top:1px solid rgba(255,255,255,.065);background:rgba(2,11,8,.82)}}
.foot{{text-align:center;color:#b5c5be;font-size:9px;line-height:1.35}}
.foot .fi{{font-size:20px;display:block;margin-bottom:5px}}
@media(max-width:520px){{
  .topbar{{height:66px}}.domain{{font-size:13px;padding:10px 15px}}.hero{{padding:28px 14px 15px}}
  .features{{gap:6px}}.feature{{padding:11px 3px}}.feature .ico{{font-size:21px}}.feature b{{font-size:9px}}
  .checkout{{padding:10px 14px 21px}}.cta{{min-height:65px;border-radius:21px}}.visual{{min-height:205px}}
  .phone{{width:170px;height:280px;top:34px}}.ball,.cards{{font-size:53px}}
}}
</style>
</head>
<body>
<div class="page">
  <div class="topbar"><div class="domain"><span class="lock">▣</span> batraxy.com/{html.escape(slug)}</div></div>

  <section class="hero">
    <div class="brandline"><div class="logo">BETR<span class="o">O</span>XY</div><div class="crown">♛</div></div>
    <div class="tag">Play &nbsp;|&nbsp; Win &nbsp;|&nbsp; More</div>
    <div class="official">🛡 OFFICIAL BETROXY WHATSAPP ACCESS</div>
    <h1>Get in touch on <span>WhatsApp</span> with Betroxy</h1>
    <p>Chat with the official BETROXY team for quick assistance, account help, updates and offers.</p>

    <div class="features">
      <div class="feature"><div class="ico">⚡</div><b>FAST<br>RESPONSE</b></div>
      <div class="feature"><div class="ico">🎧</div><b>DEDICATED<br>SUPPORT</b></div>
      <div class="feature"><div class="ico">🎁</div><b>EXCLUSIVE<br>UPDATES</b></div>
      <div class="feature"><div class="ico">🛡️</div><b>SAFE &<br>SECURE</b></div>
    </div>
  </section>

  <section class="checkout">
    <a class="cta" href="{whatsapp_go}"><span class="wa">◉</span> Continue on WhatsApp <span class="arrow">›</span></a>

    <div class="message-card">
      <div class="msg-icon">💬</div>
      <div class="msg-copy">
        <div class="msg-label">Pre-filled message</div>
        <div class="msg-text">{message}</div>
      </div>
    </div>

    <div class="visual">
      <div class="ball">🏏</div>
      <div class="phone">
        <div class="phone-logo">BETR<span>O</span>XY</div>
        <div class="phone-grid">
          <div>⚽<br>Sports</div><div>🎰<br>Casino</div><div>🎥<br>Live</div>
          <div>✈️<br>Aviator</div><div>777<br>Slots</div><div>🎲<br>Games</div>
        </div>
      </div>
      <div class="cards">♠️</div>
    </div>
  </section>

  <footer class="footer">
    <div class="foot"><span class="fi">🔞</span>18+<br>Play responsibly</div>
    <div class="foot"><span class="fi">🛡️</span>Your data<br>is secure</div>
    <div class="foot"><span class="fi">👥</span>Official<br>BETROXY support</div>
  </footer>
</div>
</body>
</html>"""


# The V56 route calls checkout._checkout_html dynamically, so replacing it here
# upgrades every campaign URL that has checkout enabled without changing routing.
checkout._checkout_html = premium_checkout_html
bot.logger.warning("V61_PREMIUM_CHECKOUT active=on")

if __name__ == "__main__":
    bot.main()
