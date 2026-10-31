"""
Inline mode handler for SS Retro Bot.
Enables users to search and share music tracks in any Telegram chat via @SSRetroBot query.
"""

from __future__ import annotations

import logging
from telegram import (
    InlineQueryResultArticle,
    InlineQueryResultCachedAudio,
    InputTextMessageContent,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from database import db
from services.search_engine import search_engine
from utils.formatting import escape_html, format_duration

logger = logging.getLogger(__name__)


async def inline_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline searches from any Telegram chat."""
    inline_query = update.inline_query
    if not inline_query:
        return

    query = inline_query.query.strip()
    if not query:
        # Prompt user to type query
        await inline_query.answer([], switch_pm_text="Type a song or artist to search...", switch_pm_parameter="search")
        return

    tracks = await search_engine.search(query, max_total_results=10)
    results = []

    for idx, track in enumerate(tracks):
        duration_str = format_duration(track.duration)
        cached_file_id = await db.get_cached_telegram_file_id(track.id, track.provider_name)

        if cached_file_id:
            results.append(
                InlineQueryResultCachedAudio(
                    id=f"inline_{track.id}_{idx}",
                    audio_file_id=cached_file_id,
                    caption=(
                        f"🎵 <b>{escape_html(track.title)}</b> — <i>{escape_html(track.artist)}</i>\n"
                        f"💿 {escape_html(track.album or 'Single')} | 🌐 {escape_html(track.provider_name)}"
                    ),
                    parse_mode=ParseMode.HTML,
                )
            )
        else:
            description = f"{track.artist} • {duration_str} • {track.provider_name}"
            msg_content = (
                f"🎵 <b>{escape_html(track.title)}</b>\n"
                f"👤 <b>Artist:</b> {escape_html(track.artist)}\n"
                f"💿 <b>Album:</b> {escape_html(track.album or 'Single')}\n"
                f"⏱ <b>Duration:</b> {duration_str}\n"
                f"🌐 <b>Provider:</b> {escape_html(track.provider_name)}\n"
            )
            if track.external_url:
                msg_content += f"\n🔗 <a href='{track.external_url}'>Listen / Explore</a>"

            results.append(
                InlineQueryResultArticle(
                    id=f"inline_{track.id}_{idx}",
                    title=f"{track.title} - {track.artist}",
                    description=description,
                    thumbnail_url=track.cover_url,
                    input_message_content=InputTextMessageContent(
                        msg_content,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=False,
                    ),
                )
            )

    await inline_query.answer(results, cache_time=300, is_personal=False)
