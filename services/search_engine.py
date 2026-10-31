"""
Concurrent Multi-Provider Music Search Engine.
Handles parallel provider queries, result aggregation, deduplication, relevance scoring,
caching, and pagination.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from config import config
from database import db
from services.providers.base import UnifiedTrack
from services.providers.registry import ProviderRegistry, registry

logger = logging.getLogger(__name__)


def _normalize_string(text: str | None) -> str:
    """Normalize string for fuzzy duplicate detection."""
    if not text:
        return ""
    # Lowercase, remove brackets, parenthesis, ft., feat.
    cleaned = re.sub(r"\([^)]*\)|\[[^\]]*\]", "", text.lower())
    cleaned = re.sub(r"\b(feat|ft|featuring|remix|version)\b.*", "", cleaned)
    cleaned = re.sub(r"[^\w\s]", "", cleaned)
    return " ".join(cleaned.split())


def _calculate_relevance(track: UnifiedTrack, query_tokens: List[str], full_query: str) -> float:
    """
    Calculate a numerical relevance score for ranking.
    Higher score indicates higher relevance to the search query.
    """
    score = 0.0
    title_lower = (track.title or "").lower()
    artist_lower = (track.artist or "").lower()
    album_lower = (track.album or "").lower()

    # Exact full match bonuses
    if full_query in title_lower:
        score += 60.0
    if full_query in artist_lower:
        score += 40.0

    # Token match scoring
    for token in query_tokens:
        if token in title_lower:
            score += 20.0
        if token in artist_lower:
            score += 15.0
        if token in album_lower:
            score += 8.0

    # Boost authorized full audio tracks so users have direct access to music
    if track.has_full_audio:
        score += 10.0

    return score


def deduplicate_tracks(tracks: List[UnifiedTrack]) -> List[UnifiedTrack]:
    """
    Deduplicate tracks based on normalized title, artist, and duration similarity.
    Preserves highest quality/authorized audio entry when duplicates exist.
    """
    seen: Dict[Tuple[str, str], UnifiedTrack] = {}

    for track in tracks:
        norm_title = _normalize_string(track.title)
        norm_artist = _normalize_string(track.artist)
        key = (norm_title, norm_artist)

        if not norm_title:
            continue

        if key not in seen:
            seen[key] = track
        else:
            existing = seen[key]
            # Replace existing if current has full audio and existing does not
            if track.has_full_audio and not existing.has_full_audio:
                seen[key] = track

    return list(seen.values())


class MusicSearchEngine:
    """Coordinates search across multiple active music providers."""

    def __init__(self, provider_registry: ProviderRegistry | None = None):
        self.registry = provider_registry or registry

    async def _search_provider(
        self, provider, query: str, limit: int
    ) -> List[UnifiedTrack]:
        """Query a single provider safely with timeout and error suppression."""
        try:
            results = await asyncio.wait_for(
                provider.search(query, limit=limit),
                timeout=config.search_timeout_seconds,
            )
            return results or []
        except asyncio.TimeoutError:
            logger.warning("Provider [%s] timed out on query '%s'", provider.name, query)
            return []
        except Exception as e:
            logger.error("Provider [%s] failed on query '%s': %s", provider.name, query, e)
            return []

    async def search(
        self,
        query: str,
        user_id: int | None = None,
        use_cache: bool = True,
        max_total_results: int = 40,
    ) -> List[UnifiedTrack]:
        """
        Execute concurrent multi-provider search.
        Normalizes, deduplicates, and ranks all aggregated results.
        """
        clean_query = query.strip()
        if not clean_query:
            return []

        # Check search cache
        cache_key = hashlib.md5(f"search:{clean_query.lower()}".encode()).hexdigest()
        if use_cache:
            cached_data = await db.get_cached_search(cache_key)
            if cached_data:
                logger.debug("Cache hit for query '%s'", clean_query)
                return [UnifiedTrack.from_dict(d) for d in cached_data]

        # Record search in user history and analytics if user_id is provided
        if user_id:
            await db.add_search_history(user_id, clean_query)
            await db.record_event("search", user_id, clean_query)

        # Get active configured providers
        configured_providers = self.registry.get_configured()
        if not configured_providers:
            logger.warning("No providers are currently configured.")
            return []

        # Execute searches concurrently
        per_provider_limit = max(5, max_total_results // max(len(configured_providers), 1))
        tasks = [
            self._search_provider(provider, clean_query, per_provider_limit)
            for provider in configured_providers
        ]
        provider_results = await asyncio.gather(*tasks, return_exceptions=True)

        aggregated: List[UnifiedTrack] = []
        for res in provider_results:
            if isinstance(res, list):
                aggregated.extend(res)

        # Deduplicate
        deduped = deduplicate_tracks(aggregated)

        # Relevance ranking
        query_tokens = [t.lower() for t in clean_query.split() if len(t) > 1]
        ranked = sorted(
            deduped,
            key=lambda t: _calculate_relevance(t, query_tokens, clean_query.lower()),
            reverse=True,
        )

        final_results = ranked[:max_total_results]

        # Save to database cache
        if use_cache and final_results:
            await db.save_cached_search(
                cache_key,
                [t.to_dict() for t in final_results],
                ttl_seconds=config.cache_ttl_hours * 3600,
            )

        return final_results

    async def get_track_by_id(self, track_id: str) -> Optional[UnifiedTrack]:
        """Fetch track details by checking providers."""
        # Check providers matching prefix
        if track_id.startswith("yt_"):
            p = self.registry.get_provider("YouTube Music")
            if p:
                return await p.get_track(track_id)
        elif track_id.startswith("jamendo_"):
            p = self.registry.get_provider("Jamendo")
            if p:
                return await p.get_track(track_id)
        elif track_id.startswith("archive_"):
            p = self.registry.get_provider("Internet Archive")
            if p:
                return await p.get_track(track_id)
        elif track_id.startswith("itunes_"):
            p = self.registry.get_provider("iTunes")
            if p:
                return await p.get_track(track_id)
        elif track_id.startswith("spotify_"):
            p = self.registry.get_provider("Spotify")
            if p:
                return await p.get_track(track_id)
        elif track_id.startswith("mb_"):
            p = self.registry.get_provider("MusicBrainz")
            if p:
                return await p.get_track(track_id)
        elif track_id.startswith("7digital_"):
            p = self.registry.get_provider("MassiveMusic / 7digital")
            if p:
                return await p.get_track(track_id)

        # Fallback: check all providers
        for p in self.registry.get_configured():
            try:
                track = await p.get_track(track_id)
                if track:
                    return track
            except Exception:
                continue
        return None

    @staticmethod
    def paginate(
        items: List[Any], page: int, page_size: int = 5
    ) -> Tuple[List[Any], int, int]:
        """
        Slice list into page chunks.
        Returns: (page_items, total_pages, current_page_clamped)
        """
        if not items:
            return [], 1, 1

        total_pages = max(1, (len(items) + page_size - 1) // page_size)
        clamped_page = max(1, min(page, total_pages))
        start = (clamped_page - 1) * page_size
        end = start + page_size
        return items[start:end], total_pages, clamped_page


# Global search engine instance
search_engine = MusicSearchEngine()
