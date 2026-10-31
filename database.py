"""
Asynchronous SQLite database interface for SS Retro Bot using aiosqlite.
Manages users, search history, favorites, settings, cache, and metrics.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite

from config import config

logger = logging.getLogger(__name__)


SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        language_code TEXT,
        created_at REAL NOT NULL,
        last_active REAL NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS search_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        query TEXT NOT NULL,
        timestamp REAL NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users (user_id)
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_search_history_user ON search_history (user_id, timestamp DESC);
    """,
    """
    CREATE TABLE IF NOT EXISTS recent_tracks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        track_id TEXT NOT NULL,
        title TEXT NOT NULL,
        artist TEXT NOT NULL,
        provider_name TEXT NOT NULL,
        raw_json TEXT NOT NULL,
        selected_at REAL NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users (user_id)
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_recent_tracks_user ON recent_tracks (user_id, selected_at DESC);
    """,
    """
    CREATE TABLE IF NOT EXISTS favorites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        track_id TEXT NOT NULL,
        title TEXT NOT NULL,
        artist TEXT NOT NULL,
        album TEXT,
        duration INTEGER,
        audio_url TEXT,
        provider_name TEXT NOT NULL,
        telegram_file_id TEXT,
        raw_json TEXT NOT NULL,
        saved_at REAL NOT NULL,
        UNIQUE (user_id, track_id),
        FOREIGN KEY (user_id) REFERENCES users (user_id)
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_favorites_user ON favorites (user_id, saved_at DESC);
    """,
    """
    CREATE TABLE IF NOT EXISTS user_settings (
        user_id INTEGER PRIMARY KEY,
        results_per_page INTEGER NOT NULL DEFAULT 5,
        preferred_language TEXT DEFAULT 'all',
        preferred_genre TEXT DEFAULT 'all',
        audio_quality TEXT DEFAULT 'standard',
        updated_at REAL NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users (user_id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS telegram_file_cache (
        track_id TEXT NOT NULL,
        provider_name TEXT NOT NULL,
        telegram_file_id TEXT NOT NULL,
        file_size INTEGER,
        cached_at REAL NOT NULL,
        PRIMARY KEY (track_id, provider_name)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS search_cache (
        cache_key TEXT PRIMARY KEY,
        results_json TEXT NOT NULL,
        cached_at REAL NOT NULL,
        expires_at REAL NOT NULL
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_search_cache_expiry ON search_cache (expires_at);
    """,
    """
    CREATE TABLE IF NOT EXISTS analytics_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_name TEXT NOT NULL,
        user_id INTEGER,
        details TEXT,
        timestamp REAL NOT NULL
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_analytics_event ON analytics_events (event_name, timestamp);
    """,
]


class Database:
    """Async database operations manager."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or config.database_path

    async def init_db(self) -> None:
        """Create tables and indices if they do not exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            for statement in SCHEMA_STATEMENTS:
                await db.execute(statement)
            await db.commit()
        logger.info("Database schema initialized at %s", self.db_path)

    async def upsert_user(
        self,
        user_id: int,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        language_code: str | None = None,
    ) -> None:
        """Record or update user activity."""
        now = time.time()
        clean_user_id = int(user_id)
        clean_username = str(username) if isinstance(username, str) else None
        clean_first = str(first_name) if isinstance(first_name, str) else None
        clean_last = str(last_name) if isinstance(last_name, str) else None
        clean_lang = str(language_code) if isinstance(language_code, str) else None

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO users (user_id, username, first_name, last_name, language_code, created_at, last_active)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username=excluded.username,
                    first_name=excluded.first_name,
                    last_name=excluded.last_name,
                    language_code=coalesce(excluded.language_code, users.language_code),
                    last_active=excluded.last_active
                """,
                (clean_user_id, clean_username, clean_first, clean_last, clean_lang, now, now),
            )
            # Ensure settings row exists
            await db.execute(
                """
                INSERT OR IGNORE INTO user_settings (user_id, results_per_page, updated_at)
                VALUES (?, ?, ?)
                """,
                (user_id, config.default_results_per_page, now),
            )
            await db.commit()

    async def add_search_history(self, user_id: int, query: str) -> None:
        """Record a search query in user history."""
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO search_history (user_id, query, timestamp) VALUES (?, ?, ?)",
                (user_id, query.strip(), now),
            )
            # Prune user history beyond last 50 entries
            await db.execute(
                """
                DELETE FROM search_history
                WHERE user_id = ? AND id NOT IN (
                    SELECT id FROM search_history WHERE user_id = ? ORDER BY timestamp DESC LIMIT 50
                )
                """,
                (user_id, user_id),
            )
            await db.commit()

    async def get_search_history(self, user_id: int, limit: int = 10) -> List[str]:
        """Fetch distinct recent queries for a user."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT DISTINCT query FROM search_history
                WHERE user_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (user_id, limit),
            ) as cursor:
                rows = await cursor.fetchall()
                return [r[0] for r in rows]

    async def add_recent_track(self, user_id: int, track: Dict[str, Any]) -> None:
        """Record a selected track."""
        now = time.time()
        raw_json = json.dumps(track, ensure_ascii=False)
        track_id = str(track.get("id") or "")
        title = track.get("title") or "Unknown"
        artist = track.get("artist") or "Unknown"
        provider = track.get("provider_name") or "unknown"

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO recent_tracks (user_id, track_id, title, artist, provider_name, raw_json, selected_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, track_id, title, artist, provider, raw_json, now),
            )
            # Prune beyond 30 entries
            await db.execute(
                """
                DELETE FROM recent_tracks
                WHERE user_id = ? AND id NOT IN (
                    SELECT id FROM recent_tracks WHERE user_id = ? ORDER BY selected_at DESC LIMIT 30
                )
                """,
                (user_id, user_id),
            )
            await db.commit()

    async def get_recent_tracks(self, user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        """Fetch recent tracks selected by a user."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT raw_json FROM recent_tracks
                WHERE user_id = ?
                ORDER BY selected_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ) as cursor:
                rows = await cursor.fetchall()
                tracks = []
                for (raw,) in rows:
                    try:
                        tracks.append(json.loads(raw))
                    except Exception:
                        continue
                return tracks

    async def add_favorite(self, user_id: int, track: Dict[str, Any]) -> bool:
        """Add a track to user's favorites. Returns True if added, False if already exists."""
        now = time.time()
        track_id = str(track.get("id") or "")
        title = track.get("title") or "Unknown"
        artist = track.get("artist") or "Unknown"
        album = track.get("album")
        duration = track.get("duration")
        audio_url = track.get("audio_url")
        provider = track.get("provider_name") or "unknown"
        telegram_file_id = track.get("telegram_file_id")
        raw_json = json.dumps(track, ensure_ascii=False)

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT OR IGNORE INTO favorites
                (user_id, track_id, title, artist, album, duration, audio_url, provider_name, telegram_file_id, raw_json, saved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    track_id,
                    title,
                    artist,
                    album,
                    duration,
                    audio_url,
                    provider,
                    telegram_file_id,
                    raw_json,
                    now,
                ),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def remove_favorite(self, user_id: int, track_id: str) -> bool:
        """Remove a track from favorites."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM favorites WHERE user_id = ? AND track_id = ?",
                (user_id, str(track_id)),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def is_favorite(self, user_id: int, track_id: str) -> bool:
        """Check if a track is in user favorites."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT 1 FROM favorites WHERE user_id = ? AND track_id = ?",
                (user_id, str(track_id)),
            ) as cursor:
                row = await cursor.fetchone()
                return row is not None

    async def get_favorites(self, user_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        """Fetch all user favorites ordered by most recently saved."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT raw_json FROM favorites
                WHERE user_id = ?
                ORDER BY saved_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ) as cursor:
                rows = await cursor.fetchall()
                results = []
                for (raw,) in rows:
                    try:
                        results.append(json.loads(raw))
                    except Exception:
                        continue
                return results

    async def get_user_settings(self, user_id: int) -> Dict[str, Any]:
        """Fetch settings for a user with sensible fallbacks."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM user_settings WHERE user_id = ?",
                (user_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return dict(row)
                return {
                    "user_id": user_id,
                    "results_per_page": config.default_results_per_page,
                    "preferred_language": "all",
                    "preferred_genre": "all",
                    "audio_quality": "standard",
                }

    async def update_user_setting(self, user_id: int, key: str, value: Any) -> None:
        """Update a specific setting for a user."""
        allowed_keys = {
            "results_per_page": int,
            "preferred_language": str,
            "preferred_genre": str,
            "audio_quality": str,
        }
        if key not in allowed_keys:
            raise ValueError(f"Invalid setting key: {key}")

        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                f"""
                INSERT INTO user_settings (user_id, {key}, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    {key}=excluded.{key},
                    updated_at=excluded.updated_at
                """,
                (user_id, value, now),
            )
            await db.commit()

    async def get_cached_telegram_file_id(
        self, track_id: str, provider_name: str
    ) -> Optional[str]:
        """Retrieve cached Telegram audio file_id for instant delivery."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT telegram_file_id FROM telegram_file_cache
                WHERE track_id = ? AND provider_name = ?
                """,
                (str(track_id), provider_name),
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None

    async def cache_telegram_file_id(
        self,
        track_id: str,
        provider_name: str,
        telegram_file_id: str,
        file_size: int | None = None,
    ) -> None:
        """Store Telegram audio file_id."""
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO telegram_file_cache
                (track_id, provider_name, telegram_file_id, file_size, cached_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (str(track_id), provider_name, telegram_file_id, file_size, now),
            )
            await db.commit()

    async def get_cached_search(self, cache_key: str) -> Optional[List[Dict[str, Any]]]:
        """Get cached search results if not expired."""
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT results_json FROM search_cache
                WHERE cache_key = ? AND expires_at > ?
                """,
                (cache_key, now),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    try:
                        return json.loads(row[0])
                    except Exception:
                        return None
                return None

    async def save_cached_search(
        self, cache_key: str, results: List[Dict[str, Any]], ttl_seconds: int = 86400
    ) -> None:
        """Cache search results for a query."""
        now = time.time()
        expires = now + ttl_seconds
        payload = json.dumps(results, ensure_ascii=False)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO search_cache (cache_key, results_json, cached_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (cache_key, payload, now, expires),
            )
            # Periodically prune expired entries
            await db.execute("DELETE FROM search_cache WHERE expires_at < ?", (now,))
            await db.commit()

    async def clear_all_search_cache(self) -> None:
        """Purge all search cache entries so users always get fresh, live search results."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM search_cache")
            await db.commit()
            logger.info("Purged search cache database table.")

    async def record_event(
        self, event_name: str, user_id: int | None = None, details: str = ""
    ) -> None:
        """Record an analytics or telemetry event."""
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO analytics_events (event_name, user_id, details, timestamp)
                VALUES (?, ?, ?, ?)
                """,
                (event_name, user_id, details, now),
            )
            await db.commit()

    async def get_all_users(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Retrieve registered users sorted by most recent activity."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT user_id, username, first_name, last_name, language_code, created_at, last_active
                FROM users
                ORDER BY last_active DESC
                LIMIT ?
                """,
                (limit,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def get_all_user_ids(self) -> List[int]:
        """Retrieve all registered user IDs for broadcasts."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT user_id FROM users ORDER BY user_id") as cursor:
                rows = await cursor.fetchall()
                return [r[0] for r in rows]

    async def get_analytics_summary(self) -> Dict[str, int]:
        """Retrieve overall usage counters."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM users") as c:
                total_users = (await c.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM search_history") as c:
                total_searches = (await c.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM favorites") as c:
                total_favorites = (await c.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM telegram_file_cache") as c:
                cached_files = (await c.fetchone())[0]
            return {
                "total_users": total_users,
                "total_searches": total_searches,
                "total_favorites": total_favorites,
                "cached_telegram_files": cached_files,
            }


# Singleton database instance
db = Database()
