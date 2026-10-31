"""
Live Integration Tests against real external public music APIs (iTunes, Internet Archive, MusicBrainz).
Verifies actual network communication, response parsing, and real search results.
"""

import asyncio
import pytest

from services.providers.internet_archive import InternetArchiveProvider
from services.providers.itunes import ITunesProvider
from services.providers.musicbrainz import MusicBrainzProvider
from services.search_engine import MusicSearchEngine


def test_live_itunes_search():
    """Live test against Apple iTunes public search API."""
    provider = ITunesProvider()
    assert provider.is_configured()

    # Search for Hindi song
    tracks = asyncio.run(provider.search("Tum Hi Ho", limit=3))
    assert len(tracks) > 0
    t = tracks[0]
    assert "tum hi ho" in t.title.lower() or "arijit" in t.artist.lower()
    assert t.provider_name == "iTunes"
    assert t.has_full_audio is False  # Authorized metadata only


def test_live_internet_archive_search():
    """Live test against Archive.org public domain audio search API."""
    provider = InternetArchiveProvider()
    assert provider.is_configured()

    tracks = asyncio.run(provider.search("classic audio", limit=3))
    assert len(tracks) > 0
    assert tracks[0].provider_name == "Internet Archive"


def test_live_musicbrainz_search():
    """Live test against MusicBrainz Open API."""
    provider = MusicBrainzProvider()
    assert provider.is_configured()

    # Retry once if public MusicBrainz API experiences network latency
    tracks = asyncio.run(provider.search("Shape of You", limit=2))
    if not tracks:
        import time
        time.sleep(1.0)
        tracks = asyncio.run(provider.search("Shape of You", limit=2))
    # If MusicBrainz server is reachable, assert results; otherwise skip gracefully
    if not tracks:
        pytest.skip("MusicBrainz public API rate-limited or experiencing network latency.")
    assert len(tracks) > 0
    assert tracks[0].provider_name == "MusicBrainz"


def test_live_search_engine_integration():
    """Live test of multi-provider search engine aggregating real results."""
    engine = MusicSearchEngine()
    results = asyncio.run(engine.search("Arijit Singh", max_total_results=5, use_cache=False))
    assert len(results) > 0
    # Must contain Arijit Singh in either title or artist
    matches = [t for t in results if "arijit" in t.artist.lower() or "arijit" in t.title.lower()]
    assert len(matches) > 0
