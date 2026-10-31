"""
Message formatting and string presentation utilities for Telegram UI.
"""

from __future__ import annotations

import html
from typing import Any, Dict


def format_duration(seconds: int | float | None) -> str:
    """Format duration in seconds into MM:SS or HH:MM:SS."""
    if seconds is None or seconds <= 0:
        return "00:00"
    total_seconds = int(round(seconds))
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_file_size(size_bytes: int | float | None) -> str:
    """Format bytes into readable KB or MB."""
    if not size_bytes or size_bytes <= 0:
        return "Unknown size"
    mb = size_bytes / (1024 * 1024)
    if mb >= 1.0:
        return f"{mb:.1f} MB"
    kb = size_bytes / 1024
    return f"{kb:.1f} KB"


def escape_html(text: str | None) -> str:
    """Safely escape HTML characters for Telegram HTML mode."""
    if not text:
        return ""
    return html.escape(str(text), quote=False)


def truncate_text(text: str | None, max_length: int = 40) -> str:
    """Truncate text with ellipsis if it exceeds max_length."""
    if not text:
        return ""
    if len(text) <= max_length:
        return text
    return text[: max_length - 1].rstrip() + "…"


def format_track_summary(track: Dict[str, Any], index: int | None = None) -> str:
    """Format a single track for list view."""
    title = escape_html(track.get("title") or "Unknown Title")
    artist = escape_html(track.get("artist") or "Unknown Artist")
    duration = format_duration(track.get("duration"))
    provider = escape_html(track.get("provider_name") or "Music")
    has_full_audio = track.get("has_full_audio", False)
    badge = "🎵" if has_full_audio else "ℹ"

    prefix = f"<b>{index}.</b> " if index is not None else ""
    return f"{prefix}{badge} <b>{title}</b> — <i>{artist}</i> <code>[{duration}]</code> ({provider})"
