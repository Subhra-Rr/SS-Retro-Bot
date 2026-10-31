"""
Unit tests for configuration management.
"""

from pathlib import Path
from config import Config, _parse_admin_ids


def test_config_defaults():
    cfg = Config(telegram_bot_token="test_token_123")
    assert cfg.telegram_bot_token == "test_token_123"
    assert cfg.max_file_size_mb == 49
    assert cfg.max_file_size_bytes == 49 * 1024 * 1024
    assert cfg.default_results_per_page == 5
    assert not cfg.is_admin(999)


def test_config_admin_ids():
    cfg = Config(admin_user_ids=[12345, 67890])
    assert cfg.is_admin(12345)
    assert cfg.is_admin(67890)
    assert not cfg.is_admin(11111)


def test_parse_admin_ids():
    parsed = _parse_admin_ids("123, 456, invalid, 789")
    assert parsed == [123, 456, 789]
    assert _parse_admin_ids("") == []


def test_config_validation():
    # Valid config
    cfg_valid = Config(telegram_bot_token="123456789:ABCdefGHIjklMNOpqrSTUvwxYZ")
    is_valid, errors = cfg_valid.validate()
    assert is_valid
    assert len(errors) == 0

    # Placeholder token
    cfg_invalid = Config(telegram_bot_token="YOUR_TELEGRAM_BOT_TOKEN_HERE")
    is_valid, errors = cfg_invalid.validate(allow_placeholder_token=False)
    assert not is_valid
    assert any("TELEGRAM_BOT_TOKEN" in e for e in errors)

    # Allowed placeholder
    is_valid, errors = cfg_invalid.validate(allow_placeholder_token=True)
    assert is_valid


def test_config_redacted_repr():
    cfg = Config(telegram_bot_token="1234567890:SUPERSECRETTOKENVALUEHERE")
    repr_str = repr(cfg)
    assert "SUPERSECRETTOKENVALUEHERE" not in repr_str
    assert "..." in repr_str
