"""
YouTube Music & Audio Provider using yt-dlp.
Enables universal music search across all languages (Hindi, Bollywood, Odia, Bengali,
Punjabi, English, etc.) and supplies complete audio tracks for Telegram delivery.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

import yt_dlp

from config import config
from services.providers.base import BaseMusicProvider, UnifiedTrack

logger = logging.getLogger(__name__)


class YouTubeMusicProvider(BaseMusicProvider):
    """YouTube Music / open audio provider with full-track audio delivery."""

    name = "YouTube Music"
    can_deliver_full_audio = True

    def is_configured(self) -> bool:
        """yt-dlp is locally installed and ready."""
        return True

    def _sync_search(self, query: str, limit: int) -> List[Dict[str, Any]]:
        """Synchronously execute fast flat search with yt-dlp."""
        ydl_opts = {
            "format": "bestaudio/best",
            "extract_flat": True,
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
        }
        search_query = f"ytsearch{min(limit, 10)}:{query}"
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
        """Convert yt-dlp flat entry into UnifiedTrack."""
        video_id = entry.get("id")
        if not video_id:
            return None

        raw_title = entry.get("title") or "Unknown Title"
        # Often title is in format "Artist - Title" or "Title | Official Video"
        artist = entry.get("uploader") or entry.get("channel") or "Artist"
        title = raw_title

        if " - " in raw_title:
            parts = raw_title.split(" - ", 1)
            artist = parts[0].strip()
            title = parts[1].strip()

        duration = entry.get("duration")
        cover_url = entry.get("thumbnail") or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
        audio_url = f"https://www.youtube.com/watch?v={video_id}"

        return UnifiedTrack(
            id=f"yt_{video_id}",
            provider_name=self.name,
            title=title,
            artist=artist,
            album="Single",
            duration=int(duration) if duration else None,
            release_date=None,
            cover_url=cover_url,
            audio_url=audio_url,
            has_full_audio=True,
            license_type="Audio Delivery",
            allow_redistribution=True,
            genre=None,
            language=None,
            external_url=audio_url,
        )

    async def search(
        self, query: str, limit: int = 10, offset: int = 0
    ) -> List[UnifiedTrack]:
        """Asynchronously search tracks across global and Indian catalogues."""
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

        def _get_info():
            ydl_opts = {"quiet": True, "no_warnings": True, "extract_flat": True}
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(url, download=False)
            except Exception:
                return None

        info = await asyncio.to_thread(_get_info)
        if info:
            return self._normalize_entry(info)
        return None

    async def get_top_tracks(self, limit: int = 10) -> List[UnifiedTrack]:
        """Fetch trending / top hits."""
        return await self.search("top music hits", limit=limit)
