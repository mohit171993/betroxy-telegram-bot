import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import bot
import v62_ai_admin_assistant_bootstrap as v62
import v53_attractive_customer_experience_bootstrap as v53
import v57_reliable_banner_bootstrap as v57

# V63 - customer-facing welcome banner only.
# No source file names, deployment instructions, or package contents are shown.

BANNER_PATH = Path("/tmp/betroxy_customer_banner.jpg")


def _font(size, bold=False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            return ImageFont.truetype(p, size=size)
    return ImageFont.load_default()


def _build_banner():
    w, h = 1280, 720
    img = Image.new("RGB", (w, h), (4, 10, 8))
    px = img.load()

    # Dark green radial/vertical glow background.
    for y in range(h):
        for x in range(w):
            gx = max(0.0, 1.0 - abs(x - 760) / 850.0)
            gy = max(0.0, 1.0 - abs(y - 330) / 560.0)
            glow = gx * gy
            r = int(4 + 2 * glow)
            g = int(10 + 33 * glow)
            b = int(8 + 20 * glow)
            px[x, y] = (r, g, b)

    d = ImageDraw.Draw(img)
    green = (35, 235, 125)
    gold = (245, 198, 59)
    white = (245, 247, 246)
    muted = (168, 185, 177)
    panel = (9, 31, 24)
    panel2 = (13, 42, 31)

    # Accent lines / glow strips.
    d.rounded_rectangle((0, 0, w, 8), 4, fill=green)
    d.polygon([(0, 650), (760, 535), (1280, 560), (1280, 720), (0, 720)], fill=(4, 18, 13))
    d.line((0, 650, 760, 535, 1280, 560), fill=green, width=3)

    # Crown mark.
    crown = [(90, 86), (112, 48), (135, 86), (163, 49), (185, 86), (178, 112), (97, 112)]
    d.polygon(crown, fill=gold)
    d.rectangle((96, 113, 179, 124), fill=gold)

    d.text((80, 142), "BETROXY", font=_font(72, True), fill=white)
    d.text((83, 224), "PLAY  |  WIN  |  MORE", font=_font(24), fill=green)
    d.text((80, 294), "OFFICIAL", font=_font(44, True), fill=white)
    d.text((80, 342), "TELEGRAM BOT", font=_font(58, True), fill=green)
    d.text((82, 416), "Access BETROXY services, updates and support", font=_font(25), fill=muted)
    d.text((82, 452), "from one clean, simple place.", font=_font(25), fill=muted)

    # Feature chips.
    chips = [
        ("Creator Links", 80, 514),
        ("Campaign Tools", 278, 514),
        ("Landing Design", 494, 514),
    ]
    for label, x, y in chips:
        tw = d.textbbox((0, 0), label, font=_font(19, True))[2]
        boxw = max(178, tw + 52)
        d.rounded_rectangle((x, y, x + boxw, y + 58), 16, fill=panel2, outline=green, width=2)
        d.ellipse((x + 16, y + 19, x + 34, y + 37), fill=green)
        d.text((x + 44, y + 16), label, font=_font(19, True), fill=white)

    # Phone-style UI card on the right.
    phone = (760, 58, 1195, 650)
    d.rounded_rectangle(phone, 46, fill=(7, 15, 18), outline=(87, 104, 107), width=4)
    d.rounded_rectangle((786, 93, 1168, 625), 28, fill=(11, 25, 27))
    d.ellipse((816, 116, 868, 168), fill=(4, 28, 21), outline=green, width=2)
    d.text((830, 127), "B", font=_font(24, True), fill=gold)
    d.text((886, 118), "Betroxy", font=_font(28, True), fill=white)
    d.text((887, 151), "bot", font=_font(18), fill=muted)

    button_labels = [
        ("Creator Links", 814, 205), ("Add Creator", 993, 205),
        ("Pixel Manager", 814, 278), ("Landing Design", 993, 278),
        ("Campaign Tools", 814, 351), ("Refresh", 993, 351),
    ]
    for label, x, y in button_labels:
        bw = 160 if x < 900 else 150
        d.rounded_rectangle((x, y, x + bw, y + 58), 13, fill=panel2, outline=(27, 116, 76), width=1)
        d.text((x + 14, y + 17), label, font=_font(16, True), fill=white)

    d.rounded_rectangle((814, 425, 1143, 482), 13, fill=panel2, outline=(27, 116, 76), width=1)
    d.text((902, 441), "Admin Panel", font=_font(18, True), fill=white)

    d.rounded_rectangle((814, 506, 1143, 595), 17, fill=(240, 243, 242))
    d.text((834, 524), "Welcome to BETROXY!", font=_font(20, True), fill=(18, 28, 25))
    d.text((834, 557), "What would you like to do?", font=_font(16), fill=(58, 70, 66))

    # Bottom clean footer.
    d.text((80, 670), "18+  •  PLAY RESPONSIBLY", font=_font(18, True), fill=muted)
    d.text((940, 670), "BETROXY", font=_font(20, True), fill=green)

    img.save(BANNER_PATH, "JPEG", quality=90, optimize=True)


try:
    _build_banner()
    bot.logger.warning("V63 customer banner generated path=%s", BANNER_PATH)
except Exception as exc:
    bot.logger.exception("V63 banner generation failed: %s", exc)


async def v63_start(update, context):
    # Preserve referral/deep-link behavior exactly as before.
    if getattr(context, "args", None):
        return await v57.v54._original_start(update, context)

    msg = update.effective_message
    if not msg:
        return

    try:
        if BANNER_PATH.exists():
            with BANNER_PATH.open("rb") as photo:
                await msg.reply_photo(
                    photo=photo,
                    caption="✨ <b>BETROXY</b> • Official Access & Support",
                    parse_mode=bot.ParseMode.HTML,
                )
        else:
            # Safe fallback if local image generation ever fails.
            await msg.reply_photo(
                photo=v57.BANNER_URL,
                caption="✨ <b>BETROXY</b> • Official Access & Support",
                parse_mode=bot.ParseMode.HTML,
            )
    except Exception as exc:
        bot.logger.exception("V63 customer banner send failed: %s", exc)

    await msg.reply_text(
        v53.v53_public_welcome_text(),
        parse_mode=bot.ParseMode.HTML,
        reply_markup=v53.v53_public_menu(update.effective_user.id if update.effective_user else None),
        disable_web_page_preview=True,
    )


bot.start = v63_start
bot.logger.warning("V63_CLEAN_CUSTOMER_BANNER active=on no_dev_file_names=on")

if __name__ == "__main__":
    bot.main()
