"""
Unit tests for Audio Downloader and license verification.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pytest

from services.downloader import AudioDownloader, license_allows_delivery
from services.providers.base import UnifiedTrack


def test_license_verification():
    # Metadata-only track
    track_meta = UnifiedTrack(
        id="itunes_1",
        provider_name="iTunes",
        title="Song",
        artist="Artist",
        has_full_audio=False,
        audio_url=None,
    )
    allowed, reason = license_allows_delivery(track_meta)
    assert not allowed
    assert "metadata" in reason.lower()

    # Track with audio but redistribution disallowed
    track_nodist = UnifiedTrack(
        id="t_2",
        provider_name="Custom",
        title="Song",
        artist="Artist",
        has_full_audio=True,
        audio_url="https://example.com/audio.mp3",
        allow_redistribution=False,
    )
    allowed, reason = license_allows_delivery(track_nodist)
    assert not allowed
    assert "redistribution" in reason.lower()

    # Fully authorized track
    track_auth = UnifiedTrack(
        id="jamendo_1",
        provider_name="Jamendo",
        title="Song",
        artist="Artist",
        has_full_audio=True,
        audio_url="https://example.com/audio.mp3",
        allow_redistribution=True,
    )
    allowed, reason = license_allows_delivery(track_auth)
    assert allowed
    assert reason == "Authorized"


def test_audio_downloader_stream_and_cleanup(tmp_path: Path):
    downloader = AudioDownloader(download_dir=tmp_path, max_bytes=1024 * 1024)
    track = UnifiedTrack(
        id="archive_sample",
        provider_name="Internet Archive",
        title="Classical Piece",
        artist="Master",
        has_full_audio=True,
        audio_url="https://archive.org/download/item/piece.mp3",
        allow_redistribution=True,
    )

    # Mock chunk generator
    async def mock_aiter_bytes(*args, **kwargs):
        yield b"ID3v2_AUDIO_STREAM_CHUNK_1"
        yield b"_CHUNK_2"

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"Content-Length": "35"}
    mock_resp.aiter_bytes = mock_aiter_bytes
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=None)

    mock_client = MagicMock()
    mock_client.stream.return_value = mock_resp
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        with patch.object(downloader, "_tag_mp3"):
            file_path, err = asyncio.run(downloader.download_track(track))

    assert err is None
    assert file_path is not None
    assert file_path.exists()
    assert file_path.stat().st_size > 0

    # Test cleanup
    downloader.cleanup(file_path)
    assert not file_path.exists()
