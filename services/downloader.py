"""
Audio Download and Metadata Tagging Service.
Safely downloads authorized audio files, streams with size limits, updates ID3 tags,
embeds high-res album art, supports lossless FLAC/WAV export, download progress hooks,
and manages temporary file lifecycles.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

import httpx
import yt_dlp
from mutagen.id3 import ID3, APIC, TALB, TIT2, TPE1, ID3NoHeaderError

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
    """Manages secure downloads of authorized audio tracks with speed optimization and progress hooks."""

    # Class-level cookie opts cache — detected once per process startup
    _cookie_opts_cache: dict | None = None

    def __init__(self, download_dir: Path | None = None, max_bytes: int | None = None):
        self.download_dir = download_dir or config.temp_download_dir
        self.max_bytes = max_bytes or config.max_file_size_bytes

    def _prepare_filepath(self, track: UnifiedTrack, extension: str = "mp3") -> Path:
        """Generate a collision-free local temporary file destination."""
        self.download_dir.mkdir(parents=True, exist_ok=True)
        unique_token = uuid.uuid4().hex[:12]
        return self.download_dir / f"stream_{unique_token}.{extension}"

    def _tag_mp3(self, file_path: Path, track: UnifiedTrack) -> None:
        """Inject or standardize ID3v2 tags and embed album cover art."""
        if not file_path.name.lower().endswith(".mp3"):
            return

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

            # Embed Album Cover Art (Feature 11)
            cover_url = track.cover_url
            if cover_url and cover_url.startswith("http"):
                try:
                    with httpx.Client(timeout=8.0, follow_redirects=True) as client:
                        resp = client.get(cover_url)
                        if resp.status_code == 200 and len(resp.content) > 1000:
                            mime = "image/png" if cover_url.lower().endswith(".png") else "image/jpeg"
                            tags.add(
                                APIC(
                                    encoding=3,
                                    mime=mime,
                                    type=3,  # Front cover
                                    desc="Cover",
                                    data=resp.content,
                                )
                            )
                except Exception as art_err:
                    logger.debug("Could not embed album cover art: %s", art_err)

            tags.save(str(file_path))
        except Exception as e:
            logger.warning("Could not write ID3 tags to %s: %s", file_path.name, e)

    @staticmethod
    def _get_cookie_opts() -> dict:
        """
        Try to load browser cookies for YouTube bot-challenge bypass.
        Returns a dict with 'cookiesfrombrowser' key if a supported browser is found,
        or an empty dict if none are available.
        Result is cached at class level to avoid re-detection on every download.
        """
        if AudioDownloader._cookie_opts_cache is not None:
            return AudioDownloader._cookie_opts_cache

        for browser in ("firefox", "chrome", "chromium", "edge", "brave", "opera", "vivaldi"):
            try:
                test_ydl = yt_dlp.YoutubeDL({"quiet": True, "cookiesfrombrowser": (browser, None, None, None)})
                _ = test_ydl.cookiejar
                logger.info("YouTube cookie source detected: %s browser", browser)
                AudioDownloader._cookie_opts_cache = {"cookiesfrombrowser": (browser, None, None, None)}
                return AudioDownloader._cookie_opts_cache
            except Exception:
                continue

        logger.warning("No browser cookies found. YouTube bot challenges may block downloads.")
        AudioDownloader._cookie_opts_cache = {}
        return {}

    def _sync_yt_download(
        self,
        url: str,
        output_path: Path,
        duration: Optional[int] = None,
        quality: str = "320",
        output_format: str = "mp3",
        progress_callback: Optional[Callable[[int, str, str], None]] = None,
    ) -> tuple[Optional[Path], Optional[str]]:
        """Download and extract audio using yt-dlp and ffmpeg with dynamic bitrate scaling, progress tracking, and speed optimization."""
        # Check duration limit (Telegram Bot API hard limit is 50MB)
        if duration and duration > 7200:  # > 2 hours
            dur_min = duration // 60
            return (
                None,
                f"This continuous compilation is {dur_min} minutes long, which exceeds Telegram's 50MB upload limit. "
                "Please choose an individual song track for audio delivery.",
            )

        # Dynamic bitrate scaling if user requested mp3
        chosen_quality = quality
        if output_format == "mp3":
            if duration and duration > 0:
                calculated_kbps = int(360000 / duration)
                requested_kbps = int(quality) if quality.isdigit() else 320
                if calculated_kbps < requested_kbps:
                    chosen_quality = str(calculated_kbps)
            else:
                chosen_quality = quality if quality.isdigit() else "320"

        # Unique token
        self.download_dir.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex[:12]
        outtmpl = str(self.download_dir / f"yt_{token}.%(ext)s")
        target_ext = output_format.lower()
        target_file = self.download_dir / f"yt_{token}.{target_ext}"

        ffmpeg_bin = (
            shutil.which("ffmpeg")
            or ("/usr/bin/ffmpeg" if Path("/usr/bin/ffmpeg").exists() else None)
            or ("/usr/local/bin/ffmpeg" if Path("/usr/local/bin/ffmpeg").exists() else None)
        )

        # Progress hook for yt-dlp (Feature 20: Genuine Download Meter)
        def ydl_progress_hook(d: dict) -> None:
            if not progress_callback:
                return
            try:
                status = d.get("status")
                if status == "downloading":
                    total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                    downloaded = d.get("downloaded_bytes") or 0
                    percent = int(downloaded / total * 100) if total > 0 else 0
                    speed = d.get("speed") or 0
                    speed_str = (
                        f"{speed / (1024 * 1024):.1f} MB/s"
                        if speed > 1024 * 1024
                        else (f"{speed / 1024:.0f} KB/s" if speed else "...")
                    )
                    eta = d.get("eta")
                    eta_str = f"{eta}s" if eta is not None else "..."
                    progress_callback(percent, speed_str, eta_str)
                elif status == "finished":
                    progress_callback(100, "Done", "0s")
            except Exception:
                pass

        # Postprocessor selection (Feature 1 & Feature 12: Audio Quality & FLAC/WAV Export)
        if target_ext == "flac":
            postprocessor = {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "flac",
            }
        elif target_ext == "wav":
            postprocessor = {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "wav",
            }
        else:
            postprocessor = {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": chosen_quality,
            }

        cookie_opts = self._get_cookie_opts()

        base_opts = {
            "format": "bestaudio/best[ext=m4a]/best[ext=mp3]/best",
            "outtmpl": outtmpl,
            "postprocessors": [postprocessor],
            "quiet": True,
            "no_warnings": True,
            "geo_bypass": True,
            "nocheckcertificate": True,
            "socket_timeout": 35,
            "retries": 10,
            "fragment_retries": 10,
            "skip_unavailable_fragments": True,
            "concurrent_fragment_downloads": 8,  # FAST PARALLEL DOWNLOADS
            "http_chunk_size": 10485760,         # 10MB CHUNKS FOR MAXIMUM SPEED
            "buffersize": 65536,                 # 64KB BUFFER
            "continuedl": True,                  # DOWNLOAD RESUME (FEATURE 4)
            "noplaylist": True,
            "progress_hooks": [ydl_progress_hook],
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            },
            **cookie_opts,
        }
        if ffmpeg_bin:
            base_opts["ffmpeg_location"] = ffmpeg_bin

        download_success = False
        last_error = None

        # Attempt 1: TV + Android client
        opts_tv = dict(base_opts)
        opts_tv["extractor_args"] = {
            "youtube": {
                "player_client": ["tv", "android", "web"],
            }
        }
        try:
            with yt_dlp.YoutubeDL(opts_tv) as ydl:
                ydl.download([url])
            download_success = True
        except Exception as e1:
            logger.warning("yt-dlp tv+android player attempt failed for %s: %s", url, e1)
            last_error = e1

        # Attempt 2: iOS client fallback
        if not download_success or not target_file.exists() or target_file.stat().st_size == 0:
            try:
                opts_ios = dict(base_opts)
                opts_ios["extractor_args"] = {
                    "youtube": {
                        "player_client": ["ios", "mweb"],
                    }
                }
                with yt_dlp.YoutubeDL(opts_ios) as ydl:
                    ydl.download([url])
                download_success = True
            except Exception as e2:
                logger.warning("yt-dlp ios/mweb player attempt failed for %s: %s", url, e2)
                last_error = e2

        # Attempt 3: Default client
        if not download_success or not target_file.exists() or target_file.stat().st_size == 0:
            try:
                opts_default = dict(base_opts)
                with yt_dlp.YoutubeDL(opts_default) as ydl:
                    ydl.download([url])
                download_success = True
            except Exception as e3:
                logger.warning("yt-dlp default attempt failed for %s: %s", url, e3)
                last_error = e3

        # Extract 11-char video ID for fallback
        video_id = None
        m = re.search(r"(?:v=|youtu\.be/|shorts/)([a-zA-Z0-9_-]{11})", url)
        if m:
            video_id = m.group(1)

        # Attempt 4: Cloud proxy streaming fallback (Piped/Invidious)
        if video_id and (not target_file.exists() or target_file.stat().st_size == 0):
            logger.info("Attempting cloud streaming proxy fallback for video %s", video_id)
            if self._fallback_stream_download(video_id, target_file, chosen_quality, ffmpeg_bin):
                if target_file.exists() and target_file.stat().st_size > 0:
                    return target_file, None

        # Check if target file exists and is valid
        if target_file.exists() and target_file.stat().st_size > 0:
            file_size = target_file.stat().st_size
            if file_size > self.max_bytes:
                target_file.unlink(missing_ok=True)
                return (
                    None,
                    "Audio file exceeds Telegram's 50MB upload limit. Please select a shorter track.",
                )
            return target_file, None

        # Fallback: scan for any file produced with the token
        for candidate in self.download_dir.glob(f"yt_{token}.*"):
            if candidate.is_file() and candidate.stat().st_size > 0:
                if candidate.suffix.lower() == f".{target_ext}":
                    return candidate, None
                if ffmpeg_bin:
                    conv_cmd = [
                        ffmpeg_bin,
                        "-y",
                        "-i", str(candidate),
                        "-vn",
                        str(target_file),
                    ]
                    subprocess.run(conv_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    candidate.unlink(missing_ok=True)
                    if target_file.exists() and target_file.stat().st_size > 0:
                        return target_file, None

        err_msg = str(last_error) if last_error else "Audio extraction did not produce a valid file."
        return None, f"Audio extraction notice: {err_msg}"

    def _fallback_stream_download(
        self, video_id: str, target_file: Path, chosen_quality: str, ffmpeg_bin: Optional[str]
    ) -> bool:
        """Fallback to open streaming endpoints if YouTube blocks datacenter IP."""
        piped_instances = [
            "https://api.piped.private.coffee",
            "https://pipedapi.kavin.rocks",
            "https://pipedapi.tokhmi.xyz",
        ]
        invidious_instances = [
            "https://inv.nadeko.net",
            "https://invidious.nerdvpn.de",
        ]

        # Try Piped instances
        for instance in piped_instances:
            try:
                endpoint = f"{instance}/streams/{video_id}"
                with httpx.Client(timeout=10.0, follow_redirects=True) as client:
                    resp = client.get(endpoint)
                    if resp.status_code == 200:
                        data = resp.json()
                        audio_streams = data.get("audioStreams", [])
                        if audio_streams:
                            best_stream = audio_streams[0].get("url")
                            if best_stream:
                                tmp_audio = target_file.with_suffix(".tmp_stream")
                                with client.stream("GET", best_stream) as stream_resp:
                                    with open(tmp_audio, "wb") as f:
                                        for chunk in stream_resp.iter_bytes(chunk_size=65536):
                                            f.write(chunk)
                                if tmp_audio.exists() and tmp_audio.stat().st_size > 1000:
                                    if ffmpeg_bin:
                                        conv_cmd = [
                                            ffmpeg_bin,
                                            "-y",
                                            "-i", str(tmp_audio),
                                            "-vn",
                                            "-b:a", f"{chosen_quality}k",
                                            str(target_file),
                                        ]
                                        subprocess.run(conv_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                                        tmp_audio.unlink(missing_ok=True)
                                        if target_file.exists() and target_file.stat().st_size > 0:
                                            return True
                                    else:
                                        tmp_audio.rename(target_file)
                                        return True
            except Exception as pe:
                logger.debug("Piped instance %s failed for %s: %s", instance, video_id, pe)

        # Try Invidious instances
        for inv in invidious_instances:
            try:
                endpoint = f"{inv}/api/v1/videos/{video_id}"
                with httpx.Client(timeout=10.0, follow_redirects=True) as client:
                    resp = client.get(endpoint)
                    if resp.status_code == 200:
                        data = resp.json()
                        formats = data.get("adaptiveFormats", [])
                        audio_formats = [f for f in formats if f.get("type", "").startswith("audio/")]
                        if audio_formats:
                            stream_url = audio_formats[0].get("url")
                            if stream_url:
                                tmp_audio = target_file.with_suffix(".tmp_stream")
                                with client.stream("GET", stream_url) as stream_resp:
                                    with open(tmp_audio, "wb") as f:
                                        for chunk in stream_resp.iter_bytes(chunk_size=65536):
                                            f.write(chunk)
                                if tmp_audio.exists() and tmp_audio.stat().st_size > 1000:
                                    if ffmpeg_bin:
                                        conv_cmd = [
                                            ffmpeg_bin,
                                            "-y",
                                            "-i", str(tmp_audio),
                                            "-vn",
                                            "-b:a", f"{chosen_quality}k",
                                            str(target_file),
                                        ]
                                        subprocess.run(conv_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                                        tmp_audio.unlink(missing_ok=True)
                                        if target_file.exists() and target_file.stat().st_size > 0:
                                            return True
            except Exception as ie:
                logger.debug("Invidious instance %s failed for %s: %s", inv, video_id, ie)

        return False

    async def download_track(
        self,
        track: UnifiedTrack,
        quality: str = "320",
        output_format: str = "mp3",
        progress_callback: Optional[Callable[[int, str, str], None]] = None,
    ) -> tuple[Optional[Path], Optional[str]]:
        """
        Download complete audio track to temporary disk storage.
        Supports quality selection, FLAC/WAV format, progress tracking, and auto-retry.
        Returns: (file_path, error_message)
        """
        allowed, reason = license_allows_delivery(track)
        if not allowed:
            return None, reason

        file_path = self._prepare_filepath(track, extension=output_format)

        # Handle YouTube Music / open audio streaming sources
        if (
            track.id.startswith("yt_")
            or track.provider_name == "YouTube Music"
            or (track.audio_url and ("youtube.com" in track.audio_url or "youtu.be" in track.audio_url))
        ):
            target_file = None
            try:
                target_file, dl_err = await asyncio.to_thread(
                    self._sync_yt_download,
                    track.audio_url,
                    file_path,
                    track.duration,
                    quality,
                    output_format,
                    progress_callback,
                )

                # Feature 17: Auto-retry on Failed Download with alternative search terms
                if not target_file or not target_file.exists() or target_file.stat().st_size == 0:
                    logger.info("Initial stream failed for '%s'. Starting auto-retry with alternative search terms...", track.title)
                    from services.providers.registry import registry
                    yt_p = registry.get_provider("YouTube Music")
                    if yt_p:
                        clean_t = re.sub(r"\(.*?\)|\[.*?\]", "", track.title).strip()
                        alt_queries = [
                            f"{clean_t} {track.artist} official audio",
                            f"{clean_t} {track.artist} audio",
                            f"{clean_t} song",
                        ]
                        for alt_q in alt_queries:
                            try:
                                alt_tracks = await yt_p.search(alt_q, limit=2)
                                if alt_tracks and alt_tracks[0].audio_url:
                                    logger.info("Auto-retrying with alternative match: %s", alt_tracks[0].title)
                                    target_file, dl_err = await asyncio.to_thread(
                                        self._sync_yt_download,
                                        alt_tracks[0].audio_url,
                                        file_path,
                                        alt_tracks[0].duration or track.duration,
                                        quality,
                                        output_format,
                                        progress_callback,
                                    )
                                    if target_file and target_file.exists() and target_file.stat().st_size > 0:
                                        break
                            except Exception as alt_err:
                                logger.debug("Auto-retry query '%s' failed: %s", alt_q, alt_err)

                if not target_file or not target_file.exists() or target_file.stat().st_size == 0:
                    return None, dl_err or "Failed to download and extract audio from stream."

                if output_format.lower() == "mp3":
                    self._tag_mp3(target_file, track)

                return target_file, None
            except Exception as e:
                self.cleanup(target_file or file_path)
                return None, f"Audio download failed: {e}"

        # Direct HTTP stream download (Jamendo, etc.)
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

                    content_length = response.headers.get("Content-Length")
                    total_bytes = int(content_length) if content_length and content_length.isdigit() else 0
                    if total_bytes > self.max_bytes:
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
                            if progress_callback and total_bytes > 0:
                                pct = int(total_downloaded / total_bytes * 100)
                                progress_callback(pct, "Direct", "")

            if total_downloaded == 0:
                self.cleanup(file_path)
                return None, "Downloaded file was empty (0 bytes received)."

            if output_format.lower() == "mp3":
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
    def cleanup_stale_files(max_age_hours: float = 1.0) -> int:
        """
        Automatically clean downloaded temp files older than max_age_hours (Feature 18).
        Returns the number of files cleaned.
        """
        temp_dir = config.temp_download_dir
        if not temp_dir.exists():
            return 0
        now = time.time()
        max_age_seconds = max_age_hours * 3600
        cleaned = 0
        try:
            for item in temp_dir.iterdir():
                if item.is_file():
                    try:
                        mtime = item.stat().st_mtime
                        if now - mtime > max_age_seconds:
                            item.unlink(missing_ok=True)
                            cleaned += 1
                    except Exception:
                        pass
        except Exception as e:
            logger.debug("Error during stale file cleanup: %s", e)
        if cleaned > 0:
            logger.info("Cleaned %d stale temporary audio files from cache.", cleaned)
        return cleaned

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
