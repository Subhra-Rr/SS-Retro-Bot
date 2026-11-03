"""
Telegram Audio Delivery Service.
Delivers complete, validated audio files via Telegram Bot API sendAudio,
with genuine download progress tracking, smart duplicate detection,
BPM & musical key detection, custom filename formatting, and file_id caching.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from telegram import Bot, Message
from telegram.constants import ChatAction, ParseMode
from telegram.error import NetworkError, TelegramError, TimedOut

from database import db
from services.audio_validator import audio_validator
from services.downloader import audio_downloader
from services.providers.base import UnifiedTrack
from utils.audio_analysis import detect_bpm_and_key
from utils.formatting import escape_html, format_duration
from utils.security import sanitize_filename
from utils.telegram_helpers import safe_edit_message

logger = logging.getLogger(__name__)


class TelegramAudioDelivery:
    """Coordinates downloading, validation, delivery, caching, and cleanup."""

    async def deliver_track(
        self,
        bot: Bot,
        chat_id: int,
        track: UnifiedTrack,
        user_id: int | None = None,
        status_msg: Optional[Message] = None,
        quality: str = "320",
        output_format: str = "mp3",
    ) -> tuple[bool, Optional[str], Optional[Message]]:
        """
        Deliver full audio track to Telegram chat.
        Returns: (success: bool, error_message: Optional[str], message: Optional[Message])
        """
        duration_sec = int(round(track.duration)) if track.duration else 0

        # Step 1: Smart Duplicate Detection & Instant Delivery (Feature 3)
        cache_key = f"{track.provider_name}:{quality}:{output_format}"
        cached_file_id = await db.get_cached_telegram_file_id(track.id, cache_key)
        if not cached_file_id:
            cached_file_id = await db.get_cached_telegram_file_id(track.id, track.provider_name)

        if cached_file_id:
            logger.info("Delivering track %s via cached Telegram file_id (Instant Cache)", track.id)
            instant_caption = (
                f"🎵 <b>{escape_html(track.title)}</b>\n"
                f"👤 <b>Artist:</b> {escape_html(track.artist)}\n"
                f"💿 <b>Album:</b> {escape_html(track.album or 'Single')}\n"
                f"🎚️ <b>Quality:</b> <code>{quality} kbps {output_format.upper()}</code>\n"
                f"⚡ <i>Delivered instantly from high-speed cache</i>"
            )
            try:
                msg = await bot.send_audio(
                    chat_id=chat_id,
                    audio=cached_file_id,
                    caption=instant_caption,
                    parse_mode=ParseMode.HTML,
                    title=track.title,
                    performer=track.artist,
                    duration=duration_sec,
                )
                if user_id:
                    await db.add_recent_track(user_id, track.to_dict())
                    await db.record_download(user_id, track.to_dict())
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

        # Step 3: Setup genuine download progress hook (Feature 20)
        loop = asyncio.get_running_loop()
        last_edit_time = 0.0
        last_percent = -1

        def on_download_progress(percent: int, speed: str, eta: str) -> None:
            nonlocal last_edit_time, last_percent
            if not status_msg:
                return
            now = time.time()
            if (now - last_edit_time >= 3.5 and abs(percent - last_percent) >= 5) or percent == 100:
                last_edit_time = now
                last_percent = percent
                filled = min(10, max(0, percent // 10))
                bar = "▰" * filled + "▱" * (10 - filled)
                status_text = (
                    f"⏬ <i>Downloading '<b>{escape_html(track.title[:30])}</b>'...</i>\n"
                    f"[{bar}] <b>{percent}%</b>\n"
                    f"⚡ <b>Speed:</b> {speed} | <b>ETA:</b> {eta}"
                )
                asyncio.run_coroutine_threadsafe(
                    safe_edit_message(status_msg, status_text, parse_mode=ParseMode.HTML),
                    loop,
                )

        # Step 4: Download track
        logger.info("Downloading track %s (%s, %s kbps)", track.id, output_format, quality)
        file_path, dl_error = await audio_downloader.download_track(
            track=track,
            quality=quality,
            output_format=output_format,
            progress_callback=on_download_progress,
        )
        if not file_path:
            return False, dl_error or "Could not retrieve audio track.", None

        # Step 5: Validate downloaded audio
        validation = audio_validator.validate_file(file_path)
        if not validation.is_valid:
            audio_downloader.cleanup(file_path)
            return False, validation.error_message or "Audio validation failed.", None

        file_duration = (
            int(round(validation.duration_seconds))
            if validation.duration_seconds
            else duration_sec
        )

        # Step 6: BPM & Musical Key Detection (Feature 8)
        bpm, musical_key = detect_bpm_and_key(file_path, file_duration)

        # Step 7: Apply Custom Filename Format (Feature 9)
        fn_format = "artist_title"
        if user_id:
            try:
                settings = await db.get_user_settings(user_id)
                fn_format = settings.get("filename_format", "artist_title")
            except Exception:
                pass

        if fn_format == "title_artist":
            raw_filename = f"{track.title} - {track.artist}"
        elif fn_format == "title_year" and track.release_date:
            year = track.release_date[:4] if len(track.release_date) >= 4 else ""
            raw_filename = f"{track.title} ({year})" if year else f"{track.title}"
        elif fn_format == "retro_title":
            raw_filename = f"SS Retro - {track.title}"
        else:
            raw_filename = f"{track.artist} - {track.title}"

        ext = output_format.lower()
        safe_name = sanitize_filename(raw_filename)
        safe_filename = f"{safe_name}.{ext}" if not safe_name.lower().endswith(f".{ext}") else safe_name

        caption = (
            f"🎵 <b>{escape_html(track.title)}</b>\n"
            f"👤 <b>Artist:</b> {escape_html(track.artist)}\n"
            f"💿 <b>Album:</b> {escape_html(track.album or 'Single')}\n"
            f"🎚️ <b>Quality:</b> <code>{quality} kbps {output_format.upper()}</code>\n"
            f"🎹 <b>Tempo:</b> ~<code>{bpm} BPM</code> • <b>Key:</b> <code>{musical_key}</code>\n"
            f"🌐 <b>Provider:</b> {escape_html(track.provider_name)}"
        )

        # Step 8: Send audio directly to Telegram
        try:
            with open(file_path, "rb") as audio_file:
                msg = await bot.send_audio(
                    chat_id=chat_id,
                    audio=audio_file,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    title=track.title,
                    performer=track.artist,
                    filename=safe_filename,
                    duration=file_duration,
                    write_timeout=300.0,
                    read_timeout=120.0,
                    connect_timeout=60.0,
                )

            # Step 9: Cache Telegram file_id for future instant delivery
            if msg and msg.audio and msg.audio.file_id:
                await db.cache_telegram_file_id(
                    track_id=track.id,
                    provider_name=cache_key,
                    telegram_file_id=msg.audio.file_id,
                    file_size=validation.file_size_bytes,
                )

            # Step 10: Record in recent tracks & download statistics (Feature 14)
            if user_id:
                await db.add_recent_track(user_id, track.to_dict())
                await db.record_download(user_id, track.to_dict())
                await db.record_event("track_delivery_new", user_id, track.id)

            return True, None, msg

        except (TimedOut, asyncio.TimeoutError) as te:
            logger.warning(
                "Telegram upload timed out waiting for server acknowledgment for track %s: %s",
                track.id,
                te,
            )
            return False, "Upload to Telegram took too long or timed out. Please try again.", None

        except Exception as e:
            logger.error("Failed to send audio for track %s to Telegram: %s", track.id, e)
            return False, f"Telegram upload error: {e}", None

        finally:
            audio_downloader.cleanup(file_path)


# Global delivery instance
audio_delivery = TelegramAudioDelivery()
