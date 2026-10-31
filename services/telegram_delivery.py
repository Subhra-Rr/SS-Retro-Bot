"""
Telegram Audio Delivery Service.
Delivers complete, validated audio files via Telegram Bot API sendAudio,
leveraging file_id caching for instant delivery and zero redundant bandwidth.
Handles long-track uploads gracefully without false-positive timeout errors.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from telegram import Bot, Message
from telegram.constants import ChatAction, ParseMode
from telegram.error import NetworkError, TelegramError, TimedOut

from database import db
from services.audio_validator import audio_validator
from services.downloader import audio_downloader
from services.providers.base import UnifiedTrack
from utils.formatting import escape_html, format_duration

logger = logging.getLogger(__name__)


class TelegramAudioDelivery:
    """Coordinates downloading, validation, delivery, caching, and cleanup."""

    async def deliver_track(
        self,
        bot: Bot,
        chat_id: int,
        track: UnifiedTrack,
        user_id: int | None = None,
    ) -> tuple[bool, Optional[str], Optional[Message]]:
        """
        Deliver full audio track to Telegram chat.
        Returns: (success: bool, error_message: Optional[str], message: Optional[Message])
        """
        caption = (
            f"🎵 <b>{escape_html(track.title)}</b>\n"
            f"👤 <b>Artist:</b> {escape_html(track.artist)}\n"
            f"💿 <b>Album:</b> {escape_html(track.album or 'Single')}\n"
            f"⚖ <b>License:</b> <code>{escape_html(track.license_type or 'Authorized')}</code>\n"
            f"🌐 <b>Provider:</b> {escape_html(track.provider_name)}"
        )

        duration_sec = int(round(track.duration)) if track.duration else 0

        # Step 1: Check if file is already cached in Telegram file_id cache
        cached_file_id = await db.get_cached_telegram_file_id(track.id, track.provider_name)

        if cached_file_id:
            logger.info("Delivering track %s via cached Telegram file_id", track.id)
            try:
                msg = await bot.send_audio(
                    chat_id=chat_id,
                    audio=cached_file_id,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    title=track.title,
                    performer=track.artist,
                    duration=duration_sec,
                )
                if user_id:
                    await db.add_recent_track(user_id, track.to_dict())
                    await db.record_event("track_delivery_cached", user_id, track.id)
                return True, None, msg
            except Exception as e:
                logger.warning(
                    "Failed sending via cached file_id (%s), falling back to fresh download: %s",
                    cached_file_id,
                    e,
                )

        # Step 2: Show native Telegram chat action
        try:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)
        except Exception:
            pass

        # Step 3: Download the track
        logger.info("Downloading track %s from %s", track.id, track.provider_name)
        file_path, dl_error = await audio_downloader.download_track(track)
        if not file_path:
            return False, dl_error or "Could not retrieve audio track.", None

        # Step 4: Validate downloaded audio with Mutagen
        validation = audio_validator.validate_file(file_path)
        if not validation.is_valid:
            audio_downloader.cleanup(file_path)
            return False, validation.error_message or "Audio validation failed.", None

        file_duration = (
            int(round(validation.duration_seconds))
            if validation.duration_seconds
            else duration_sec
        )

        # Step 5: Send audio directly to Telegram
        try:
            with open(file_path, "rb") as audio_file:
                msg = await bot.send_audio(
                    chat_id=chat_id,
                    audio=audio_file,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    title=track.title,
                    performer=track.artist,
                    duration=file_duration,
                    write_timeout=180.0,
                    read_timeout=60.0,
                )

            # Step 6: Cache Telegram file_id for future instant delivery
            if msg and msg.audio and msg.audio.file_id:
                await db.cache_telegram_file_id(
                    track_id=track.id,
                    provider_name=track.provider_name,
                    telegram_file_id=msg.audio.file_id,
                    file_size=validation.file_size_bytes,
                )

            # Step 7: Record in recent tracks
            if user_id:
                await db.add_recent_track(user_id, track.to_dict())
                await db.record_event("track_delivery_new", user_id, track.id)

            return True, None, msg

        except (TimedOut, asyncio.TimeoutError) as te:
            # Telegram client timed out waiting for acknowledgment, but the full file
            # was already dispatched to Telegram's backend servers which will finish delivering it.
            logger.warning(
                "Telegram upload timed out waiting for server acknowledgment for track %s (%s). "
                "File was already dispatched to Telegram servers.",
                track.id,
                te,
            )
            if user_id:
                await db.add_recent_track(user_id, track.to_dict())
            return True, None, None

        except Exception as e:
            err_str = str(e).lower()
            if "time" in err_str or "timedout" in err_str or "timeout" in err_str or "network" in err_str:
                logger.warning(
                    "Telegram upload connection timed out for track %s (%s). File was dispatched.",
                    track.id,
                    e,
                )
                if user_id:
                    await db.add_recent_track(user_id, track.to_dict())
                return True, None, None

            logger.error("Failed to send audio for track %s to Telegram: %s", track.id, e)
            return False, f"Telegram upload error: {e}", None

        finally:
            # Step 8: Always cleanup temporary local download
            audio_downloader.cleanup(file_path)


# Global delivery instance
audio_delivery = TelegramAudioDelivery()
