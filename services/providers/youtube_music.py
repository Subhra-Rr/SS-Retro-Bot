"""
YouTube Music & Audio Provider using yt-dlp.
Enables universal music search across all languages (Hindi, Bollywood, Odia, Bengali,
Punjabi, English, etc.) and direct YouTube URL-to-MP3 conversion for Telegram delivery.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

import yt_dlp

from config import config
from services.providers.base import BaseMusicProvider, UnifiedTrack

logger = logging.getLogger(__name__)

YOUTUBE_URL_PATTERN = re.compile(
    r"(https?://)?(www\.|m\.|music\.)?(youtube\.com/(watch\?v=|shorts/|playlist\?|v/)|youtu\.be/)([a-zA-Z0-9_-]{11})",
    re.IGNORECASE,
)


class YouTubeMusicProvider(BaseMusicProvider):
    """YouTube Music / open audio provider with full-track audio delivery."""

    name = "YouTube Music"
    can_deliver_full_audio = True

    def is_configured(self) -> bool:
        """yt-dlp is locally installed and ready."""
        return True

    def is_youtube_url(self, text: str) -> bool:
        """Check if a string contains or is a valid YouTube URL."""
        return self.extract_video_id(text) is not None

    def extract_video_id(self, text: str) -> Optional[str]:
        """Extract the 11-character video ID from any YouTube URL format."""
        if not text:
            return None
        raw = text.strip()
        # 1. youtu.be/<id>
        m = re.search(r"youtu\.be/([a-zA-Z0-9_-]{11})", raw)
        if m:
            return m.group(1)
        # 2. shorts/<id>
        m = re.search(r"shorts/([a-zA-Z0-9_-]{11})", raw)
        if m:
            return m.group(1)
        # 3. watch?v=<id> via url parsing
        try:
            from urllib.parse import parse_qs, urlparse
            parsed = urlparse(raw if "://" in raw else f"https://{raw}")
            if "youtube.com" in parsed.netloc:
                qs = parse_qs(parsed.query)
                if "v" in qs and qs["v"]:
                    return qs["v"][0]
        except Exception:
            pass
        # 4. generic fallback (?v= or &v=)
        m = re.search(r"[?&]v=([a-zA-Z0-9_-]{11})", raw)
        if m:
            return m.group(1)
        # 5. /embed/<id> or /v/<id>
        m = re.search(r"/(?:embed|v)/([a-zA-Z0-9_-]{11})", raw)
        if m:
            return m.group(1)
        return None

    def _sync_search(self, query: str, limit: int) -> List[Dict[str, Any]]:
        """Synchronously execute fast flat search with yt-dlp."""
        ydl_opts = {
            "format": "bestaudio/best",
            "extract_flat": True,
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
        }
        search_query = f"ytsearch{min(limit, 15)}:{query}"
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(search_query, download=False)
                if not info:
                    return []
                return info.get("entries", []) or []
        except Exception as e:
            logger.warning("yt-dlp search error on query '%s': %s", query, e)
            return []

    def _normalize_entry(self, entry: Dict[str, Any]) -> Optional[UnifiedTrack]:
        """Convert yt-dlp flat entry into UnifiedTrack with clean metadata."""
        video_id = entry.get("id")
        if not video_id:
            return None

        raw_title = (entry.get("title") or "Unknown Title").strip()
        channel = (entry.get("uploader") or entry.get("channel") or "Artist").strip()
        artist = channel
        title = raw_title

        # Clean "Artist - Title" formats common in YouTube audio uploads
        if " - " in raw_title:
            parts = raw_title.split(" - ", 1)
            artist = parts[0].strip()
            title = parts[1].strip()
        elif " | " in raw_title:
            parts = raw_title.split(" | ", 1)
            title = parts[0].strip()
            artist = parts[1].strip()

        # Clean brackets / official video noise from title
        clean_title = re.sub(r"\(.*?official.*?\)", "", title, flags=re.IGNORECASE)
        clean_title = re.sub(r"\[.*?official.*?\]", "", clean_title, flags=re.IGNORECASE)
        clean_title = clean_title.strip() or title

        duration = entry.get("duration")
        cover_url = entry.get("thumbnail") or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
        audio_url = f"https://www.youtube.com/watch?v={video_id}"

        return UnifiedTrack(
            id=f"yt_{video_id}",
            provider_name=self.name,
            title=clean_title,
            artist=artist,
            album="YouTube Audio",
            duration=int(duration) if duration else None,
            release_date=None,
            cover_url=cover_url,
            audio_url=audio_url,
            has_full_audio=True,
            license_type="YouTube Direct Audio",
            allow_redistribution=True,
            genre=None,
            language=None,
            external_url=audio_url,
        )

    async def search(
        self, query: str, limit: int = 15, offset: int = 0
    ) -> List[UnifiedTrack]:
        """Asynchronously search tracks across YouTube's live music catalogue."""
        clean_query = query.strip()
        if not clean_query:
            return []

        entries = await asyncio.to_thread(self._sync_search, clean_query, limit)
        tracks: List[UnifiedTrack] = []
        for entry in entries:
            t = self._normalize_entry(entry)
            if t:
                tracks.append(t)
        return tracks

    async def get_track(self, track_id: str) -> Optional[UnifiedTrack]:
        """Fetch single track details by video ID."""
        clean_id = track_id.replace("yt_", "").strip()
        url = f"https://www.youtube.com/watch?v={clean_id}"
        return await self.get_track_from_url(url)

    async def get_track_from_url(self, url: str) -> Optional[UnifiedTrack]:
        """Fetch track metadata directly from any YouTube URL (video, short, music link)."""
        video_id = self.extract_video_id(url)
        clean_url = f"https://www.youtube.com/watch?v={video_id}" if video_id else url.strip()

        def _get_info():
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "extract_flat": True,
                "skip_download": True,
            }
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(clean_url, download=False)
            except Exception as e:
                logger.error("Failed to extract YouTube URL %s: %s", clean_url, e)
                return None

        info = await asyncio.to_thread(_get_info)
        if info:
            return self._normalize_entry(info)
        return None

    async def get_top_tracks(self, limit: int = 10) -> List[UnifiedTrack]:
        """Fetch trending / top individual song hits, filtering out non-stop compilations."""
        results = await self.search("popular songs official music video audio", limit=limit * 2)
        # Prioritize genuine single song tracks (duration between 60s and 720s)
        filtered = [t for t in results if not t.duration or (60 <= t.duration <= 720)]
        return filtered[:limit] if filtered else results[:limit]
