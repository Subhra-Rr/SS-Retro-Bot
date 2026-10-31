"""
Jamendo Music API Provider.
Searches Jamendo's Creative Commons music catalogue and supplies authorized full-track MP3 audio.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from config import config
from services.providers.base import BaseMusicProvider, UnifiedTrack

logger = logging.getLogger(__name__)

JAMENDO_API_URL = "https://api.jamendo.com/v3.0/tracks/"


class JamendoProvider(BaseMusicProvider):
    """Jamendo API implementation for CC-licensed independent tracks with full MP3 delivery."""

    name = "Jamendo"
    can_deliver_full_audio = True

    def __init__(self, client_id: str | None = None):
        self.client_id = config.jamendo_client_id if client_id is None else client_id

    def is_configured(self) -> bool:
        """Returns True if a valid client_id is configured."""
        if not self.client_id:
            return False
        cid = self.client_id.strip().lower()
        if cid.startswith("your_") or cid in ("your_client_id", "your_jamendo_client_id"):
            return False
        return len(self.client_id) >= 6

    def _normalize_track(self, raw: Dict[str, Any]) -> UnifiedTrack:
        """Convert Jamendo API raw track payload into UnifiedTrack."""
        track_id = f"jamendo_{raw.get('id')}"
        title = raw.get("name") or "Unknown Track"
        artist = raw.get("artist_name") or "Unknown Artist"
        album = raw.get("album_name")
        duration = raw.get("duration")  # integer seconds
        release_date = raw.get("releasedate")
        cover_url = raw.get("image") or raw.get("album_image")
        audio_url = raw.get("audio")  # Full MP3 stream/download URL
        license_ccurl = raw.get("license_ccurl") or "Creative Commons"
        shareurl = raw.get("shareurl") or f"https://www.jamendo.com/track/{raw.get('id')}"

        musicinfo = raw.get("musicinfo") or {}
        tags = musicinfo.get("tags") or {}
        genres = tags.get("genres") or []
        genre = genres[0] if genres else None

        return UnifiedTrack(
            id=track_id,
            provider_name=self.name,
            title=title,
            artist=artist,
            album=album,
            duration=int(duration) if duration else None,
            release_date=release_date,
            cover_url=cover_url,
            audio_url=audio_url,
            has_full_audio=bool(audio_url),
            license_type=license_ccurl,
            allow_redistribution=True,
            genre=genre,
            language=None,
            external_url=shareurl,
        )

    async def search(
        self, query: str, limit: int = 10, offset: int = 0
    ) -> List[UnifiedTrack]:
        """Search Jamendo tracks by name, artist, or tags."""
        if not self.is_configured():
            logger.debug("Jamendo provider is not configured. Skipping.")
            return []

        params = {
            "client_id": self.client_id,
            "format": "jsonpretty",
            "limit": min(limit, 20),
            "offset": offset,
            "namesearch": query,
            "include": "musicinfo",
            "audioformat": "mp32",
        }

        try:
            async with httpx.AsyncClient(timeout=config.search_timeout_seconds) as client:
                response = await client.get(JAMENDO_API_URL, params=params)
                if response.status_code != 200:
                    logger.warning(
                        "Jamendo search failed with HTTP %s: %s",
                        response.status_code,
                        response.text[:200],
                    )
                    return []

                data = response.json()
                headers = data.get("headers", {})
                if headers.get("status") == "failed":
                    logger.warning("Jamendo API returned failure: %s", headers.get("error_message"))
                    return []

                results = data.get("results", [])
                return [self._normalize_track(item) for item in results]

        except httpx.TimeoutException:
            logger.warning("Jamendo search timed out after %s seconds", config.search_timeout_seconds)
            return []
        except Exception as e:
            logger.error("Unexpected error in Jamendo search: %s", e)
            return []

    async def get_track(self, track_id: str) -> Optional[UnifiedTrack]:
        """Fetch single track from Jamendo by numeric ID."""
        if not self.is_configured():
            return None

        clean_id = track_id.replace("jamendo_", "").strip()
        params = {
            "client_id": self.client_id,
            "format": "jsonpretty",
            "id": clean_id,
            "include": "musicinfo",
            "audioformat": "mp32",
        }

        try:
            async with httpx.AsyncClient(timeout=config.search_timeout_seconds) as client:
                response = await client.get(JAMENDO_API_URL, params=params)
                if response.status_code != 200:
                    return None
                data = response.json()
                results = data.get("results", [])
                if results:
                    return self._normalize_track(results[0])
                return None
        except Exception as e:
            logger.error("Failed to retrieve Jamendo track %s: %s", track_id, e)
            return None

    async def get_top_tracks(self, limit: int = 10) -> List[UnifiedTrack]:
        """Fetch trending / popular tracks on Jamendo."""
        if not self.is_configured():
            return []

        params = {
            "client_id": self.client_id,
            "format": "jsonpretty",
            "limit": min(limit, 20),
            "order": "popularity_total",
            "include": "musicinfo",
            "audioformat": "mp32",
        }

        try:
            async with httpx.AsyncClient(timeout=config.search_timeout_seconds) as client:
                response = await client.get(JAMENDO_API_URL, params=params)
                if response.status_code == 200:
                    data = response.json()
                    results = data.get("results", [])
                    return [self._normalize_track(item) for item in results]
                return []
        except Exception as e:
            logger.error("Failed to fetch top Jamendo tracks: %s", e)
            return []
