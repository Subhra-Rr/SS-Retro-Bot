"""
Structured logging configuration with automatic token and credential redaction.
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

# Patterns matching tokens and secrets to prevent leaking into logs
SECRET_PATTERNS = [
    re.compile(r"bot\d+:[A-Za-z0-9_-]{35}"),  # Telegram bot tokens
    re.compile(r"client_secret=[A-Za-z0-9_-]+", re.IGNORECASE),
    re.compile(r"api_key=[A-Za-z0-9_-]+", re.IGNORECASE),
    re.compile(r"client_id=[A-Za-z0-9_-]+", re.IGNORECASE),
]


class RedactingFormatter(logging.Formatter):
    """Custom formatter that automatically redacts API keys and bot tokens."""

    def format(self, record: logging.LogRecord) -> str:
        original = super().format(record)
        redacted = original
        for pattern in SECRET_PATTERNS:
            redacted = pattern.sub("[REDACTED_SECRET]", redacted)
        return redacted


def setup_logging(level_name: str = "INFO", log_file_path: Path | None = None) -> None:
    """Configure root logging with sanitized formatters and file + stream handlers."""
    numeric_level = getattr(logging, level_name.upper(), logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Avoid duplicate handlers on re-initialization
    if root_logger.handlers:
        root_logger.handlers.clear()

    formatter = RedactingFormatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console stdout handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # File handler if specified
    if log_file_path:
        try:
            log_file_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
            file_handler.setFormatter(formatter)
            root_logger.addHandler(file_handler)
        except Exception as e:
            console_handler.handle(
                logging.LogRecord(
                    name="logging_config",
                    level=logging.WARNING,
                    pathname=__file__,
                    lineno=50,
                    msg=f"Could not open log file at {log_file_path}: {e}",
                    args=(),
                    exc_info=None,
                )
            )

    # Silence verbose third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.INFO)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
