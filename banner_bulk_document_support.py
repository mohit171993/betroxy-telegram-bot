"""Allow BETROXY bulk banner sessions to accept PNG/JPG documents as well as Photos.

Telegram desktop often sends selected PNG creatives as Documents. The existing
bulk uploader intentionally stores Photo file_ids because the production channel
sender uses sendPhoto. This compatibility layer downloads an admin image document,
re-uploads it silently to the admin as a Telegram Photo, captures the resulting
Photo file_id, immediately deletes that normalization message, and then feeds the
Photo file_id into the existing pending/approval/rotation flow.
"""
from __future__ import annotations

from io import BytesIO

from telegram.ext import ApplicationHandlerStop

import bot
import banner_manager as bm
import banner_bulk_upload as bulk

_installed = False
_previous_post_init = None

_ALLOWED_EXT = (".png", ".jpg", ".jpeg", ".webp")


def _is_image_document(document) -> bool:
    if not document:
        return False
    mime = str(getattr(document, "mime_type", "") or "").lower()
    name = str(getattr(document, "file_name", "") or "").lower()
    return mime.startswith("image/") or name.endswith(_ALLOWED_EXT)


def _patch_prompt():
    previous = bulk._bulk_prompt
    if getattr(previous, "_document_support_patched", False):
        return

    def prompt_with_documents():
        return (
            "📦 <b>PHASE 1 — BULK CHANNEL BANNER UPLOAD</b>\n\n"
            "Send <b>7 or 8 banners</b> now. You can select all of them together.\n"
            "Accepted: <b>Telegram Photos or PNG/JPG image files</b>.\n\n"
            "The bot will segregate them automatically in this order:\n"
            "1–2 → <b>10 AM — Quiz Open</b>\n"
            "3–4 → <b>4 PM — Afternoon</b>\n"
            "5–6 → <b>7 PM — Last Chance</b>\n"
            "7–8 → <b>9:05 PM — Results/Winners</b>\n\n"
            "Channel format: <b>4:5 portrait</b> (about 1122×1402).\n"
            "PNG/JPG files are automatically normalized to Telegram Photo file_ids.\n"
            "Existing locked banners stay as fallbacks.\n"
            "Nothing becomes live until <b>Approve All</b>.\n\n"
            "If you upload 8 images, review opens automatically after image 8.\n"
            "If you upload 7, tap <b>Finish Upload</b> after the seventh image."
        )

    prompt_with_documents._document_support_patched = True
    bulk._bulk_prompt = prompt_with_documents


async def _normalize_document_to_photo(context, admin_id, document):
    tg_file = await context.bot.get_file(document.file_id)
    data = await tg_file.download_as_bytearray()
    stream = BytesIO(bytes(data))
    original_name = str(getattr(document, "file_name", "") or "banner.png")
    # Telegram/Pillow infer the upload from bytes; use a normal photo filename.
    stream.name = original_name
    sent = await context.bot.send_photo(
        chat_id=int(admin_id),
        photo=stream,
        disable_notification=True,
    )
    photos = list(getattr(sent, "photo", None) or [])
    if not photos:
        raise RuntimeError("Telegram did not return a Photo after image normalization")
    photo = photos[-1]
    try:
        await context.bot.delete_message(chat_id=int(admin_id), message_id=int(sent.message_id))
    except Exception:
        bot.logger.exception("BANNER_BULK_NORMALIZE_DELETE_FAILED message_id=%s", getattr(sent, "message_id", None))
    return str(photo.file_id), str(photo.file_unique_id or "")


def _install_document_handler():
    global _previous_post_init
    _previous_post_init = bot.post_init

    async def document_support_post_init(application):
        if _previous_post_init:
            await _previous_post_init(application)

        async def bulk_document_upload(update, context):
            user = getattr(update, "effective_user", None)
            message = getattr(update, "effective_message", None)
            if not user or not message or not bot.is_admin(user.id):
                return

            session = bulk._session(user.id) or {}
            if str(session.get("status") or "") != "active":
                return

            document = getattr(message, "document", None)
            if not _is_image_document(document):
                return

            existing = bulk._items(user.id)
            if len(existing) >= bulk.BULK_MAX:
                await bulk._update_progress(context, user.id, len(existing))
                raise ApplicationHandlerStop

            source_unique_id = str(getattr(document, "file_unique_id", "") or "")
            if bulk._duplicate_in_session(user.id, source_unique_id):
                await context.bot.send_message(
                    chat_id=user.id,
                    text="ℹ️ Duplicate banner ignored in this bulk batch.",
                    disable_notification=True,
                )
                raise ApplicationHandlerStop

            try:
                photo_file_id, normalized_unique_id = await _normalize_document_to_photo(
                    context, user.id, document
                )
            except Exception:
                bot.logger.exception(
                    "BANNER_BULK_DOCUMENT_NORMALIZE_FAILED admin=%s file=%s mime=%s",
                    user.id,
                    getattr(document, "file_name", None),
                    getattr(document, "mime_type", None),
                )
                await context.bot.send_message(
                    chat_id=user.id,
                    text="❌ I could not convert that image file into a Telegram Photo. Please use PNG or JPG.",
                )
                raise ApplicationHandlerStop

            seq_no = len(existing) + 1
            slot = bulk._slot_for_sequence(seq_no)
            # Keep the source document unique id for duplicate protection within this
            # batch, while storing the normalized Photo file_id for production sends.
            banner_id = bm._insert_pending(slot, photo_file_id, normalized_unique_id, user.id)
            try:
                bulk._insert_item(user.id, seq_no, slot, banner_id, source_unique_id or normalized_unique_id)
            except Exception:
                bm._cancel_pending(banner_id)
                raise

            bot.logger.warning(
                "BANNER_BULK_DOCUMENT_CAPTURED admin=%s seq=%s slot=%s banner_id=%s "
                "source=%s normalized_photo=on media_group=%s",
                user.id,
                seq_no,
                slot,
                banner_id,
                getattr(document, "file_name", None),
                getattr(message, "media_group_id", None),
            )
            await bulk._update_progress(context, user.id, seq_no)

            if seq_no >= bulk.BULK_MAX:
                await bulk._finish_review(context, user.id)

            raise ApplicationHandlerStop

        application.add_handler(
            bot.MessageHandler(bot.filters.Document.ALL, bulk_document_upload),
            group=-94,
        )
        bot.logger.warning(
            "BANNER_BULK_DOCUMENT_HANDLERS active=on png_jpg_files=accepted "
            "normalize_to_photo_file_id=on admin_only=on"
        )

    bot.post_init = document_support_post_init


def install():
    global _installed
    if _installed:
        return
    _patch_prompt()
    _install_document_handler()
    _installed = True
    bot.logger.warning(
        "BANNER_BULK_DOCUMENT_SUPPORT active=on photos=accepted documents=png/jpg/webp "
        "normalize=telegram_photo_file_id existing_rotation=unchanged"
    )
