"""Telegram handlers package."""

from handlers.admin import (
    admin_command,
    block_term_command,
    blocklist_command,
    broadcast_command,
    schedule_broadcast_command,
    unblock_term_command,
    users_command,
)
from handlers.callbacks import handle_callback_query
from handlers.commands import (
    charts_command,
    favorites_command,
    help_command,
    history_command,
    recent_command,
    settings_command,
    start_command,
    top_command,
)
from handlers.inline import inline_query_handler
from handlers.music import (
    album_command,
    artist_command,
    audio_message_handler,
    search_command,
    song_command,
    text_search_handler,
)
from handlers.playlist import playlist_command
from handlers.stats import stats_command

__all__ = [
    "start_command",
    "help_command",
    "top_command",
    "charts_command",
    "recent_command",
    "history_command",
    "favorites_command",
    "settings_command",
    "search_command",
    "song_command",
    "artist_command",
    "album_command",
    "text_search_handler",
    "audio_message_handler",
    "handle_callback_query",
    "inline_query_handler",
    "admin_command",
    "users_command",
    "broadcast_command",
    "schedule_broadcast_command",
    "block_term_command",
    "unblock_term_command",
    "blocklist_command",
    "playlist_command",
    "stats_command",
]
