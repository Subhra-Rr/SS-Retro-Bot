"""
Unit tests for Provider Registry and individual Provider Adapters.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pytest

from services.providers.base import BaseMusicProvider, UnifiedTrack
from services.providers.internet_archive import InternetArchiveProvider
from services.providers.itunes import ITunesProvider
from services.providers.jamendo import JamendoProvider
from services.providers.massivemusic import MassiveMusicProvider
from services.providers.musicbrainz import MusicBrainzProvider
from services.providers.registry import ProviderRegistry
from services.providers.spotify import SpotifyProvider


def test_registry_registration_and_filtering():
    reg = ProviderRegistry()
    assert len(reg.get_all()) >= 5

    # Check configured vs audio
    configured = reg.get_configured()
    assert len(configured) >= 2  # iTunes, Archive.org, MusicBrainz always configured

    names = [p.name for p in reg.get_all()]
    assert "iTunes" in names
    assert "Internet Archive" in names
    assert "Jamendo" in names
    assert "Spotify" in names
    assert "MusicBrainz" in names


def test_unconfigured_providers():
    jamendo = JamendoProvider(client_id="")
    assert not jamendo.is_configured()

    spotify = SpotifyProvider(client_id="", client_secret="")
    assert not spotify.is_configured()

    massive = MassiveMusicProvider(api_key="")
    assert not massive.is_configured()


def test_itunes_search_normalization():
    itunes = ITunesProvider()
    mock_payload = {
        "resultCount": 1,
        "results": [
            {
                "trackId": 999123,
                "trackName": "Kesariya",
                "artistName": "Arijit Singh",
                "collectionName": "Brahmastra",
                "trackTimeMillis": 268000,
                "artworkUrl100": "https://example.com/100x100bb.jpg",
                "releaseDate": "2022-07-17T07:00:00Z",
                "primaryGenreName": "Bollywood",
                "trackViewUrl": "https://music.apple.com/track/999123",
            }
        ],
    }

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_payload

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        tracks = asyncio.run(itunes.search("Kesariya", limit=5))

    assert len(tracks) == 1
    t = tracks[0]
    assert t.id == "itunes_999123"
    assert t.title == "Kesariya"
    assert t.artist == "Arijit Singh"
    assert t.album == "Brahmastra"
    assert t.duration == 268
    assert "600x600bb.jpg" in t.cover_url
    assert t.has_full_audio is False  # Metadata only
    assert t.allow_redistribution is False


def test_internet_archive_normalization():
    archive = InternetArchiveProvider()
    mock_search_payload = {
        "response": {
            "docs": [
                {
                    "identifier": "odia_bhajan_sample",
                    "title": "Jagannatha Bhajan",
                    "creator": "Bhikari Bal",
                    "album": "Bhakti Sangeet",
                    "year": 1985,
                    "genre": "Devotional",
                }
            ]
        }
    }
    mock_files_payload = {
        "result": [
            {
                "name": "odia_bhajan_sample.mp3",
                "format": "VBR MP3",
                "size": "5242880",
                "length": "03:45",
            }
        ]
    }

    mock_search_resp = MagicMock(spec=httpx.Response)
    mock_search_resp.status_code = 200
    mock_search_resp.json.return_value = mock_search_payload

    mock_files_resp = MagicMock(spec=httpx.Response)
    mock_files_resp.status_code = 200
    mock_files_resp.json.return_value = mock_files_payload

    async def mock_get(url, *args, **kwargs):
        if "advancedsearch" in str(url):
            return mock_search_resp
        return mock_files_resp

    with patch("httpx.AsyncClient.get", side_effect=mock_get):
        tracks = asyncio.run(archive.search("Jagannatha", limit=5))

    assert len(tracks) == 1
    t = tracks[0]
    assert t.id == "archive_odia_bhajan_sample"
    assert t.title == "Jagannatha Bhajan"
    assert t.artist == "Bhikari Bal"
    assert t.has_full_audio is True
    assert t.allow_redistribution is True
    assert t.duration == 225
    assert "archive.org/download/" in t.audio_url


def test_jamendo_mock_search():
    jamendo = JamendoProvider(client_id="VALID_TEST_CLIENT_ID")
    assert jamendo.is_configured()

    mock_payload = {
        "headers": {"status": "success"},
        "results": [
            {
                "id": 12345,
                "name": "Electric Dreams",
                "artist_name": "Synthwave Project",
                "album_name": "Retro Neon",
                "duration": 210,
                "releasedate": "2023-01-01",
                "image": "https://example.com/art.jpg",
                "audio": "https://mp3d.jamendo.com/download/track/12345/mp32/",
                "license_ccurl": "https://creativecommons.org/licenses/by/4.0/",
                "shareurl": "https://www.jamendo.com/track/12345",
            }
        ],
    }

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_payload

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        tracks = asyncio.run(jamendo.search("synthwave", limit=5))

    assert len(tracks) == 1
    t = tracks[0]
    assert t.id == "jamendo_12345"
    assert t.title == "Electric Dreams"
    assert t.has_full_audio is True
    assert t.allow_redistribution is True
