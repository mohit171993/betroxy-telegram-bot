import html
import json
import threading
import time
from datetime import datetime, timezone

import requests

import bot
import v112_quiz_mobile_ux as v112

v110 = v112.v110
v111 = v112.v111


def _tg_send_text(chat_id, text, rows):
    try:
        payload = {
            "chat_id": str(chat_id),
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": json.dumps({"inline_keyboard": rows}, separators=(",", ":")),
        }
        r = requests.post(f"{v110.TG_API}/sendMessage", data=payload, timeout=25)
        data = r.json() if r.content else {}
        return bool(r.ok and data.get("ok")), data
    except Exception as exc:
        return False, {"description": str(exc)}


def _difficulty_label(question):
    return {1: "🟢 EASY", 2: "🟡 MEDIUM", 3: "🔴 HARD"}.get(int(question.get("difficulty") or 1), "QUIZ")


def _question_text(question, seq):
    return (
        f"🏆 <b>BETROXY DAILY CHALLENGE</b>\n\n"
        f"<b>Question {seq}/7</b>  •  {html.escape(str(question['sport']))}\n"
        f"{_difficulty_label(question)}\n\n"
        f"❓ <b>{html.escape(str(question['question']))}</b>\n\n"
        f"⏱ <b>{v110.QUESTION_SECONDS} seconds</b>\n"
        f"Tap one answer below."
    )


async def _send_question_text(uid, campaign, entry, question):
    seq = int(question["seq"])
    ok, data = _tg_send_text(uid, _question_text(question, seq), v110._question_buttons(uid, question))
    if ok:
        v110._set_session(
            uid,
            campaign_id=campaign["id"],
            entry_id=entry["id"],
            current_question_id=question["id"],
            question_sent_at=datetime.now(timezone.utc),
            flow_state="in_quiz",
        )
        bot.logger.warning("V113_TEXT_QUESTION_SENT uid=%s campaign=%s entry=%s seq=%s", uid, campaign["id"], entry["id"], seq)
    else:
        bot.logger.error("V113_TEXT_QUESTION_FAILED uid=%s seq=%s detail=%s", uid, seq, data)
    return ok


# Replace the image question sender everywhere in the V110 quiz flow.
v110._send_question_to_user = _send_question_text


def _send_v113_test():
    time.sleep(18)
    try:
        campaign = v110._today_campaign(test_mode=True)
        lead = v110._find_test_user()
        if not lead or not lead.get("reachable_bot"):
            bot.logger.warning("V113_TEST target_unavailable")
            return
        uid = int(lead["telegram_user_id"])
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM v110_quiz_questions WHERE campaign_id=%s ORDER BY seq LIMIT 1",
                    (int(campaign["id"]),),
                )
                q = cur.fetchone()
        if not q:
            bot.logger.warning("V113_TEST no_question")
            return
        ok, data = _tg_send_text(uid, _question_text(q, 1), v110._question_buttons(uid, q))
        mid = ((data.get("result") or {}).get("message_id")) if isinstance(data, dict) else None
        bot.logger.warning("V113_TEXT_UX_TEST sent=%s uid=%s message_id=%s buttons=4", ok, uid, mid)
    except Exception:
        bot.logger.exception("V113_TEXT_UX_TEST_FAILED")


bot.logger.warning("V113_TEXT_QUIZ_UX active=on question_images=off telegram_text=on answer_buttons=4")


if __name__ == "__main__":
    v110._ensure_schema()
    v110._selftest()
    v111._compatibility_selftest()
    threading.Thread(target=v110._public_worker, name="betroxy-v110-public-worker", daemon=True).start()
    threading.Thread(target=_send_v113_test, name="betroxy-v113-test", daemon=True).start()
    threading.Thread(target=v110.v83._worker_loop, name="betroxy-engagement-worker", daemon=True).start()
    bot.logger.warning("V113 polling handover delay=12s")
    time.sleep(12)
    bot.main()
