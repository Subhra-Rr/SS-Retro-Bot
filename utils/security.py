"""
Security utilities including filename sanitization, rate limiting, and input validation.
"""

from __future__ import annotations

import re
import time
import unicodedata
from collections import defaultdict
from typing import Dict, List


def sanitize_filename(name: str, max_length: int = 100, default: str = "audio_track") -> str:
    """
    Sanitize an arbitrary string into a safe cross-platform filename.
    Strips Windows/POSIX forbidden characters, prevents path traversal, and enforces max length.
    """
    if not name:
        return default

    # Normalize unicode to NFKD
    normalized = unicodedata.normalize("NFKD", name)

    # Remove non-ascii or non-printable chars while keeping alphanumeric, dash, underscore, space, dot
    safe = re.sub(r'[\\/*?:"<>|]', "", normalized)
    safe = re.sub(r"[^\w\s.-]", "", safe, flags=re.UNICODE)
    safe = safe.strip(" .")

    # If result is empty after stripping
    if not safe:
        safe = default

    # Truncate length safely preserving extension if possible
    if len(safe) > max_length:
        safe = safe[:max_length].rstrip(" .")

    return safe or default


def sanitize_query(query: str, max_length: int = 300) -> str:
    """Clean and constrain user search input to prevent injection or abuse."""
    if not query:
        return ""
    # Strip dangerous characters, control codes
    cleaned = "".join(ch for ch in query if ch.isprintable())
    # Collapse multiple whitespace
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:max_length]


class UserRateLimiter:
    """
    In-memory rate limiter per user ID using sliding window algorithm.
    Guards against rapid fire spam, API hammering, and duplicate submissions.
    """

    def __init__(self, max_requests: int = 10, window_seconds: float = 60.0):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._history: Dict[int, List[float]] = defaultdict(list)

    def is_allowed(self, user_id: int) -> bool:
        """Check if user request is within allowed limits."""
        now = time.monotonic()
        cutoff = now - self.window_seconds

        # Prune old timestamps
        timestamps = [ts for ts in self._history[user_id] if ts > cutoff]
        self._history[user_id] = timestamps

        if len(timestamps) >= self.max_requests:
            return False

        timestamps.append(now)
        return True

    def retry_after(self, user_id: int) -> float:
        """Returns seconds until the user can issue the next request."""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        timestamps = [ts for ts in self._history[user_id] if ts > cutoff]
        if not timestamps or len(timestamps) < self.max_requests:
            return 0.0
        earliest = timestamps[0]
        return max(0.0, round((earliest + self.window_seconds) - now, 1))

    def reset(self, user_id: int | None = None) -> None:
        """Clear rate limit history for a specific user or globally."""
        if user_id is not None:
            self._history.pop(user_id, None)
        else:
            self._history.clear()
