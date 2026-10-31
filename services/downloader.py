"""
Audio Download and Metadata Tagging Service.
Safely downloads authorized audio files, streams with size limits, updates ID3 tags,
and manages temporary file lifecycles.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Optional

import httpx
import yt_dlp
from mutagen.id3 import ID3, TALB, TIT2, TPE1, ID3NoHeaderError

from config import config
from services.providers.base import UnifiedTrack
from utils.security import sanitize_filename

logger = logging.getLogger(__name__)


def license_allows_delivery(track: UnifiedTrack) -> tuple[bool, str]:
    """
    Verify whether the track has authorized permissions for complete full audio delivery.
    Enforces strict compliance with Section 4.
    """
    if not track.has_full_audio or not track.audio_url:
        return (
            False,
            "This track is available for metadata and discovery only. "
            "The provider does not license full-track audio downloads.",
        )

    if not track.allow_redistribution:
        return (
            False,
            "Redistribution permissions are not granted by the rights holder for this track.",
        )

    return True, "Authorized"


class AudioDownloader:
    """Manages secure downloads of authorized audio tracks."""

    def __init__(self, download_dir: Path | None = None, max_bytes: int | None = None):
        self.download_dir = download_dir or config.temp_download_dir
        self.max_bytes = max_bytes or config.max_file_size_bytes

    def _prepare_filepath(self, track: UnifiedTrack) -> Path:
        """Generate a collision-free local temporary file destination."""
        self.download_dir.mkdir(parents=True, exist_ok=True)
        unique_token = uuid.uuid4().hex[:12]
        return self.download_dir / f"stream_{unique_token}.mp3"

    def _tag_mp3(self, file_path: Path, track: UnifiedTrack) -> None:
        """Inject or standardize ID3v2 tags for rich Telegram display."""
        try:
            try:
                tags = ID3(str(file_path))
            except ID3NoHeaderError:
                tags = ID3()

            if track.title:
                tags["TIT2"] = TIT2(encoding=3, text=track.title)
            if track.artist:
                tags["TPE1"] = TPE1(encoding=3, text=track.artist)
            if track.album:
                tags["TALB"] = TALB(encoding=3, text=track.album)

            tags.save(str(file_path))
        except Exception as e:
            logger.warning("Could not write ID3 tags to %s: %s", file_path.name, e)

    def _sync_yt_download(
        self, url: str, output_path: Path, duration: Optional[int] = None
    ) -> tuple[Optional[Path], Optional[str]]:
        """Download and extract audio using yt-dlp and ffmpeg with dynamic bitrate scaling."""
        import yt_dlp

        # Check duration limit (Telegram Bot API hard limit is 50MB)
        if duration and duration > 7200:  # > 2 hours
            dur_min = duration // 60
            return (
                None,
                f"This continuous compilation is {dur_min} minutes long, which exceeds Telegram's 50MB upload limit. "
                "Please choose an individual song track for MP3 delivery.",
            )

        # Dynamic bitrate scaling to ensure the converted MP3 stays within Telegram's 49MB upload ceiling
        # 45MB = 368,640 kilobits
        if duration and duration > 0:
            calculated_kbps = int(360000 / duration)
            if calculated_kbps >= 320:
                chosen_quality = "320"
            elif calculated_kbps >= 192:
                chosen_quality = "192"
            elif calculated_kbps >= 128:
                chosen_quality = "128"
            elif calculated_kbps >= 64:
                chosen_quality = "64"
            else:
                chosen_quality = "48"
        else:
            chosen_quality = "320"

        # Unique, clean alphanumeric token to prevent format collisions with % (e.g. '99% Headshot'),
        # emojis, dots, or Windows path constraints.
        self.download_dir.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex[:12]
        outtmpl = str(self.download_dir / f"yt_{token}.%(ext)s")
        target_mp3 = self.download_dir / f"yt_{token}.mp3"

        import shutil
        ffmpeg_bin = shutil.which("ffmpeg")
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": outtmpl,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": chosen_quality,
                }
            ],
            "quiet": True,
            "no_warnings": True,
        }
        if ffmpeg_bin:
            ydl_opts["ffmpeg_location"] = ffmpeg_bin

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            # Check if target MP3 exists
            if target_mp3.exists() and target_mp3.stat().st_size > 0:
                file_size = target_mp3.stat().st_size
                if file_size > self.max_bytes:
                    target_mp3.unlink(missing_ok=True)
                    return (
                        None,
                        "Audio file exceeds Telegram's 50MB upload limit. Please select a shorter track.",
                    )
                return target_mp3, None

            # Fallback check: find any file produced with the token
            for candidate in self.download_dir.glob(f"yt_{token}.*"):
                if candidate.is_file() and candidate.stat().st_size > 0:
                    if candidate.suffix.lower() == ".mp3":
                        return candidate, None
                    # If file is not mp3 and ffmpeg is available, convert directly
                    if ffmpeg_bin:
                        import subprocess
                        conv_cmd = [
                            ffmpeg_bin,
                            "-y",
                            "-i", str(candidate),
                            "-vn",
                            "-acodec", "libmp3lame",
                            "-b:a", f"{chosen_quality}k",
                            str(target_mp3),
                        ]
                        subprocess.run(conv_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        candidate.unlink(missing_ok=True)
                        if target_mp3.exists() and target_mp3.stat().st_size > 0:
                            return target_mp3, None

            return None, "Audio extraction did not produce a valid file."
        except Exception as e:
            logger.error("yt-dlp download error on %s: %s", url, e)
            return None, f"Audio extraction error: {e}"

    async def download_track(self, track: UnifiedTrack) -> tuple[Optional[Path], Optional[str]]:
        """
        Download complete audio track to temporary disk storage.
        Returns: (file_path, error_message)
        """
        allowed, reason = license_allows_delivery(track)
        if not allowed:
            return None, reason

        file_path = self._prepare_filepath(track)

        # Handle YouTube Music / open audio streaming sources
        if (
            track.id.startswith("yt_")
            or track.provider_name == "YouTube Music"
            or (track.audio_url and ("youtube.com" in track.audio_url or "youtu.be" in track.audio_url))
        ):
            target_file = None
            try:
                target_file, dl_err = await asyncio.to_thread(
                    self._sync_yt_download, track.audio_url, file_path, track.duration
                )
                if not target_file:
                    return None, dl_err or "Failed to download and extract audio from stream."
                self._tag_mp3(target_file, track)
                return target_file, None
            except Exception as e:
                self.cleanup(target_file or file_path)
                return None, f"Audio download failed: {e}"

        total_downloaded = 0

        try:
            timeout = httpx.Timeout(config.download_timeout_seconds, connect=10.0)
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                async with client.stream("GET", track.audio_url) as response:
                    if response.status_code != 200:
                        return (
                            None,
                            f"Provider audio server returned HTTP error {response.status_code}.",
                        )

                    # Check Content-Length header if provided
                    content_length = response.headers.get("Content-Length")
                    if content_length and int(content_length) > self.max_bytes:
                        max_mb = self.max_bytes / (1024 * 1024)
                        return (
                            None,
                            f"Audio file exceeds {max_mb:.0f}MB Telegram upload limit.",
                        )

                    with open(file_path, "wb") as f:
                        async for chunk in response.aiter_bytes(chunk_size=65536):
                            total_downloaded += len(chunk)
                            if total_downloaded > self.max_bytes:
                                f.close()
                                self.cleanup(file_path)
                                return (
                                    None,
                                    "Download aborted: file exceeded maximum allowable file size.",
                                )
                            f.write(chunk)

            if total_downloaded == 0:
                self.cleanup(file_path)
                return None, "Downloaded file was empty (0 bytes received)."

            # Inject tags
            self._tag_mp3(file_path, track)
            return file_path, None

        except httpx.TimeoutException:
            self.cleanup(file_path)
            return None, "Download timed out while fetching audio stream."
        except Exception as e:
            self.cleanup(file_path)
            logger.error("Download failed for track %s: %s", track.id, e)
            return None, f"Download failed: {e}"

    @staticmethod
    def cleanup(file_path: Path | None) -> None:
        """Safely delete a temporary file if it exists."""
        if file_path and file_path.exists():
            try:
                file_path.unlink()
                logger.debug("Cleaned up temporary file %s", file_path.name)
            except Exception as e:
                logger.warning("Failed to clean up temporary file %s: %s", file_path, e)


# Global downloader instance
audio_downloader = AudioDownloader()
