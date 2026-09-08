import io

from PIL import Image, ImageDraw, ImageFont

import bot
import v62_ai_admin_assistant_bootstrap as v62

# V63: customer-facing /start banner only. No admin UI, file names, code,
# setup instructions, campaign controls, or technical screenshots.


def _font(size, bold=False):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _public_banner_bytes():
    w, h = 1200, 675
    img = Image.new("RGB", (w, h), (3, 10, 8))
    d = ImageDraw.Draw(img)

    # Premium dark green background / glow.
    for r in range(430, 20, -14):
        strength = max(0, int(45 * (1 - r / 430)))
        d.ellipse((730-r, 335-r, 730+r, 335+r), fill=(3, 20 + strength // 2, 13 + strength // 3))
    d.rectangle((0, 0, w, 8), fill=(76, 255, 126))
    d.rectangle((0, h-8, w, h), fill=(27, 129, 65))

    # Crown mark.
    cx, cy = 115, 95
    gold = (244, 194, 52)
    d.polygon([(cx,cy+55),(cx+5,cy),(cx+42,cy+36),(cx+72,cy-8),(cx+102,cy+36),(cx+139,cy),(cx+144,cy+55)], fill=gold)
    d.rectangle((cx, cy+55, cx+144, cy+76), fill=gold)

    d.text((115, 205), "BETROXY", font=_font(92, True), fill=(255,255,255))
    d.text((120, 310), "PLAY  •  WIN  •  ENJOY", font=_font(30, True), fill=(112,255,157))
    d.text((120, 395), "OFFICIAL ACCESS & SUPPORT", font=_font(40, True), fill=(255,255,255))
    d.text((120, 458), "Everything you need, in one place.", font=_font(29), fill=(190,207,199))

    # Simple decorative sports/game motif, not an interface screenshot.
    d.ellipse((860, 160, 1080, 380), outline=(76,255,126), width=8)
    d.ellipse((900, 200, 1040, 340), fill=(12,40,27), outline=(244,194,52), width=6)
    d.text((935, 220), "B", font=_font(84, True), fill=gold)
    d.text((906, 330), "BETROXY", font=_font(24, True), fill=(255,255,255))
    d.line((810, 500, 1100, 500), fill=(46,162,88), width=3)
    d.text((835, 525), "18+  •  PLAY RESPONSIBLY", font=_font(22, True), fill=(166,188,176))

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=91, optimize=True)
    out.seek(0)
    return out


async def v63_start(update, context):
    # Preserve all referral/deep-link behavior from the previous stack.
    if getattr(context, "args", None):
        return await v62.v61.v60.v59.v59_start(update, context)

    msg = update.effective_message
    if not msg:
        return

    try:
        await msg.reply_photo(
            photo=_public_banner_bytes(),
            caption="✨ <b>BETROXY</b> • Official Access & Support",
            parse_mode=bot.ParseMode.HTML,
        )
    except Exception as exc:
        bot.logger.exception("V63 public banner failed: %s", exc)

    # Keep the current V59/V53 customer welcome and menu.
    await msg.reply_text(
        v62.v61.v60.v59.v53.v53_public_welcome_text(),
        parse_mode=bot.ParseMode.HTML,
        reply_markup=v62.v61.v60.v59.v53.v53_public_menu(update.effective_user.id if update.effective_user else None),
        disable_web_page_preview=True,
    )


bot.start = v63_start
bot.logger.warning("V63_CLEAN_PUBLIC_BANNER active=on no_admin_ui=on")

if __name__ == "__main__":
    bot.main()
