"""
MassiveMusic / 7digital B2B Commercial API Provider.
Implements the 7digital Open Music Platform B2B catalog adapter.
Commercial licensing and agreement details are documented below.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from config import config
from services.providers.base import BaseMusicProvider, UnifiedTrack

logger = logging.getLogger(__name__)

SEVENDIGITAL_API_BASE = "https://api.7digital.com/1.2"


class MassiveMusicProvider(BaseMusicProvider):
    """
    MassiveMusic / 7digital B2B provider adapter.

    Commercial Access Application Process:
    --------------------------------------
    1. MassiveMusic / 7digital is an enterprise B2B licensed digital music provider.
    2. To obtain an OAuth consumer key and secret, register an enterprise developer
       account at https://developer.7digital.com/ or contact 7digital sales for
       a commercial music delivery contract.
    3. Once licensed, enter MASSIVEMUSIC_API_KEY and MASSIVEMUSIC_API_SECRET in your .env file.
    4. Unconfigured adapters remain safely disabled without affecting other providers.
    """

    name = "MassiveMusic / 7digital"
    can_deliver_full_audio = False  # B2B encrypted or licensed stream depending on commercial tier

    def __init__(self, api_key: str | None = None, api_secret: str | None = None):
        self.api_key = config.massivemusic_api_key if api_key is None else api_key
        self.api_secret = config.massivemusic_api_secret if api_secret is None else api_secret

    def is_configured(self) -> bool:
        """Check if 7digital / MassiveMusic consumer credentials are provided."""
        if not self.api_key:
            return False
        if self.api_key.startswith("YOUR_") or "api_key" in self.api_key.lower():
            return False
        return len(self.api_key) > 8

    def _normalize_track(self, item: Dict[str, Any]) -> UnifiedTrack:
        """Normalize 7digital track XML/JSON response."""
        track_id = f"7digital_{item.get('id')}"
        title = item.get("title") or "Unknown Title"

        artist_obj = item.get("artist") or {}
        artist = artist_obj.get("name") or "Unknown Artist"

        release_obj = item.get("release") or {}
        album = release_obj.get("title")
        cover_url = release_obj.get("image")

        duration_sec = item.get("duration")
        url = item.get("url")

        return UnifiedTrack(
            id=track_id,
            provider_name=self.name,
            title=title,
            artist=artist,
            album=album,
            duration=int(duration_sec) if duration_sec else None,
            release_date=None,
            cover_url=cover_url,
            audio_url=None,
            has_full_audio=False,
            license_type="Commercial Enterprise B2B",
            allow_redistribution=False,
            genre=None,
            language=None,
            external_url=url,
        )

    async def search(
        self, query: str, limit: int = 10, offset: int = 0
    ) -> List[UnifiedTrack]:
        """Search 7digital track catalogue."""
        if not self.is_configured():
            logger.debug("MassiveMusic/7digital is unconfigured. Skipping.")
            return []

        params = {
            "oauth_consumer_key": self.api_key,
            "q": query,
            "pageSize": min(limit, 20),
            "page": (offset // limit) + 1 if limit > 0 else 1,
        }
        headers = {"Accept": "application/json"}

        try:
            async with httpx.AsyncClient(timeout=config.search_timeout_seconds) as client:
                response = await client.get(
                    f"{SEVENDIGITAL_API_BASE}/track/search", params=params, headers=headers
                )
                if response.status_code != 200:
                    return []

                data = response.json()
                results = data.get("searchResults", {}).get("searchResult", [])
                tracks = []
                for res in results:
                    track = res.get("track")
                    if track:
                        tracks.append(self._normalize_track(track))
                return tracks

        except httpx.TimeoutException:
            logger.warning("MassiveMusic search timed out")
            return []
        except Exception as e:
            logger.error("Error in MassiveMusic search: %s", e)
            return []

    async def get_track(self, track_id: str) -> Optional[UnifiedTrack]:
        """Fetch 7digital track by ID."""
        if not self.is_configured():
            return None

        clean_id = track_id.replace("7digital_", "").strip()
        params = {"oauth_consumer_key": self.api_key, "trackId": clean_id}
        headers = {"Accept": "application/json"}

        try:
            async with httpx.AsyncClient(timeout=config.search_timeout_seconds) as client:
                response = await client.get(
                    f"{SEVENDIGITAL_API_BASE}/track/details", params=params, headers=headers
                )
                if response.status_code == 200:
                    return self._normalize_track(response.json().get("track", {}))
                return None
        except Exception as e:
            logger.error("Failed to fetch MassiveMusic track %s: %s", track_id, e)
            return None
