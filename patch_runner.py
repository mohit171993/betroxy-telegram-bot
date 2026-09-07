import io
import re
from datetime import datetime, timezone

import bot
import v27_runner  # activates smart-checker Apify fallback + server completion patches

print("PATCH_RUNNER_PRODUCTION_SMART_CHECK_ACTIVE", flush=True)
bot.logger.warning("PATCH_RUNNER_PRODUCTION_SMART_CHECK_ACTIVE")


# ============================================================
# PRODUCTION SMART-CHECK RULES
# ============================================================
# - No hard-coded creator count.
# - Every active Instagram campaign link is automatically eligible, including
#   links generated in the future.
# - If all four compliance fields are already positive for that creator's
#   current campaign day, the creator is skipped for the rest of that day.
# - A new campaign day is evaluated independently.
# ============================================================

POSITIVE = {"verified", "pass", "passed", "ok", "present", "found", "yes"}
NEGATIVE = {"missing", "issue", "fix", "fail", "failed", "no"}


def _norm_status(value):
    return str(value or "pending").strip().lower()


def _all_four_positive(v):
    return all(
        _norm_status(v.get(k)) in POSITIVE
        for k in (
            "bio_status",
            "only_our_link_status",
            "story_status",
            "story_link_status",
        )
    )


def _has_definite_negative(v):
    return any(
        _norm_status(v.get(k)) in NEGATIVE
        for k in (
            "bio_status",
            "only_our_link_status",
            "story_status",
            "story_link_status",
        )
    )


def _active_instagram_links():
    with bot.get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM campaign_links
                WHERE is_active=TRUE
                  AND LOWER(COALESCE(source_type,'instagram'))='instagram'
                ORDER BY id
                """
            )
            return cur.fetchall()


# Preserve same-day PASS rows when a new Smart Check is requested.
# The underlying bot function currently resets the day's rows to pending; we
# snapshot fully-positive rows before that reset and restore them immediately.
_original_request_local_verifier_run = bot.request_local_verifier_run


def production_request_local_verifier_run():
    preserved = []
    for cl in _active_instagram_links():
        day = bot.current_campaign_day(cl)
        v = bot.get_verification_row(cl["id"], day) or {}
        if _all_four_positive(v):
            preserved.append((cl["id"], day, dict(v)))

    result = _original_request_local_verifier_run()

    if preserved:
        with bot.get_db() as conn:
            with conn.cursor() as cur:
                for campaign_link_id, day, v in preserved:
                    cur.execute(
                        """
                        UPDATE campaign_verification
                        SET bio_status=%s,
                            only_our_link_status=%s,
                            story_status=%s,
                            story_link_status=%s,
                            auto_checked_at=%s,
                            auto_check_status=%s,
                            auto_check_detail=%s,
                            detected_bio_links=%s,
                            detected_story_count=%s,
                            checker_mode=%s,
                            checked_at=%s
                        WHERE campaign_link_id=%s AND campaign_day=%s
                        """,
                        (
                            v.get("bio_status"),
                            v.get("only_our_link_status"),
                            v.get("story_status"),
                            v.get("story_link_status"),
                            v.get("auto_checked_at"),
                            v.get("auto_check_status"),
                            v.get("auto_check_detail"),
                            v.get("detected_bio_links"),
                            v.get("detected_story_count") or 0,
                            v.get("checker_mode"),
                            v.get("checked_at"),
                            campaign_link_id,
                            day,
                        ),
                    )
            conn.commit()

    bot.logger.warning(
        "SMART_CHECK_ALL_LINKS request: active=%s preserved_same_day_pass=%s",
        len(_active_instagram_links()),
        len(preserved),
    )
    return result


bot.request_local_verifier_run = production_request_local_verifier_run


# Filter the checker target feed dynamically. This is the key that makes the
# checker apply to ALL current/future active links without any fixed number,
# while skipping rows that have already fully passed today.
_original_verifier_targets = bot.tracker_api.view_functions.get("verifier_targets")
if _original_verifier_targets and not getattr(_original_verifier_targets, "_production_skip_pass_wrapped", False):
    def production_verifier_targets(*args, **kwargs):
        response = _original_verifier_targets(*args, **kwargs)
        try:
            data = response.get_json() or {}
            targets = data.get("targets") or []

            links = {int(x["id"]): x for x in _active_instagram_links()}
            eligible = []
            skipped = []
            for t in targets:
                cid = int(t.get("campaign_link_id") or 0)
                cl = links.get(cid)
                if not cl:
                    continue
                day = bot.current_campaign_day(cl)
                v = bot.get_verification_row(cid, day) or {}
                if _all_four_positive(v):
                    skipped.append(t)
                else:
                    eligible.append(t)

            data["targets"] = eligible
            data["smart_mode"] = True
            data["active_instagram_links"] = len(targets)
            data["already_passed_today"] = len(skipped)
            data["checking_now"] = len(eligible)
            bot.logger.warning(
                "SMART_CHECK_ALL_LINKS targets total=%s skipped_pass_today=%s checking=%s",
                len(targets), len(skipped), len(eligible),
            )
            return bot.jsonify(data)
        except Exception:
            bot.logger.exception("Production Smart Check target filtering failed")
            return response

    production_verifier_targets._production_skip_pass_wrapped = True
    bot.tracker_api.view_functions["verifier_targets"] = production_verifier_targets


# ============================================================
# PROMOTER FIX REPORT
# ============================================================

def _status_label(value):
    s = _norm_status(value)
    if s in POSITIVE:
        return "OK"
    if s in NEGATIVE:
        return "FIX REQUIRED"
    return "REVIEW"


def _fix_action(field, value, assigned_url):
    if _norm_status(value) in POSITIVE:
        return "No action required."
    if field == "bio_status":
        return f"Add the assigned BETROXY campaign link to the Instagram bio: {assigned_url}"
    if field == "only_our_link_status":
        return "Remove other promotional/external bio links and keep only the assigned BETROXY campaign link."
    if field == "story_status":
        return "Publish/restore the required Instagram Story and keep it live for the campaign requirement."
    if field == "story_link_status":
        return f"Add the correct clickable Story link/sticker using: {assigned_url}"
    return "Please correct this item and request a re-check."


def _current_issue_rows():
    out = []
    for cl in _active_instagram_links():
        day = bot.current_campaign_day(cl)
        v = bot.get_verification_row(cl["id"], day) or {}
        if _has_definite_negative(v):
            out.append((cl, day, v))
    return out


def build_promoter_fix_pdf():
    rows = _current_issue_rows()
    buf = io.BytesIO()
    buf.name = "BETROXY_Promoter_Fix_Report.pdf"

    doc = bot.SimpleDocTemplate(
        buf,
        pagesize=bot.landscape(bot.A4),
        rightMargin=24,
        leftMargin=24,
        topMargin=24,
        bottomMargin=24,
    )
    styles = bot.getSampleStyleSheet()
    title = bot.ParagraphStyle(
        "PromoterTitle",
        parent=styles["Title"],
        fontSize=16,
        leading=20,
        alignment=bot.TA_CENTER,
        spaceAfter=10,
    )
    small = bot.ParagraphStyle(
        "PromoterSmall",
        parent=styles["BodyText"],
        fontSize=8,
        leading=10,
    )
    tiny = bot.ParagraphStyle(
        "PromoterTiny",
        parent=styles["BodyText"],
        fontSize=7,
        leading=9,
    )

    story = [
        bot.Paragraph("BETROXY PROMOTION COMPLIANCE – ACTION REQUIRED", title),
        bot.Paragraph(
            "This report lists only creators with a confirmed compliance issue. "
            "Please correct the items marked FIX REQUIRED and inform us once completed so the Smart Check can be run again.",
            small,
        ),
        bot.Spacer(1, 10),
    ]

    if not rows:
        story.append(bot.Paragraph("No confirmed promoter fixes are required at this time.", small))
    else:
        for index, (cl, day, v) in enumerate(rows, 1):
            username = str(cl.get("instagram_username") or "").strip().lstrip("@")
            assigned = f"{bot.PUBLIC_BASE_URL}/{cl['slug']}"
            story.append(bot.Paragraph(f"{index}. @{bot.html.escape(username)} — Campaign Day {day}", styles["Heading3"]))
            story.append(bot.Paragraph(f"Assigned campaign link: {bot.html.escape(assigned)}", tiny))
            story.append(bot.Spacer(1, 4))

            data = [["Check", "Result", "Action required"]]
            fields = [
                ("Bio link", "bio_status"),
                ("Only our bio link", "only_our_link_status"),
                ("Story live", "story_status"),
                ("Story link", "story_link_status"),
            ]
            for label, key in fields:
                value = v.get(key)
                data.append([
                    bot.Paragraph(label, tiny),
                    bot.Paragraph(_status_label(value), tiny),
                    bot.Paragraph(bot.html.escape(_fix_action(key, value, assigned)), tiny),
                ])

            table = bot.Table(data, colWidths=[90, 82, 560], repeatRows=1)
            table.setStyle(bot.TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), bot.colors.HexColor("#1f3d32")),
                ("TEXTCOLOR", (0, 0), (-1, 0), bot.colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.4, bot.colors.grey),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))
            story.append(table)
            story.append(bot.Spacer(1, 12))

    generated = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")
    story.append(bot.Paragraph(f"Generated: {generated}", tiny))
    doc.build(story)
    buf.seek(0)
    return buf, len(rows)


# Add a direct shareable promoter report button to the campaign menu.
_original_campaign_menu = bot.campaign_menu


def production_campaign_menu():
    markup = _original_campaign_menu()
    rows = [list(r) for r in markup.inline_keyboard]
    rows.insert(
        max(0, len(rows) - 1),
        [bot.InlineKeyboardButton("📣 Promoter Fix PDF", callback_data="promoter_fix_report")],
    )
    return bot.InlineKeyboardMarkup(rows)


bot.campaign_menu = production_campaign_menu


# Handle the new report button while leaving every existing callback untouched.
_original_callback_handler = bot.callback_handler


async def production_callback_handler(update, context):
    q = update.callback_query
    if q and q.data == "promoter_fix_report":
        await q.answer()
        if not update.effective_user or update.effective_user.id != bot.ADMIN_ID:
            await q.message.reply_text("Admin only.")
            return
        pdf_file, count = build_promoter_fix_pdf()
        caption = (
            f"📣 <b>Promoter Fix Report</b>\n\n"
            f"Creators requiring confirmed fixes: <b>{count}</b>\n"
            "Share this PDF with the promoter. After the fixes are completed, run Smart Check again."
        )
        await q.message.reply_document(
            document=pdf_file,
            filename="BETROXY_Promoter_Fix_Report.pdf",
            caption=caption,
            parse_mode=bot.ParseMode.HTML,
        )
        return
    return await _original_callback_handler(update, context)


bot.callback_handler = production_callback_handler


# ============================================================
# EXISTING BULK CREATOR MANAGEMENT FIXES
# ============================================================

def _split_items(raw):
    """Return unique campaign inputs from multiline/whitespace-pasted Telegram text."""
    raw = (raw or "").strip()
    if not raw:
        return []

    urls = re.findall(r"https?://[^\s]+", raw, flags=re.I)
    parts = urls if urls else [x.strip() for x in raw.splitlines() if x.strip()]

    out = []
    seen = set()
    for item in parts:
        item = item.strip().rstrip(",;.)]")
        key = item.lower()
        if item and key not in seen:
            seen.add(key)
            out.append(item)
    return out[:1000]


async def _bulk_create(update):
    if not await bot.require_admin(update):
        return bot.ConversationHandler.END

    items = _split_items(update.message.text or "")
    bot.logger.warning("BULK_CREATE_PRODUCTION items=%s", len(items))
    created_rows, existing_rows, failed = [], [], []

    for item in items:
        try:
            row, created = bot.create_campaign_creator(item)
            (created_rows if created else existing_rows).append(row)
        except Exception as exc:
            failed.append((item, str(exc)))

    lines = [
        "✅ <b>Bulk Generation Complete</b>",
        "",
        f"Processed: <b>{len(items)}</b>",
        f"Created: <b>{len(created_rows)}</b>",
        f"Already existed: <b>{len(existing_rows)}</b>",
        f"Failed: <b>{len(failed)}</b>",
    ]

    for row in created_rows[:25]:
        landing, _ = bot.creator_urls(row)
        lines.append(f"\n✅ @{bot.html.escape(str(row['instagram_username']))}\n<code>{bot.html.escape(landing)}</code>")
    for row in existing_rows[:10]:
        lines.append(f"\nℹ️ Already existed: @{bot.html.escape(str(row['instagram_username']))}")
    for item, error in failed[:10]:
        lines.append(f"\n❌ <code>{bot.html.escape(item[:180])}</code>\n{bot.html.escape(error[:220])}")

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=bot.ParseMode.HTML,
        reply_markup=bot.campaign_menu(),
        disable_web_page_preview=True,
    )
    return bot.ConversationHandler.END


_original_single_save = bot.campaign_add_single_save


async def patched_campaign_add_single_save(update, context):
    items = _split_items(update.message.text or "")
    if len(items) > 1:
        return await _bulk_create(update)
    return await _original_single_save(update, context)


async def patched_campaign_add_bulk_save(update, context):
    return await _bulk_create(update)


async def patched_campaign_disable_by_link_save(update, context):
    if not await bot.require_admin(update):
        return bot.ConversationHandler.END

    items = _split_items(update.message.text or "")
    disabled, already_disabled, failed = [], [], []

    for item in items:
        try:
            row, _parsed = bot.find_campaign_link_from_input(item)
            if not row:
                raise ValueError("No matching creator link found")
            if not row.get("is_active"):
                already_disabled.append(row)
                continue
            changed = bot.set_campaign_link_active(row["agent_code"], False)
            if not changed:
                raise ValueError("Could not disable creator link")
            disabled.append(changed)
        except Exception as exc:
            failed.append((item, str(exc)))

    lines = [
        "⛔ <b>Bulk Disable Complete</b>",
        "",
        f"Processed: <b>{len(items)}</b>",
        f"Disabled now: <b>{len(disabled)}</b>",
        f"Already disabled: <b>{len(already_disabled)}</b>",
        f"Failed: <b>{len(failed)}</b>",
    ]
    for row in disabled[:30]:
        lines.append(f"\n✅ @{bot.html.escape(str(row['instagram_username']))}")
    for row in already_disabled[:10]:
        lines.append(f"\nℹ️ Already disabled: @{bot.html.escape(str(row['instagram_username']))}")
    for item, error in failed[:10]:
        lines.append(f"\n❌ <code>{bot.html.escape(item[:180])}</code> — {bot.html.escape(error[:220])}")

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=bot.ParseMode.HTML,
        reply_markup=bot.campaign_menu(),
        disable_web_page_preview=True,
    )
    return bot.ConversationHandler.END


bot.campaign_add_single_save = patched_campaign_add_single_save
bot.campaign_add_bulk_save = patched_campaign_add_bulk_save
bot.campaign_disable_by_link_save = patched_campaign_disable_by_link_save

bot.logger.warning(
    "PATCH_BINDINGS production create=%s bulk=%s disable=%s callback=%s",
    bot.campaign_add_single_save.__name__,
    bot.campaign_add_bulk_save.__name__,
    bot.campaign_disable_by_link_save.__name__,
    bot.callback_handler.__name__,
)

if __name__ == "__main__":
    bot.main()
