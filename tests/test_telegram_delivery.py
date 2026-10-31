"""
Unit tests for Telegram audio delivery and file_id caching.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from database import Database
from services.audio_validator import AudioValidationResult
from services.providers.base import UnifiedTrack
from services.telegram_delivery import TelegramAudioDelivery


@pytest.fixture
def delivery_env(tmp_path: Path):
    db_file = tmp_path / "test_delivery.db"
    db_inst = Database(db_path=db_file)
    asyncio.run(db_inst.init_db())
    return db_inst, TelegramAudioDelivery()


def test_delivery_via_cached_file_id(delivery_env):
    db_inst, delivery = delivery_env

    track = UnifiedTrack(
        id="jamendo_999",
        provider_name="Jamendo",
        title="Cache Hit Song",
        artist="Test Artist",
        has_full_audio=True,
        audio_url="https://example.com/audio.mp3",
        allow_redistribution=True,
    )

    async def _run():
        # Pre-seed cache
        await db_inst.cache_telegram_file_id(
            track_id="jamendo_999",
            provider_name="Jamendo",
            telegram_file_id="TELEGRAM_CACHED_FILE_ID_12345",
        )

        mock_bot = MagicMock()
        mock_msg = MagicMock()
        mock_bot.send_audio = AsyncMock(return_value=mock_msg)

        with patch("services.telegram_delivery.db", db_inst):
            with patch("services.telegram_delivery.audio_downloader") as mock_dl:
                success, err, msg = await delivery.deliver_track(
                    bot=mock_bot,
                    chat_id=12345,
                    track=track,
                    user_id=888,
                )

                assert success is True
                assert err is None
                # Downloader should NOT have been called due to cache hit
                mock_dl.download_track.assert_not_called()
                mock_bot.send_audio.assert_called_once()
                args, kwargs = mock_bot.send_audio.call_args
                assert kwargs["audio"] == "TELEGRAM_CACHED_FILE_ID_12345"

    asyncio.run(_run())


def test_delivery_new_download_success(delivery_env, tmp_path: Path):
    db_inst, delivery = delivery_env

    track = UnifiedTrack(
        id="archive_new",
        provider_name="Internet Archive",
        title="New Track",
        artist="Artist",
        has_full_audio=True,
        audio_url="https://example.com/audio.mp3",
        allow_redistribution=True,
    )

    fake_file = tmp_path / "new_track.mp3"
    fake_file.write_bytes(b"dummy_audio_bytes")

    async def _run():
        mock_bot = MagicMock()
        mock_msg = MagicMock()
        mock_msg.audio.file_id = "NEW_TELEGRAM_FILE_ID_777"
        mock_bot.send_audio = AsyncMock(return_value=mock_msg)

        val_result = AudioValidationResult(
            is_valid=True,
            duration_seconds=120,
            file_size_bytes=len(fake_file.read_bytes()),
        )

        with patch("services.telegram_delivery.db", db_inst):
            with patch(
                "services.telegram_delivery.audio_downloader.download_track",
                new_callable=AsyncMock,
                return_value=(fake_file, None),
            ):
                with patch(
                    "services.telegram_delivery.audio_validator.validate_file",
                    return_value=val_result,
                ):
                    with patch("services.telegram_delivery.audio_downloader.cleanup") as mock_cleanup:
                        success, err, msg = await delivery.deliver_track(
                            bot=mock_bot,
                            chat_id=12345,
                            track=track,
                            user_id=888,
                        )

                        assert success is True
                        assert err is None
                        mock_bot.send_audio.assert_called_once()
                        mock_cleanup.assert_called_once_with(fake_file)

                        # Verify cached in database
                        cached_id = await db_inst.get_cached_telegram_file_id("archive_new", "Internet Archive")
                        assert cached_id == "NEW_TELEGRAM_FILE_ID_777"

    asyncio.run(_run())
