"""
iTunes Search API Provider.
Provides global music metadata discovery across Indian (Bollywood, Odia, Bengali, Punjabi,
South Indian) and international catalogues, with cover art and album information.
Does not supply unauthorized full audio files; acts as an authorized metadata discovery provider.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from config import config
from services.providers.base import BaseMusicProvider, UnifiedTrack

logger = logging.getLogger(__name__)

ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
ITUNES_LOOKUP_URL = "https://itunes.apple.com/lookup"


class ITunesProvider(BaseMusicProvider):
    """Apple iTunes Search API provider for rich track and album metadata."""

    name = "iTunes"
    can_deliver_full_audio = False  # Metadata discovery only

    def is_configured(self) -> bool:
        """iTunes public search API requires no credentials."""
        return True

    def _normalize_track(self, item: Dict[str, Any]) -> UnifiedTrack:
        """Normalize iTunes song object into UnifiedTrack."""
        track_id = f"itunes_{item.get('trackId')}"
        title = item.get("trackName") or "Unknown Title"
        artist = item.get("artistName") or "Unknown Artist"
        album = item.get("collectionName")

        # Convert millis to seconds
        duration_ms = item.get("trackTimeMillis")
        duration_sec = int(duration_ms / 1000) if duration_ms else None

        # Upgrade artwork resolution to 600x600 if available
        artwork = item.get("artworkUrl100") or item.get("artworkUrl60")
        if artwork and "100x100" in artwork:
            artwork = artwork.replace("100x100bb.jpg", "600x600bb.jpg")

        release_date = item.get("releaseDate")
        if release_date and "T" in release_date:
            release_date = release_date.split("T")[0]

        genre = item.get("primaryGenreName")
        track_url = item.get("trackViewUrl") or item.get("collectionViewUrl")

        return UnifiedTrack(
            id=track_id,
            provider_name=self.name,
            title=title,
            artist=artist,
            album=album,
            duration=duration_sec,
            release_date=release_date,
            cover_url=artwork,
            audio_url=None,  # No unauthorized full audio
            has_full_audio=False,
            license_type="Commercial (Apple Music)",
            allow_redistribution=False,
            genre=genre,
            language=None,
            external_url=track_url,
        )

    async def search(
        self, query: str, limit: int = 10, offset: int = 0
    ) -> List[UnifiedTrack]:
        """Search iTunes music catalogue."""
        params = {
            "term": query,
            "media": "music",
            "entity": "song",
            "limit": min(limit, 20),
            "offset": offset,
        }

        try:
            async with httpx.AsyncClient(timeout=config.search_timeout_seconds) as client:
                response = await client.get(ITUNES_SEARCH_URL, params=params)
                if response.status_code != 200:
                    logger.warning("iTunes search failed with HTTP %s", response.status_code)
                    return []

                data = response.json()
                results = data.get("results", [])
                return [self._normalize_track(item) for item in results]

        except httpx.TimeoutException:
            logger.warning("iTunes search timed out")
            return []
        except Exception as e:
            logger.error("Error in iTunes search: %s", e)
            return []

    async def get_track(self, track_id: str) -> Optional[UnifiedTrack]:
        """Fetch track by iTunes numeric ID."""
        clean_id = track_id.replace("itunes_", "").strip()
        params = {"id": clean_id, "entity": "song"}

        try:
            async with httpx.AsyncClient(timeout=config.search_timeout_seconds) as client:
                response = await client.get(ITUNES_LOOKUP_URL, params=params)
                if response.status_code != 200:
                    return None

                data = response.json()
                results = data.get("results", [])
                if results:
                    return self._normalize_track(results[0])
                return None
        except Exception as e:
            logger.error("Failed to retrieve iTunes track %s: %s", track_id, e)
            return None

    async def get_top_tracks(self, limit: int = 10) -> List[UnifiedTrack]:
        """Fetch top popular songs via iTunes search shortcut."""
        return await self.search("top hits", limit=limit)
