"""
Internet Archive (archive.org) Music Provider.
Searches public domain, Creative Commons, live, vintage, and regional audio recordings.
Delivers complete authorized MP3 files.
"""

from __future__ import annotations

import asyncio
import logging
import urllib.parse
from typing import Any, Dict, List, Optional

import httpx

from config import config
from services.providers.base import BaseMusicProvider, UnifiedTrack

logger = logging.getLogger(__name__)

SEARCH_API_URL = "https://archive.org/advancedsearch.php"
METADATA_API_URL = "https://archive.org/metadata"


class InternetArchiveProvider(BaseMusicProvider):
    """Internet Archive provider delivering public domain and open access audio."""

    name = "Internet Archive"
    can_deliver_full_audio = True

    def is_configured(self) -> bool:
        """Internet Archive public API requires no credentials."""
        return True

    async def _resolve_primary_mp3(
        self, client: httpx.AsyncClient, identifier: str
    ) -> tuple[Optional[str], Optional[int], Optional[int]]:
        """
        Query files metadata to locate the primary playable MP3 file,
        its file size in bytes, and duration in seconds.
        """
        try:
            url = f"{METADATA_API_URL}/{identifier}/files"
            response = await client.get(url, timeout=4.0)
            if response.status_code != 200:
                return None, None, None

            files = response.json().get("result", [])
            # Prioritize standard VBR or 128kbps MP3
            candidate = None
            for f in files:
                name = f.get("name", "")
                if name.lower().endswith(".mp3"):
                    candidate = f
                    # If it's VBR MP3, prefer it
                    if "vbr" in f.get("format", "").lower():
                        break

            if not candidate:
                return None, None, None

            filename = candidate.get("name", "")
            size = candidate.get("size")
            size_int = int(size) if size and str(size).isdigit() else None

            # Parse length (seconds or mm:ss)
            length = candidate.get("length")
            duration_sec = None
            if length:
                try:
                    if ":" in str(length):
                        parts = [float(p) for p in str(length).split(":")]
                        if len(parts) == 2:
                            duration_sec = int(parts[0] * 60 + parts[1])
                        elif len(parts) == 3:
                            duration_sec = int(parts[0] * 3600 + parts[1] * 60 + parts[2])
                    else:
                        duration_sec = int(float(length))
                except Exception:
                    duration_sec = None

            encoded_filename = urllib.parse.quote(filename)
            audio_url = f"https://archive.org/download/{identifier}/{encoded_filename}"
            return audio_url, size_int, duration_sec

        except Exception as e:
            logger.debug("Could not resolve files for %s: %s", identifier, e)
            return None, None, None

    async def search(
        self, query: str, limit: int = 10, offset: int = 0
    ) -> List[UnifiedTrack]:
        """Search Internet Archive for audio recordings."""
        clean_query = query.replace('"', "").replace("'", "").strip()
        search_filter = f'mediatype:(audio) AND format:(MP3) AND ({clean_query})'

        params = {
            "q": search_filter,
            "fl[]": ["identifier", "title", "creator", "album", "year", "genre", "language"],
            "rows": min(limit, 10),
            "page": (offset // limit) + 1 if limit > 0 else 1,
            "output": "json",
        }

        try:
            async with httpx.AsyncClient(timeout=config.search_timeout_seconds) as client:
                response = await client.get(SEARCH_API_URL, params=params)
                if response.status_code != 200:
                    return []

                data = response.json()
                docs = data.get("response", {}).get("docs", [])
                if not docs:
                    return []

                # Concurrently resolve audio files for all retrieved items
                resolve_tasks = [
                    self._resolve_primary_mp3(client, doc.get("identifier", ""))
                    for doc in docs
                    if doc.get("identifier")
                ]
                resolved_files = await asyncio.gather(*resolve_tasks, return_exceptions=True)

                tracks: List[UnifiedTrack] = []
                for doc, res in zip(docs, resolved_files):
                    identifier = doc.get("identifier")
                    if not identifier:
                        continue

                    title = doc.get("title") or identifier
                    if isinstance(title, list):
                        title = title[0]

                    creator = doc.get("creator") or "Unknown Artist"
                    if isinstance(creator, list):
                        creator = ", ".join(creator)

                    album = doc.get("album")
                    if isinstance(album, list):
                        album = album[0]

                    year = doc.get("year")
                    cover_url = f"https://archive.org/services/img/{identifier}"
                    external_url = f"https://archive.org/details/{identifier}"

                    if isinstance(res, tuple) and len(res) == 3:
                        audio_url, size_bytes, duration = res
                    else:
                        audio_url, size_bytes, duration = None, None, None

                    tracks.append(
                        UnifiedTrack(
                            id=f"archive_{identifier}",
                            provider_name=self.name,
                            title=str(title),
                            artist=str(creator),
                            album=str(album) if album else None,
                            duration=duration,
                            release_date=str(year) if year else None,
                            cover_url=cover_url,
                            audio_url=audio_url,
                            has_full_audio=bool(audio_url),
                            license_type="Public Domain / CC Open Audio",
                            allow_redistribution=True,
                            genre=doc.get("genre") if isinstance(doc.get("genre"), str) else None,
                            language=doc.get("language") if isinstance(doc.get("language"), str) else None,
                            external_url=external_url,
                            file_size=size_bytes,
                        )
                    )

                return tracks

        except httpx.TimeoutException:
            logger.warning("Internet Archive search timed out")
            return []
        except Exception as e:
            logger.error("Error in Internet Archive search: %s", e)
            return []

    async def get_track(self, track_id: str) -> Optional[UnifiedTrack]:
        """Retrieve single track from Internet Archive."""
        identifier = track_id.replace("archive_", "").strip()
        try:
            async with httpx.AsyncClient(timeout=config.search_timeout_seconds) as client:
                url = f"{METADATA_API_URL}/{identifier}"
                response = await client.get(url)
                if response.status_code != 200:
                    return None

                data = response.json()
                meta = data.get("metadata", {})
                title = meta.get("title", identifier)
                creator = meta.get("creator", "Unknown Artist")
                if isinstance(creator, list):
                    creator = ", ".join(creator)

                audio_url, size_bytes, duration = await self._resolve_primary_mp3(
                    client, identifier
                )

                return UnifiedTrack(
                    id=f"archive_{identifier}",
                    provider_name=self.name,
                    title=str(title),
                    artist=str(creator),
                    album=meta.get("album"),
                    duration=duration,
                    release_date=meta.get("year"),
                    cover_url=f"https://archive.org/services/img/{identifier}",
                    audio_url=audio_url,
                    has_full_audio=bool(audio_url),
                    license_type="Public Domain / CC Open Audio",
                    allow_redistribution=True,
                    external_url=f"https://archive.org/details/{identifier}",
                    file_size=size_bytes,
                )
        except Exception as e:
            logger.error("Failed to retrieve Internet Archive track %s: %s", track_id, e)
            return None

    async def get_top_tracks(self, limit: int = 10) -> List[UnifiedTrack]:
        """Fetch popular / historical recordings."""
        return await self.search("classic audio", limit=limit)
