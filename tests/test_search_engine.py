"""
Unit tests for the Multi-Provider Music Search Engine.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest

from services.providers.base import BaseMusicProvider, UnifiedTrack
from services.providers.registry import ProviderRegistry
from services.search_engine import (
    MusicSearchEngine,
    _calculate_relevance,
    _normalize_string,
    deduplicate_tracks,
)


class MockProvider(BaseMusicProvider):
    def __init__(self, name: str, tracks: list[UnifiedTrack], should_timeout: bool = False):
        self.name = name
        self._tracks = tracks
        self.should_timeout = should_timeout

    def is_configured(self) -> bool:
        return True

    async def search(self, query: str, limit: int = 10, offset: int = 0):
        if self.should_timeout:
            await asyncio.sleep(5.0)
        return self._tracks

    async def get_track(self, track_id: str):
        for t in self._tracks:
            if t.id == track_id:
                return t
        return None


def test_string_normalization():
    assert _normalize_string("Tum Hi Ho (Aashiqui 2)") == "tum hi ho"
    assert _normalize_string("Song Title [Remix]") == "song title"
    assert _normalize_string("Artist ft. Someone") == "artist"


def test_deduplication():
    t1 = UnifiedTrack(
        id="itunes_1",
        provider_name="iTunes",
        title="Tum Hi Ho",
        artist="Arijit Singh",
        has_full_audio=False,
    )
    t2 = UnifiedTrack(
        id="archive_1",
        provider_name="Internet Archive",
        title="Tum Hi Ho",
        artist="Arijit Singh",
        has_full_audio=True,  # Has full audio!
    )
    t3 = UnifiedTrack(
        id="itunes_2",
        provider_name="iTunes",
        title="Different Song",
        artist="Other Artist",
        has_full_audio=False,
    )

    deduped = deduplicate_tracks([t1, t2, t3])
    assert len(deduped) == 2
    # Ensure t2 (with full audio) replaced t1
    found_tum = next(t for t in deduped if "tum hi ho" in t.title.lower())
    assert found_tum.id == "archive_1"
    assert found_tum.has_full_audio is True


def test_pagination():
    items = list(range(1, 24))  # 23 items
    page_size = 5

    # Page 1
    p1, total_pages, cur = MusicSearchEngine.paginate(items, page=1, page_size=page_size)
    assert p1 == [1, 2, 3, 4, 5]
    assert total_pages == 5
    assert cur == 1

    # Page 5 (last page)
    p5, total_pages, cur = MusicSearchEngine.paginate(items, page=5, page_size=page_size)
    assert p5 == [21, 22, 23]
    assert cur == 5

    # Clamping beyond total pages
    p_clamped, _, cur = MusicSearchEngine.paginate(items, page=99, page_size=page_size)
    assert cur == 5
    assert p_clamped == [21, 22, 23]


def test_concurrent_search_with_timeout_shield():
    t1 = UnifiedTrack(id="p1_t1", provider_name="P1", title="Hit Track", artist="Artist 1")
    p1 = MockProvider("FastProvider", [t1])
    p2 = MockProvider("SlowProvider", [], should_timeout=True)

    reg = ProviderRegistry()
    reg._providers = {"fast": p1, "slow": p2}

    engine = MusicSearchEngine(provider_registry=reg)

    # Search should finish without raising TimeoutError and return FastProvider's results
    results = asyncio.run(engine.search("Hit Track", use_cache=False))
    assert len(results) == 1
    assert results[0].id == "p1_t1"
