import re
import bot
import v27_runner  # activates smart-checker Apify fallback + live progress patches

print("PATCH_RUNNER_BULK_FIX_V2_ACTIVE", flush=True)
bot.logger.warning("PATCH_RUNNER_BULK_FIX_V2_ACTIVE")
bot.logger.warning("SMART_CHECKER_PATCH_LOADED_FROM_V27_RUNNER")


def _split_items(raw):
    """Return unique campaign inputs from multiline/whitespace-pasted Telegram text."""
    raw = (raw or "").strip()
    if not raw:
        return []

    # Extract URLs anywhere in the message first. This is more reliable than
    # relying on Telegram line breaks because some clients collapse/rewrap text.
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
    return out[:100]


async def _bulk_create(update):
    if not await bot.require_admin(update):
        return bot.ConversationHandler.END

    items = _split_items(update.message.text or "")
    bot.logger.warning("BULK_CREATE_V2 items=%s", len(items))
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
    bot.logger.warning("BULK_DISABLE_V2 items=%s", len(items))
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


# Apply fixes before bot.main() constructs its ConversationHandlers.
bot.campaign_add_single_save = patched_campaign_add_single_save
bot.campaign_add_bulk_save = patched_campaign_add_bulk_save
bot.campaign_disable_by_link_save = patched_campaign_disable_by_link_save

bot.logger.warning(
    "PATCH_BINDINGS create=%s bulk=%s disable=%s",
    bot.campaign_add_single_save.__name__,
    bot.campaign_add_bulk_save.__name__,
    bot.campaign_disable_by_link_save.__name__,
)

if __name__ == "__main__":
    bot.main()
