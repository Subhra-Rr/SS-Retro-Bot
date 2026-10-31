"""Telegram handlers package."""

from handlers.admin import admin_command
from handlers.callbacks import handle_callback_query
from handlers.commands import (
    favorites_command,
    help_command,
    recent_command,
    settings_command,
    start_command,
    top_command,
)
from handlers.inline import inline_query_handler
from handlers.music import (
    album_command,
    artist_command,
    search_command,
    song_command,
    text_search_handler,
)

__all__ = [
    "start_command",
    "help_command",
    "top_command",
    "recent_command",
    "favorites_command",
    "settings_command",
    "search_command",
    "song_command",
    "artist_command",
    "album_command",
    "text_search_handler",
    "handle_callback_query",
    "inline_query_handler",
    "admin_command",
]
