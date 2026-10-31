"""
Music search handlers for commands (/search, /song, /artist, /album) and natural text queries.
"""

from __future__ import annotations

import hashlib
import logging
from typing import List

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import config
from database import db
from services.providers.base import UnifiedTrack
from services.search_engine import search_engine
from utils.formatting import escape_html, format_track_summary
from utils.security import UserRateLimiter, sanitize_query
from utils.telegram_helpers import safe_answer_callback, safe_edit_message

logger = logging.getLogger(__name__)

# User rate limiter: 15 queries per 60 seconds
rate_limiter = UserRateLimiter(max_requests=15, window_seconds=60.0)


def build_search_view(
    query: str,
    tracks: List[UnifiedTrack],
    page: int = 1,
    page_size: int = 5,
) -> tuple[str, InlineKeyboardMarkup]:
    """Build formatted message text and inline pagination keyboard for search results."""
    page_tracks, total_pages, current_page = search_engine.paginate(tracks, page, page_size)
    query_hash = hashlib.md5(query.lower().strip().encode()).hexdigest()[:10]

    lines = [
        f"🔍 <b>Search results for:</b> <i>{escape_html(query)}</i>",
        f"📊 Found {len(tracks)} track(s) — Page {current_page} of {total_pages}\n",
    ]

    keyboard = []
    # Track selection buttons
    for idx, track in enumerate(page_tracks, start=(current_page - 1) * page_size + 1):
        lines.append(format_track_summary(track.to_dict(), index=idx))
        keyboard.append([
            InlineKeyboardButton(
                f"{idx}. {track.title[:24]}",
                callback_data=f"track:{track.id}",
            )
        ])

    # Pagination navigation row
    nav_row = []
    if current_page > 1:
        nav_row.append(
            InlineKeyboardButton(
                "◀ Prev", callback_data=f"page:{query_hash}:{current_page - 1}"
            )
        )

    nav_row.append(
        InlineKeyboardButton(
            f"📄 {current_page}/{total_pages}", callback_data="noop"
        )
    )

    if current_page < total_pages:
        nav_row.append(
            InlineKeyboardButton(
                "Next ▶", callback_data=f"page:{query_hash}:{current_page + 1}"
            )
        )

    if nav_row:
        keyboard.append(nav_row)

    keyboard.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="cmd:start")])

    return "\n".join(lines), InlineKeyboardMarkup(keyboard)


async def execute_search(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    query: str,
) -> None:
    """Core search execution workflow with rate limiting and database recording."""
    user = update.effective_user
    if not user:
        return

    # Check rate limit
    if not rate_limiter.is_allowed(user.id):
        wait_sec = rate_limiter.retry_after(user.id)
        msg_text = f"⏳ <i>Rate limit exceeded. Please wait {wait_sec:.0f}s before searching again.</i>"
        if update.message:
            await update.message.reply_text(msg_text, parse_mode=ParseMode.HTML)
        elif update.callback_query:
            await update.callback_query.answer(f"Please wait {wait_sec:.0f}s.", show_alert=True)
        return

    cleaned_query = sanitize_query(query)
    if not cleaned_query:
        if update.message:
            await update.message.reply_text(
                "🔍 Please specify a song name or artist. Example:\n<code>/search Tum Hi Ho</code>",
                parse_mode=ParseMode.HTML,
            )
        return

    # Get user preferred page size
    settings = await db.get_user_settings(user.id)
    page_size = settings.get("results_per_page", config.default_results_per_page)

    # Status message
    status_msg = None
    if update.message:
        status_msg = await update.message.reply_text(
            f"🔎 <i>Searching for '<b>{escape_html(cleaned_query)}</b>'...</i>",
            parse_mode=ParseMode.HTML,
        )
    elif update.callback_query:
        await safe_answer_callback(update.callback_query)
        status_msg = update.callback_query.message

    # Perform search
    tracks = await search_engine.search(cleaned_query, user_id=user.id)

    if not tracks:
        no_res_text = (
            f"😔 <b>No tracks found for:</b> <i>{escape_html(cleaned_query)}</i>\n\n"
            "💡 <b>Tips:</b>\n"
            "• Check spelling or try searching by artist name\n"
            "• Try broader terms like <code>Hindi songs</code> or <code>Rock</code>\n"
            "• Use <code>/top</code> to explore trending tracks"
        )
        keyboard = [
            [InlineKeyboardButton("🔥 Explore Top Hits", callback_data="cmd:top")],
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="cmd:start")],
        ]
        if status_msg:
            await safe_edit_message(
                status_msg,
                no_res_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML,
            )
        return

    # Direct YouTube link: present instant conversion card
    is_direct_yt = len(tracks) == 1 and ("youtube.com" in cleaned_query.lower() or "youtu.be" in cleaned_query.lower())
    if is_direct_yt:
        track = tracks[0]
        if context.user_data is not None:
            context.user_data[f"selected:{track.id}"] = track.to_dict()

        duration_str = format_duration(track.duration)
        yt_card = (
            "🎯 <b>YouTube Media Detected</b>\n\n"
            f"🎵 <b>Title:</b> {escape_html(track.title)}\n"
            f"👤 <b>Channel:</b> {escape_html(track.artist)}\n"
            f"⏱ <b>Duration:</b> {duration_str}\n"
            f"🔗 <a href=\"{track.external_url}\">Watch on YouTube</a>\n\n"
            "🎧 <b>Audio:</b> Ready to convert to MP3 (192–320 kbps clear sound)"
        )
        keyboard = [
            [InlineKeyboardButton("📥 Download Audio (MP3)", callback_data=f"dl:{track.id}")],
            [InlineKeyboardButton("⭐ Add to Favorites", callback_data=f"fav_add:{track.id}")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="cmd:start")],
        ]
        if status_msg:
            await safe_edit_message(
                status_msg, yt_card, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML
            )
        return

    # Store query in user_data context for fast pagination
    query_hash = hashlib.md5(cleaned_query.lower().strip().encode()).hexdigest()[:10]
    if context.user_data is not None:
        context.user_data[f"search:{query_hash}"] = {
            "query": cleaned_query,
            "tracks": [t.to_dict() for t in tracks],
        }

    text, reply_markup = build_search_view(cleaned_query, tracks, page=1, page_size=page_size)
    if status_msg:
        await safe_edit_message(status_msg, text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /search command."""
    query = " ".join(context.args) if context.args else ""
    await execute_search(update, context, query)


async def song_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /song command."""
    query = " ".join(context.args) if context.args else ""
    await execute_search(update, context, query)


async def artist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /artist command."""
    query = " ".join(context.args) if context.args else ""
    if query:
        await execute_search(update, context, f"artist {query}")
    else:
        await execute_search(update, context, query)


async def album_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /album command."""
    query = " ".join(context.args) if context.args else ""
    if query:
        await execute_search(update, context, f"album {query}")
    else:
        await execute_search(update, context, query)


async def text_search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle direct user text input as music search (natural conversational search)."""
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if text.startswith("/"):
        return  # Ignore commands handled elsewhere
    await execute_search(update, context, text)
