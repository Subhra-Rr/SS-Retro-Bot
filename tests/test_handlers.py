"""
Unit tests for Telegram command and callback handlers.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from database import Database
from handlers.commands import help_command, settings_command, start_command
from utils.formatting import escape_html, format_duration, format_file_size, format_track_summary
from utils.security import UserRateLimiter, sanitize_filename, sanitize_query


def test_formatting_utilities():
    assert format_duration(65) == "01:05"
    assert format_duration(3665) == "1:01:05"
    assert format_duration(0) == "00:00"
    assert format_duration(None) == "00:00"

    assert format_file_size(1024 * 1024 * 5) == "5.0 MB"
    assert format_file_size(1024 * 250) == "250.0 KB"

    assert escape_html("Rock & Roll <Live>") == "Rock &amp; Roll &lt;Live&gt;"

    track_dict = {
        "title": "Song Title",
        "artist": "Artist Name",
        "duration": 180,
        "provider_name": "Jamendo",
        "has_full_audio": True,
    }
    summary = format_track_summary(track_dict, index=1)
    assert "<b>1.</b>" in summary
    assert "Song Title" in summary
    assert "03:00" in summary
    assert "Jamendo" in summary


def test_security_utilities():
    # Filename sanitization
    assert sanitize_filename("Arijit Singh / Tum Hi Ho?.mp3") == "Arijit Singh  Tum Hi Ho.mp3"
    assert sanitize_filename("../../traversal.mp3") == "traversal.mp3"
    assert sanitize_filename("") == "audio_track"

    # Query sanitization
    assert sanitize_query("   Tum   Hi   Ho   ") == "Tum Hi Ho"
    assert len(sanitize_query("A" * 300)) == 150

    # Rate limiter
    limiter = UserRateLimiter(max_requests=2, window_seconds=10.0)
    user_id = 12345
    assert limiter.is_allowed(user_id) is True
    assert limiter.is_allowed(user_id) is True
    assert limiter.is_allowed(user_id) is False  # 3rd request blocked
    assert limiter.retry_after(user_id) > 0.0


def test_start_command():
    async def _run():
        mock_update = MagicMock()
        mock_update.effective_user.id = 10101
        mock_update.effective_user.first_name = "Alex"
        mock_update.effective_user.username = "alexmusic"
        mock_update.effective_user.last_name = None
        mock_update.effective_user.language_code = "en"
        mock_update.message.reply_text = AsyncMock()
        mock_update.callback_query = None

        mock_context = MagicMock()
        mock_db = AsyncMock()

        with patch("handlers.commands.db", mock_db):
            await start_command(mock_update, mock_context)

        mock_update.message.reply_text.assert_called_once()
        args, kwargs = mock_update.message.reply_text.call_args
        assert "Welcome, Alex!" in args[0]
        assert kwargs.get("reply_markup") is not None

    asyncio.run(_run())
