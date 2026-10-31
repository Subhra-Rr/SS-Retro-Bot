"""
Configuration management for SS Retro Bot.
Loads settings from .env file and environment variables with safe defaults and validation.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from dotenv import load_dotenv

# Resolve project base directory
BASE_DIR = Path(__file__).resolve().parent

# Load environment file
ENV_PATH = BASE_DIR / ".env"
load_dotenv(dotenv_path=ENV_PATH, override=False)


@dataclass(frozen=True)
class Config:
    """Application configuration container."""

    # Telegram configuration
    telegram_bot_token: str = field(
        default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    )
    bot_username: str = field(
        default_factory=lambda: os.getenv("BOT_USERNAME", "SSRetroBot").strip().lstrip("@")
    )
    admin_user_ids: List[int] = field(default_factory=list)

    # Storage paths
    database_path: Path = field(
        default_factory=lambda: BASE_DIR / os.getenv("DATABASE_PATH", "data/bot.db")
    )
    temp_download_dir: Path = field(
        default_factory=lambda: BASE_DIR / os.getenv("TEMP_DOWNLOAD_DIR", "data/temp_downloads")
    )

    # Limits and timeouts
    max_file_size_mb: int = field(
        default_factory=lambda: int(os.getenv("MAX_FILE_SIZE_MB", "49"))
    )
    default_results_per_page: int = field(
        default_factory=lambda: int(os.getenv("DEFAULT_RESULTS_PER_PAGE", "5"))
    )
    search_timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("SEARCH_TIMEOUT_SECONDS", "12.0"))
    )
    download_timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("DOWNLOAD_TIMEOUT_SECONDS", "120.0"))
    )
    cache_ttl_hours: int = field(
        default_factory=lambda: int(os.getenv("CACHE_TTL_HOURS", "24"))
    )

    # Music provider credentials
    jamendo_client_id: str = field(
        default_factory=lambda: os.getenv("JAMENDO_CLIENT_ID", "").strip()
    )
    spotify_client_id: str = field(
        default_factory=lambda: os.getenv("SPOTIFY_CLIENT_ID", "").strip()
    )
    spotify_client_secret: str = field(
        default_factory=lambda: os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()
    )
    musicbrainz_app_name: str = field(
        default_factory=lambda: os.getenv("MUSICBRAINZ_APP_NAME", "SSRetroBot/1.0.0").strip()
    )
    musicbrainz_contact: str = field(
        default_factory=lambda: os.getenv("MUSICBRAINZ_CONTACT", "https://t.me/SSRetroBot").strip()
    )
    massivemusic_api_key: str = field(
        default_factory=lambda: os.getenv("MASSIVEMUSIC_API_KEY", "").strip()
    )
    massivemusic_api_secret: str = field(
        default_factory=lambda: os.getenv("MASSIVEMUSIC_API_SECRET", "").strip()
    )

    # Logging
    log_level: str = field(
        default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper().strip()
    )

    @property
    def max_file_size_bytes(self) -> int:
        """Maximum allowable audio file size in bytes (capped at Telegram's 50MB bot upload limit)."""
        limit_mb = min(self.max_file_size_mb, 50)
        return limit_mb * 1024 * 1024

    def is_admin(self, user_id: int) -> bool:
        """Check if user id has administrative privileges."""
        return user_id in self.admin_user_ids

    def validate(self, allow_placeholder_token: bool = False) -> tuple[bool, List[str]]:
        """
        Validate critical configuration settings.
        Returns a tuple of (is_valid, list_of_errors_or_warnings).
        """
        errors: List[str] = []

        # Validate token
        is_placeholder = (
            not self.telegram_bot_token
            or self.telegram_bot_token.startswith("YOUR_")
            or "your_telegram_bot_token" in self.telegram_bot_token.lower()
        )
        if is_placeholder and not allow_placeholder_token:
            errors.append(
                "TELEGRAM_BOT_TOKEN is missing or contains a placeholder. "
                "Please configure a valid token from @BotFather in your .env file."
            )

        # Validate directories
        try:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            self.temp_download_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            errors.append(f"Failed to prepare storage directories: {e}")

        # Validate limits
        if self.max_file_size_mb <= 0 or self.max_file_size_mb > 50:
            errors.append("MAX_FILE_SIZE_MB must be between 1 and 50 (Telegram bot limit).")

        if self.default_results_per_page < 1 or self.default_results_per_page > 20:
            errors.append("DEFAULT_RESULTS_PER_PAGE must be between 1 and 20.")

        return len(errors) == 0, errors

    def __repr__(self) -> str:
        """Safe string representation redacting sensitive tokens and credentials."""
        token_preview = (
            f"{self.telegram_bot_token[:6]}...{self.telegram_bot_token[-4:]}"
            if len(self.telegram_bot_token) > 10
            else "[REDACTED]"
        )
        return (
            f"Config(bot_username='{self.bot_username}', "
            f"telegram_bot_token='{token_preview}', "
            f"database_path='{self.database_path}', "
            f"temp_download_dir='{self.temp_download_dir}', "
            f"max_file_size_mb={self.max_file_size_mb}, "
            f"jamendo_configured={bool(self.jamendo_client_id)}, "
            f"spotify_configured={bool(self.spotify_client_id and self.spotify_client_secret)}, "
            f"massivemusic_configured={bool(self.massivemusic_api_key)})"
        )


def _parse_admin_ids(raw_value: str) -> List[int]:
    """Parse comma-separated admin user IDs safely."""
    ids: List[int] = []
    if not raw_value:
        return ids
    for part in raw_value.split(","):
        cleaned = part.strip()
        if cleaned.isdigit():
            ids.append(int(cleaned))
    return ids


def load_config() -> Config:
    """Instantiate Config from active environment variables."""
    raw_admin = os.getenv("ADMIN_USER_IDS", "")
    admin_ids = _parse_admin_ids(raw_admin)
    return Config(admin_user_ids=admin_ids)


# Singleton instance
config = load_config()
