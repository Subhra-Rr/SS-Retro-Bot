"""
Unit tests for Audio Validator and Mutagen inspection.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from services.audio_validator import AudioValidator


def test_nonexistent_and_empty_files(tmp_path: Path):
    validator = AudioValidator(max_bytes=10 * 1024 * 1024)

    # Non-existent
    res = validator.validate_file(tmp_path / "missing.mp3")
    assert not res.is_valid
    assert "does not exist" in res.error_message

    # Empty 0-byte
    empty_file = tmp_path / "empty.mp3"
    empty_file.write_bytes(b"")
    res = validator.validate_file(empty_file)
    assert not res.is_valid
    assert "empty" in res.error_message


def test_file_size_limit_exceeded(tmp_path: Path):
    validator = AudioValidator(max_bytes=1024)  # 1 KB limit
    big_file = tmp_path / "large.mp3"
    big_file.write_bytes(b"x" * 2048)

    res = validator.validate_file(big_file)
    assert not res.is_valid
    assert "exceeds" in res.error_message


def test_invalid_mutagen_audio(tmp_path: Path):
    validator = AudioValidator(max_bytes=10 * 1024 * 1024)
    junk_file = tmp_path / "junk.mp3"
    junk_file.write_bytes(b"NOT_REAL_AUDIO_STREAM_DATA_HERE")

    res = validator.validate_file(junk_file)
    assert not res.is_valid
    assert "Unsupported" in res.error_message or "Corrupted" in res.error_message


def test_valid_audio_inspection(tmp_path: Path):
    validator = AudioValidator(max_bytes=10 * 1024 * 1024)
    sample_file = tmp_path / "sample.mp3"
    sample_file.write_bytes(b"dummy_data_for_mutagen_mock")

    mock_mutagen = MagicMock()
    mock_mutagen.info.length = 185.4
    mock_mutagen.info.bitrate = 192000
    mock_mutagen.tags = {"TIT2": "Valid Song", "TPE1": "Valid Artist", "TALB": "Album"}

    with patch("mutagen.File", return_value=mock_mutagen):
        res = validator.validate_file(sample_file)

    assert res.is_valid
    assert res.duration_seconds == 185
    assert res.bitrate_kbps == 192
    assert res.title == "Valid Song"
    assert res.artist == "Valid Artist"
