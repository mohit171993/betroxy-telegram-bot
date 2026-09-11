import io
import threading
import time
from datetime import datetime, timezone

from PIL import Image, ImageDraw

import bot
import v111_quiz_test_stable as v111

v110 = v111.v110


def _mobile_question_card(campaign, question, seq):
    # Compact 4:3 mobile card: readable at Telegram chat width.
    img = Image.new("RGB", (1080, 810), (10, 8, 28))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((36, 36, 1044, 774), radius=44, outline=(112, 83, 255), width=5)
    d.rounded_rectangle((62, 62, 1018, 158), radius=28, fill=(25, 19, 61))
    d.text((92, 87), "BETROXY", font=v110._font(42, True), fill=(255,255,255))
    d.text((770, 96), "DAILY CHALLENGE", font=v110._font(22, True), fill=(140,232,255))

    difficulty = int(question.get("difficulty") or 1)
    diff = {1:"EASY",2:"MEDIUM",3:"HARD"}.get(difficulty,"QUIZ")
    d.text((72, 205), f"QUESTION {seq}/7", font=v110._font(38, True), fill=(143,232,255))
    d.rounded_rectangle((820, 194, 1008, 250), radius=18, fill=(37,31,82))
    d.text((865, 209), diff, font=v110._font(22, True), fill=(255,216,92))

    # Large question typography with adaptive sizing.
    text = str(question["question"])
    font_size = 54 if len(text) <= 75 else 46
    qfont = v110._font(font_size, True)
    lines = v110._wrap(d, text, qfont, 900)
    y = 305
    for line in lines[:4]:
        d.text((72, y), line, font=qfont, fill=(255,255,255))
        y += font_size + 18

    d.rounded_rectangle((72, 625, 1008, 720), radius=28, fill=(26,20,64))
    d.text((105, 650), f"⏱  {v110.QUESTION_SECONDS} SECONDS", font=v110._font(31, True), fill=(255,216,92))
    d.text((505, 654), "TAP AN ANSWER BELOW", font=v110._font(25, True), fill=(210,214,240))

    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    out.seek(0)
    return out


# All live V110 question sends now use the compact readable card.
v110._render_question = _mobile_question_card


def _send_v112_test():
    time.sleep(18)
    try:
        campaign = v110._today_campaign(test_mode=True)
        lead = v110._find_test_user()
        if not lead or not lead.get("reachable_bot"):
            bot.logger.warning("V112_TEST target_unavailable")
            return
        uid = int(lead["telegram_user_id"])
        # Send a fresh question UX preview with real answer buttons; do not alter quiz state.
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM v110_quiz_questions WHERE campaign_id=%s ORDER BY seq LIMIT 1", (int(campaign["id"]),))
                q = cur.fetchone()
        if not q:
            bot.logger.warning("V112_TEST no_question")
            return
        caption = f"🏆 <b>Question 1/7</b> • {q['sport']}\n⏱ <b>{v110.QUESTION_SECONDS} seconds</b> — tap one answer below."
        ok, data = v110._tg_send_photo(uid, _mobile_question_card(campaign, q, 1), caption, v110._question_buttons(uid, q))
        mid = ((data.get("result") or {}).get("message_id")) if isinstance(data, dict) else None
        bot.logger.warning("V112_MOBILE_UX_TEST sent=%s uid=%s message_id=%s buttons=4", ok, uid, mid)
    except Exception:
        bot.logger.exception("V112_MOBILE_UX_TEST_FAILED")


bot.logger.warning("V112_MOBILE_QUIZ_UX active=on compact_4x3=on large_question=on answer_buttons=4")

if __name__ == "__main__":
    v110._ensure_schema()
    v110._selftest()
    v111._compatibility_selftest()
    threading.Thread(target=v110._public_worker, name="betroxy-v110-public-worker", daemon=True).start()
    threading.Thread(target=_send_v112_test, name="betroxy-v112-test", daemon=True).start()
    threading.Thread(target=v110.v83._worker_loop, name="betroxy-engagement-worker", daemon=True).start()
    bot.logger.warning("V112 polling handover delay=12s")
    time.sleep(12)
    bot.main()
