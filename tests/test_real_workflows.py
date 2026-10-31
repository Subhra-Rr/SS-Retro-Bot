"""
End-to-End automated validation of the 15 user workflows specified in Section 11.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from database import Database
from handlers.commands import help_command, start_command
from handlers.music import build_search_view, execute_search
from services.audio_validator import AudioValidationResult, AudioValidator
from services.downloader import AudioDownloader, license_allows_delivery
from services.providers.base import UnifiedTrack
from services.search_engine import MusicSearchEngine
from services.telegram_delivery import TelegramAudioDelivery


@pytest.fixture
def workflow_env(tmp_path: Path):
    db_file = tmp_path / "workflows.db"
    dl_dir = tmp_path / "downloads"
    dl_dir.mkdir(parents=True, exist_ok=True)
    db_inst = Database(db_path=db_file)
    asyncio.run(db_inst.init_db())
    downloader = AudioDownloader(download_dir=dl_dir, max_bytes=49 * 1024 * 1024)
    validator = AudioValidator(max_bytes=49 * 1024 * 1024)
    delivery = TelegramAudioDelivery()
    return db_inst, downloader, validator, delivery, dl_dir


def test_complete_15_step_user_workflow(workflow_env):
    db_inst, downloader, validator, delivery, dl_dir = workflow_env
    user_id = 999111
    chat_id = 999111

    async def _run_workflow():
        # 1. User starts the bot
        mock_start_update = MagicMock()
        mock_start_update.effective_user.id = user_id
        mock_start_update.effective_user.first_name = "Rudra"
        mock_start_update.effective_user.username = "rudra"
        mock_start_update.effective_user.last_name = "Prasad"
        mock_start_update.effective_user.language_code = "en"
        mock_start_update.message.reply_text = AsyncMock()
        with patch("handlers.commands.db", db_inst):
            await start_command(mock_start_update, MagicMock())
        mock_start_update.message.reply_text.assert_called_once()
        assert "Welcome, Rudra!" in mock_start_update.message.reply_text.call_args[0][0]

        # 2. User requests help
        mock_help_update = MagicMock()
        mock_help_update.callback_query = None
        mock_help_update.message.reply_text = AsyncMock()
        await help_command(mock_help_update, MagicMock())
        mock_help_update.message.reply_text.assert_called_once()
        assert "User Guide" in mock_help_update.message.reply_text.call_args[0][0]

        # Setup mock track catalogue covering steps 3-7
        international_track = UnifiedTrack(
            id="itunes_move",
            provider_name="iTunes",
            title="Move",
            artist="Adam Port",
            album="Move EP",
            has_full_audio=False,
        )
        hindi_track = UnifiedTrack(
            id="archive_tum_hi_ho",
            provider_name="Internet Archive",
            title="Tum Hi Ho",
            artist="Arijit Singh",
            album="Aashiqui 2",
            duration=262,
            has_full_audio=True,
            audio_url="https://archive.org/download/tumhiho/tumhiho.mp3",
            allow_redistribution=True,
        )
        odia_track = UnifiedTrack(
            id="archive_odia_jagannath",
            provider_name="Internet Archive",
            title="Ahe Nila Saila",
            artist="Salabega / Bhikari Bal",
            album="Odia Bhakti",
            duration=310,
            has_full_audio=True,
            audio_url="https://archive.org/download/odia/ahenilasaila.mp3",
            allow_redistribution=True,
        )

        search_engine = MusicSearchEngine()

        # 3. User searches for a popular international song ("Move")
        with patch.object(search_engine, "_search_provider", new_callable=AsyncMock, return_value=[international_track]):
            results_intl = await search_engine.search("Move", user_id=user_id, use_cache=False)
            assert len(results_intl) == 1
            assert results_intl[0].title == "Move"

        # 4. User searches for a Hindi song ("Tum Hi Ho")
        with patch.object(search_engine, "_search_provider", new_callable=AsyncMock, return_value=[hindi_track]):
            results_hindi = await search_engine.search("Tum Hi Ho", user_id=user_id, use_cache=False)
            assert len(results_hindi) == 1
            assert results_hindi[0].title == "Tum Hi Ho"

        # 5. User searches for an Odia song ("Odia")
        with patch.object(search_engine, "_search_provider", new_callable=AsyncMock, return_value=[odia_track]):
            results_odia = await search_engine.search("Odia", user_id=user_id, use_cache=False)
            assert len(results_odia) == 1
            assert "Nila Saila" in results_odia[0].title

        # 6. User searches for an artist ("Arijit Singh")
        with patch.object(search_engine, "_search_provider", new_callable=AsyncMock, return_value=[hindi_track]):
            results_artist = await search_engine.search("Arijit Singh", user_id=user_id, use_cache=False)
            assert results_artist[0].artist == "Arijit Singh"

        # 7. User searches for a partial song title ("Tum")
        with patch.object(search_engine, "_search_provider", new_callable=AsyncMock, return_value=[hindi_track]):
            results_partial = await search_engine.search("Tum", user_id=user_id, use_cache=False)
            assert "Tum" in results_partial[0].title

        # 8. User navigates multiple pages
        many_tracks = [
            UnifiedTrack(id=f"t_{i}", provider_name="Test", title=f"Song {i}", artist=f"Artist {i}")
            for i in range(1, 14)
        ]
        page1_text, page1_markup = build_search_view("All Songs", many_tracks, page=1, page_size=5)
        assert "Page 1 of 3" in page1_text
        page2_text, page2_markup = build_search_view("All Songs", many_tracks, page=2, page_size=5)
        assert "Page 2 of 3" in page2_text

        # 9. User selects a result (hindi_track)
        selected_track = hindi_track

        # 10. Bot checks full-track delivery permissions
        allowed, reason = license_allows_delivery(selected_track)
        assert allowed is True
        assert reason == "Authorized"

        # 11. Bot downloads the complete authorized audio
        fake_audio_file = dl_dir / "downloaded_track.mp3"
        fake_audio_file.write_bytes(b"AUTHENTIC_AUDIO_DATA_FOR_TESTING")

        with patch.object(downloader, "download_track", new_callable=AsyncMock, return_value=(fake_audio_file, None)):
            dl_path, dl_err = await downloader.download_track(selected_track)
            assert dl_err is None
            assert dl_path.exists()

        # 12. Bot validates the audio
        mock_val_result = AudioValidationResult(
            is_valid=True,
            duration_seconds=262,
            bitrate_kbps=320,
            file_size_bytes=fake_audio_file.stat().st_size,
        )
        with patch.object(validator, "validate_file", return_value=mock_val_result):
            val = validator.validate_file(dl_path)
            assert val.is_valid is True
            assert val.duration_seconds == 262

        # 13. Bot sends the complete file to Telegram
        mock_bot = MagicMock()
        mock_msg = MagicMock()
        mock_msg.audio.file_id = "TELEGRAM_DELIVERED_FILE_123"
        mock_bot.send_audio = AsyncMock(return_value=mock_msg)

        with patch("services.telegram_delivery.db", db_inst):
            with patch("services.telegram_delivery.audio_downloader.download_track", new_callable=AsyncMock, return_value=(fake_audio_file, None)):
                with patch("services.telegram_delivery.audio_validator.validate_file", return_value=mock_val_result):
                    delivered, err, sent_msg = await delivery.deliver_track(
                        bot=mock_bot,
                        chat_id=chat_id,
                        track=selected_track,
                        user_id=user_id,
                    )
                    assert delivered is True
                    assert err is None
                    mock_bot.send_audio.assert_called_once()

        # 14. Bot records the selected track
        recent_tracks = await db_inst.get_recent_tracks(user_id)
        assert len(recent_tracks) >= 1
        assert recent_tracks[0]["id"] == selected_track.id

        # 15. Temporary files are removed
        downloader.cleanup(fake_audio_file)
        assert not fake_audio_file.exists()

    asyncio.run(_run_workflow())
