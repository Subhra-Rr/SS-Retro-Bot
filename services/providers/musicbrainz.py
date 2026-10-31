"""
MusicBrainz Open Music Encyclopedia Provider.
Provides authoritative metadata, ISRC codes, releases, artist disambiguation,
and encyclopedic discovery.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from config import config
from services.providers.base import BaseMusicProvider, UnifiedTrack

logger = logging.getLogger(__name__)

MUSICBRAINZ_API_URL = "https://musicbrainz.org/ws/2/recording/"


class MusicBrainzProvider(BaseMusicProvider):
    """MusicBrainz open music catalog provider for metadata discovery."""

    name = "MusicBrainz"
    can_deliver_full_audio = False

    def is_configured(self) -> bool:
        """MusicBrainz requires a valid User-Agent header."""
        return bool(config.musicbrainz_app_name)

    def _get_headers(self) -> Dict[str, str]:
        return {
            "User-Agent": f"{config.musicbrainz_app_name} ({config.musicbrainz_contact})",
            "Accept": "application/json",
        }

    def _normalize_recording(self, rec: Dict[str, Any]) -> UnifiedTrack:
        """Normalize MusicBrainz recording object."""
        rec_id = f"mb_{rec.get('id')}"
        title = rec.get("title") or "Unknown Title"

        # Extract artists
        artist_credits = rec.get("artist-credit", [])
        artists = [a.get("name") for a in artist_credits if isinstance(a, dict) and a.get("name")]
        artist_name = ", ".join(artists) if artists else "Unknown Artist"

        # Extract primary release
        releases = rec.get("releases", [])
        album_name = releases[0].get("title") if releases else None
        release_date = releases[0].get("date") if releases else None

        # Length in ms
        length_ms = rec.get("length")
        duration_sec = int(length_ms / 1000) if length_ms else None

        # Tags / genres
        tags = rec.get("tags", [])
        genre = tags[0].get("name") if tags else None

        external_url = f"https://musicbrainz.org/recording/{rec.get('id')}"

        return UnifiedTrack(
            id=rec_id,
            provider_name=self.name,
            title=title,
            artist=artist_name,
            album=album_name,
            duration=duration_sec,
            release_date=release_date,
            cover_url=None,
            audio_url=None,
            has_full_audio=False,
            license_type="Open Data (CC0 / CC-BY-NC-SA)",
            allow_redistribution=False,
            genre=genre,
            language=None,
            external_url=external_url,
        )

    async def search(
        self, query: str, limit: int = 10, offset: int = 0
    ) -> List[UnifiedTrack]:
        """Search MusicBrainz recordings."""
        clean_query = query.replace('"', "").replace("'", "")
        mb_query = f"recording:{clean_query} OR artist:{clean_query}"
        params = {
            "query": mb_query,
            "fmt": "json",
            "limit": min(limit, 15),
            "offset": offset,
        }

        try:
            async with httpx.AsyncClient(
                timeout=config.search_timeout_seconds, headers=self._get_headers()
            ) as client:
                response = await client.get(MUSICBRAINZ_API_URL, params=params)
                if response.status_code != 200:
                    return []

                data = response.json()
                recordings = data.get("recordings", [])
                return [self._normalize_recording(r) for r in recordings]

        except httpx.TimeoutException:
            logger.warning("MusicBrainz search timed out")
            return []
        except Exception as e:
            logger.error("Error in MusicBrainz search: %s", e)
            return []

    async def get_track(self, track_id: str) -> Optional[UnifiedTrack]:
        """Fetch recording details by MusicBrainz UUID."""
        uuid = track_id.replace("mb_", "").strip()
        url = f"{MUSICBRAINZ_API_URL}{uuid}?fmt=json&inc=artists+releases+tags"
        try:
            async with httpx.AsyncClient(
                timeout=config.search_timeout_seconds, headers=self._get_headers()
            ) as client:
                response = await client.get(url)
                if response.status_code == 200:
                    return self._normalize_recording(response.json())
                return None
        except Exception as e:
            logger.error("Failed to fetch MusicBrainz track %s: %s", track_id, e)
            return None
