"""
Audio Validation and Inspection Service using Mutagen.
Verifies audio integrity, formats, duration, bitrates, and Telegram file size constraints.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import mutagen
from mutagen.mp3 import MP3

from config import config

logger = logging.getLogger(__name__)


@dataclass
class AudioValidationResult:
    """Outcome of audio validation checks."""

    is_valid: bool
    error_message: Optional[str] = None
    duration_seconds: Optional[int] = None
    bitrate_kbps: Optional[int] = None
    file_size_bytes: Optional[int] = None
    mime_type: Optional[str] = None
    artist: Optional[str] = None
    title: Optional[str] = None
    album: Optional[str] = None


class AudioValidator:
    """Validates audio files before sending to Telegram."""

    def __init__(self, max_bytes: int | None = None):
        self.max_bytes = max_bytes or config.max_file_size_bytes

    def validate_file(self, file_path: Path) -> AudioValidationResult:
        """
        Perform comprehensive validation of a local audio file.
        Checks existence, non-emptiness, file size limit, and mutagen header inspection.
        """
        if not file_path.exists():
            return AudioValidationResult(is_valid=False, error_message="Audio file does not exist on disk.")

        file_size = file_path.stat().st_size
        if file_size == 0:
            return AudioValidationResult(is_valid=False, error_message="Audio file is empty (0 bytes).")

        if file_size > self.max_bytes:
            max_mb = self.max_bytes / (1024 * 1024)
            actual_mb = file_size / (1024 * 1024)
            return AudioValidationResult(
                is_valid=False,
                error_message=f"File exceeds Telegram bot limit of {max_mb:.0f}MB (file is {actual_mb:.1f}MB).",
                file_size_bytes=file_size,
            )

        # Mutagen audio inspection
        try:
            audio = mutagen.File(str(file_path))
            if audio is None:
                return AudioValidationResult(
                    is_valid=False,
                    error_message="Unsupported or invalid audio format (Mutagen could not parse stream).",
                    file_size_bytes=file_size,
                )

            duration = int(round(audio.info.length)) if hasattr(audio.info, "length") else None
            bitrate = (
                int(round(audio.info.bitrate / 1000))
                if hasattr(audio.info, "bitrate") and audio.info.bitrate
                else None
            )

            # Minimum audio duration check (must be at least 5 seconds)
            if duration is not None and duration < 5:
                return AudioValidationResult(
                    is_valid=False,
                    error_message=f"Audio duration is too short ({duration}s). Expected full track.",
                    duration_seconds=duration,
                    file_size_bytes=file_size,
                )

            # Extract tags if available
            artist = None
            title = None
            album = None
            if audio.tags:
                artist = str(audio.tags.get("TPE1") or audio.tags.get("artist") or "")
                title = str(audio.tags.get("TIT2") or audio.tags.get("title") or "")
                album = str(audio.tags.get("TALB") or audio.tags.get("album") or "")

            mime_type = "audio/mpeg" if isinstance(audio, MP3) or file_path.suffix.lower() == ".mp3" else "audio/mp4"

            return AudioValidationResult(
                is_valid=True,
                duration_seconds=duration,
                bitrate_kbps=bitrate,
                file_size_bytes=file_size,
                mime_type=mime_type,
                artist=artist or None,
                title=title or None,
                album=album or None,
            )

        except mutagen.MutagenError as e:
            logger.warning("Mutagen parse error on %s: %s", file_path.name, e)
            return AudioValidationResult(
                is_valid=False,
                error_message=f"Corrupted or invalid audio stream: {e}",
                file_size_bytes=file_size,
            )
        except Exception as e:
            logger.error("Unexpected error validating audio file %s: %s", file_path.name, e)
            return AudioValidationResult(
                is_valid=False,
                error_message=f"Audio validation failed: {e}",
                file_size_bytes=file_size,
            )


# Global audio validator
audio_validator = AudioValidator()
