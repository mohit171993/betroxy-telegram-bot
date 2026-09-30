"""Send the public start banner photo before the existing /start flow.

Registers one extra private-chat /start handler in an early handler group.
It only sends the bundled banner photo (no caption) and never stops update
propagation, so the existing /start, verification and menu handlers still run
exactly as before. A failed photo send is logged and never blocks /start.
"""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

BANNER_PATH = Path(__file__).resolve().parent / "assets" / "public_start_banner.jpg"
HANDLER_GROUP = -100000
SKIP_PAYLOADS = frozenset({"stopreminders"})

_cached_file_id = None


async def send_public_start_banner(update, context) -> None:
    global _cached_file_id
    chat = update.effective_chat
    if not chat or chat.type != "private" or not update.effective_message:
        return
    args = list(getattr(context, "args", None) or [])
    if args and str(args[0]).strip().lower() in SKIP_PAYLOADS:
        return
    try:
        if _cached_file_id:
            await context.bot.send_photo(chat_id=chat.id, photo=_cached_file_id)
        else:
            with BANNER_PATH.open("rb") as image:
                sent = await context.bot.send_photo(chat_id=chat.id, photo=image)
            photos = getattr(sent, "photo", None) or []
            if photos:
                _cached_file_id = photos[-1].file_id
        logger.info("PUBLIC_START_BANNER_SENT chat_id=%s", chat.id)
    except Exception as exc:
        logger.warning(
            "PUBLIC_START_BANNER_FAILED chat_id=%s error=%s", chat.id, str(exc)[:160]
        )


def install(application) -> None:
    """Idempotently register the banner handler on this application."""
    try:
        from telegram.ext import CommandHandler, filters

        existing = getattr(application, "handlers", {}) or {}
        if any(getattr(h, "callback", None) is send_public_start_banner
               for h in existing.get(HANDLER_GROUP, [])):
            return
        application.add_handler(
            CommandHandler(
                "start",
                send_public_start_banner,
                filters=filters.ChatType.PRIVATE & filters.UpdateType.MESSAGE,
            ),
            group=HANDLER_GROUP,
        )
        logger.info(
            "PUBLIC_START_BANNER_READY group=%s file=%s exists=%s",
            HANDLER_GROUP, BANNER_PATH.name, BANNER_PATH.is_file(),
        )
    except Exception as exc:
        logger.warning("PUBLIC_START_BANNER_INSTALL_FAILED error=%s", str(exc)[:160])
