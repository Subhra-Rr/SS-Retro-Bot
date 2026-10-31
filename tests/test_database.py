"""
Unit tests for SQLite database operations using temporary database.
"""

import asyncio
from pathlib import Path
import pytest
from database import Database


@pytest.fixture
def temp_db(tmp_path: Path):
    db_file = tmp_path / "test_bot.db"
    db_inst = Database(db_path=db_file)
    asyncio.run(db_inst.init_db())
    return db_inst


def test_db_user_and_settings(temp_db: Database):
    async def _run():
        user_id = 99999
        await temp_db.upsert_user(
            user_id=user_id,
            username="testuser",
            first_name="Test",
            last_name="User",
            language_code="en",
        )

        settings = await temp_db.get_user_settings(user_id)
        assert settings["user_id"] == user_id
        assert settings["results_per_page"] == 5

        # Update setting
        await temp_db.update_user_setting(user_id, "results_per_page", 8)
        updated = await temp_db.get_user_settings(user_id)
        assert updated["results_per_page"] == 8

    asyncio.run(_run())


def test_db_search_history(temp_db: Database):
    async def _run():
        user_id = 88888
        await temp_db.upsert_user(user_id=user_id, username="searcher")

        await temp_db.add_search_history(user_id, "Tum Hi Ho")
        await temp_db.add_search_history(user_id, "Rock")
        await temp_db.add_search_history(user_id, "Tum Hi Ho")  # duplicate query

        history = await temp_db.get_search_history(user_id, limit=10)
        assert len(history) == 2
        assert history[0] == "Tum Hi Ho"
        assert history[1] == "Rock"

    asyncio.run(_run())


def test_db_favorites_and_recents(temp_db: Database):
    async def _run():
        user_id = 77777
        await temp_db.upsert_user(user_id=user_id, username="fan")

        track_sample = {
            "id": "jamendo_1001",
            "title": "Summer Song",
            "artist": "Indie Band",
            "album": "Sunshine",
            "duration": 180,
            "provider_name": "Jamendo",
            "has_full_audio": True,
        }

        # Add recent
        await temp_db.add_recent_track(user_id, track_sample)
        recents = await temp_db.get_recent_tracks(user_id)
        assert len(recents) == 1
        assert recents[0]["title"] == "Summer Song"

        # Add favorite
        added = await temp_db.add_favorite(user_id, track_sample)
        assert added is True
        assert await temp_db.is_favorite(user_id, "jamendo_1001") is True

        # Duplicate favorite
        added_again = await temp_db.add_favorite(user_id, track_sample)
        assert added_again is False

        favs = await temp_db.get_favorites(user_id)
        assert len(favs) == 1
        assert favs[0]["id"] == "jamendo_1001"

        # Remove favorite
        removed = await temp_db.remove_favorite(user_id, "jamendo_1001")
        assert removed is True
        assert await temp_db.is_favorite(user_id, "jamendo_1001") is False

    asyncio.run(_run())


def test_db_telegram_file_cache_and_search_cache(temp_db: Database):
    async def _run():
        # Telegram file cache
        cached = await temp_db.get_cached_telegram_file_id("track_123", "Jamendo")
        assert cached is None

        await temp_db.cache_telegram_file_id("track_123", "Jamendo", "FILE_ID_XYZ", file_size=4096)
        cached_now = await temp_db.get_cached_telegram_file_id("track_123", "Jamendo")
        assert cached_now == "FILE_ID_XYZ"

        # Search cache
        results_data = [{"id": "t1", "title": "Track 1"}]
        await temp_db.save_cached_search("query_rock", results_data, ttl_seconds=3600)
        retrieved = await temp_db.get_cached_search("query_rock")
        assert retrieved == results_data

        # Metrics
        await temp_db.record_event("test_event", user_id=123, details="payload")
        summary = await temp_db.get_analytics_summary()
        assert summary["cached_telegram_files"] == 1

    asyncio.run(_run())
