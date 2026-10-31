"""
Spotify Web API Provider.
Provides metadata discovery for tracks, albums, artists, and playlists using OAuth 2.0 Client Credentials.
Does not supply unauthorized full audio files; acts as an authorized metadata discovery provider.
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Any, Dict, List, Optional

import httpx

from config import config
from services.providers.base import BaseMusicProvider, UnifiedTrack

logger = logging.getLogger(__name__)

TOKEN_URL = "https://accounts.spotify.com/api/token"
SEARCH_URL = "https://api.spotify.com/v1/search"
TRACKS_URL = "https://api.spotify.com/v1/tracks"


class SpotifyProvider(BaseMusicProvider):
    """Spotify Web API provider for metadata discovery."""

    name = "Spotify"
    can_deliver_full_audio = False

    def __init__(self, client_id: str | None = None, client_secret: str | None = None):
        self.client_id = config.spotify_client_id if client_id is None else client_id
        self.client_secret = config.spotify_client_secret if client_secret is None else client_secret
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0

    def is_configured(self) -> bool:
        """Check if valid Spotify API credentials are set."""
        if not self.client_id or not self.client_secret:
            return False
        if self.client_id.startswith("YOUR_") or self.client_secret.startswith("YOUR_"):
            return False
        return len(self.client_id) > 10 and len(self.client_secret) > 10

    async def _get_access_token(self, client: httpx.AsyncClient) -> Optional[str]:
        """Obtain or refresh bearer token using client credentials flow."""
        now = time.time()
        if self._access_token and now < (self._token_expires_at - 60):
            return self._access_token

        auth_str = f"{self.client_id}:{self.client_secret}"
        encoded_auth = base64.b64encode(auth_str.encode()).decode()

        headers = {
            "Authorization": f"Basic {encoded_auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = {"grant_type": "client_credentials"}

        try:
            response = await client.post(TOKEN_URL, headers=headers, data=data)
            if response.status_code != 200:
                logger.warning("Spotify token request failed with status %s", response.status_code)
                return None

            payload = response.json()
            self._access_token = payload.get("access_token")
            expires_in = payload.get("expires_in", 3600)
            self._token_expires_at = now + expires_in
            return self._access_token
        except Exception as e:
            logger.error("Spotify authentication exception: %s", e)
            return None

    def _normalize_track(self, item: Dict[str, Any]) -> UnifiedTrack:
        """Normalize Spotify track object into UnifiedTrack."""
        track_id = f"spotify_{item.get('id')}"
        title = item.get("name") or "Unknown Title"

        artists_list = item.get("artists", [])
        artist_names = [a.get("name") for a in artists_list if a.get("name")]
        artist = ", ".join(artist_names) if artist_names else "Unknown Artist"

        album_obj = item.get("album", {})
        album_name = album_obj.get("name")
        release_date = album_obj.get("release_date")

        images = album_obj.get("images", [])
        cover_url = images[0].get("url") if images else None

        duration_ms = item.get("duration_ms")
        duration_sec = int(duration_ms / 1000) if duration_ms else None

        external_urls = item.get("external_urls", {})
        spotify_url = external_urls.get("spotify")

        return UnifiedTrack(
            id=track_id,
            provider_name=self.name,
            title=title,
            artist=artist,
            album=album_name,
            duration=duration_sec,
            release_date=release_date,
            cover_url=cover_url,
            audio_url=None,  # No unauthorized full downloads
            has_full_audio=False,
            license_type="Commercial (Spotify Web)",
            allow_redistribution=False,
            genre=None,
            language=None,
            external_url=spotify_url,
        )

    async def search(
        self, query: str, limit: int = 10, offset: int = 0
    ) -> List[UnifiedTrack]:
        """Search Spotify for tracks."""
        if not self.is_configured():
            logger.debug("Spotify provider unconfigured. Skipping.")
            return []

        try:
            async with httpx.AsyncClient(timeout=config.search_timeout_seconds) as client:
                token = await self._get_access_token(client)
                if not token:
                    return []

                headers = {"Authorization": f"Bearer {token}"}
                params = {
                    "q": query,
                    "type": "track",
                    "limit": min(limit, 20),
                    "offset": offset,
                }

                response = await client.get(SEARCH_URL, headers=headers, params=params)
                if response.status_code != 200:
                    logger.warning("Spotify search HTTP %s", response.status_code)
                    return []

                items = response.json().get("tracks", {}).get("items", [])
                return [self._normalize_track(item) for item in items]

        except httpx.TimeoutException:
            logger.warning("Spotify search timed out")
            return []
        except Exception as e:
            logger.error("Spotify search error: %s", e)
            return []

    async def get_track(self, track_id: str) -> Optional[UnifiedTrack]:
        """Retrieve single Spotify track by ID."""
        if not self.is_configured():
            return None

        clean_id = track_id.replace("spotify_", "").strip()
        try:
            async with httpx.AsyncClient(timeout=config.search_timeout_seconds) as client:
                token = await self._get_access_token(client)
                if not token:
                    return None

                headers = {"Authorization": f"Bearer {token}"}
                response = await client.get(f"{TRACKS_URL}/{clean_id}", headers=headers)
                if response.status_code == 200:
                    return self._normalize_track(response.json())
                return None
        except Exception as e:
            logger.error("Failed to fetch Spotify track %s: %s", track_id, e)
            return None
