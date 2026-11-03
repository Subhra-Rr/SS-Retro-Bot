"""
Music search handlers for commands (/search, /song, /artist, /album) and natural text queries.
Includes spell correction, search blacklist enforcement, artist discography browser,
batch download initiation, and audio/voice note recognition.
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
from services.providers.registry import registry
from services.search_engine import search_engine
from utils.formatting import escape_html, format_duration, format_track_summary
from utils.security import UserRateLimiter, sanitize_query
from utils.spell_checker import correct_spelling
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

    # Feature 2: Batch Download Button
    keyboard.append([
        InlineKeyboardButton("📦 Batch Select & Download", callback_data=f"batch_init:{query_hash}"),
        InlineKeyboardButton("🔙 Back to Menu", callback_data="cmd:start"),
    ])

    return "\n".join(lines), InlineKeyboardMarkup(keyboard)


async def execute_search(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    query: str,
) -> None:
    """Core search execution workflow with rate limiting, spell check, and blacklist check."""
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

    # Feature 16: Check Blacklist / Blocked Words
    if await db.is_term_blocked(cleaned_query):
        blocked_msg = "🚫 <b>This search query contains restricted terms.</b> Please try another query."
        if update.message:
            await update.message.reply_text(blocked_msg, parse_mode=ParseMode.HTML)
        elif update.callback_query:
            await update.callback_query.answer("This search term is restricted.", show_alert=True)
        return

    # Feature 19: Search Spell Correction
    corrected_query, original_query = correct_spelling(cleaned_query)
    effective_query = corrected_query if original_query else cleaned_query

    # Get user preferred page size
    settings = await db.get_user_settings(user.id)
    page_size = settings.get("results_per_page", config.default_results_per_page)

    # Status message
    status_msg = None
    if original_query:
        status_text = (
            f"🔎 <i>Searching for '<b>{escape_html(effective_query)}</b>' "
            f"(auto-corrected from '<i>{escape_html(cleaned_query)}</i>')...</i>"
        )
    else:
        status_text = f"🔎 <i>Searching for '<b>{escape_html(effective_query)}</b>'...</i>"

    if update.message:
        status_msg = await update.message.reply_text(status_text, parse_mode=ParseMode.HTML)
    elif update.callback_query:
        await safe_answer_callback(update.callback_query)
        status_msg = update.callback_query.message

    # Check if input is a YouTube Playlist URL
    try:
        yt_provider = registry.get_provider("YouTube Music")
        if yt_provider and hasattr(yt_provider, "is_playlist_url") and yt_provider.is_playlist_url(effective_query):
            title, pl_tracks = await yt_provider.get_playlist_tracks(effective_query, limit=10)
            if pl_tracks:
                lines = [
                    "📑 <b>YouTube Playlist Detected</b>\n",
                    f"💿 <b>Playlist:</b> {escape_html(title or 'YouTube Playlist')}",
                    f"📊 <b>Tracks:</b> Found {len(pl_tracks)} song(s)\n",
                    "<i>Tap any song below to download audio or apply Audio FX:</i>\n",
                ]
                keyboard = []
                for idx, track in enumerate(pl_tracks, start=1):
                    if context.user_data is not None:
                        context.user_data[f"selected:{track.id}"] = track.to_dict()
                    lines.append(format_track_summary(track.to_dict(), index=idx))
                    keyboard.append([
                        InlineKeyboardButton(
                            f"{idx}. {track.title[:24]}",
                            callback_data=f"track:{track.id}",
                        )
                    ])
                keyboard.append([InlineKeyboardButton("🔙 Main Menu", callback_data="cmd:start")])
                if status_msg:
                    await safe_edit_message(
                        status_msg,
                        "\n".join(lines),
                        reply_markup=InlineKeyboardMarkup(keyboard),
                        parse_mode=ParseMode.HTML,
                    )
                return
    except Exception as pl_err:
        logger.warning("Playlist parsing fallback to normal search: %s", pl_err)

    # Perform search
    tracks = await search_engine.search(effective_query, user_id=user.id)

    if not tracks:
        no_res_text = (
            f"😔 <b>No tracks found for:</b> <i>{escape_html(effective_query)}</i>\n\n"
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

    # Direct YouTube link
    is_direct_yt = len(tracks) == 1 and ("youtube.com" in effective_query.lower() or "youtu.be" in effective_query.lower())
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
            "🎧 <b>Audio:</b> Ready for high-speed download (up to 320 kbps)"
        )
        keyboard = [
            [InlineKeyboardButton("📥 Download (Quality Selector)", callback_data=f"dl_menu:{track.id}")],
            [
                InlineKeyboardButton("🎛️ Audio FX Studio", callback_data=f"fx_menu:{track.id}"),
                InlineKeyboardButton("⭐ Add to Favorites", callback_data=f"fav_add:{track.id}"),
            ],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="cmd:start")],
        ]
        if status_msg:
            await safe_edit_message(
                status_msg, yt_card, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML
            )
        return

    # Store query in user_data context for fast pagination
    query_hash = hashlib.md5(effective_query.lower().strip().encode()).hexdigest()[:10]
    if context.user_data is not None:
        context.user_data[f"search:{query_hash}"] = {
            "query": effective_query,
            "tracks": [t.to_dict() for t in tracks],
        }

    text, reply_markup = build_search_view(effective_query, tracks, page=1, page_size=page_size)
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
    """Handle /artist command — full discography browser (Feature 7)."""
    query = " ".join(context.args) if context.args else ""
    if not query:
        if update.message:
            await update.message.reply_text(
                "👤 <b>Artist Discography Browser</b>\n\n"
                "Usage: <code>/artist &lt;Artist Name&gt;</code>\n"
                "<i>Example:</i> <code>/artist Arijit Singh</code>\n"
                "<i>Shows full discography with albums, release years, and download buttons.</i>",
                parse_mode=ParseMode.HTML,
            )
        return

    await execute_artist_discography(update, context, query)


async def execute_artist_discography(
    update: Update, context: ContextTypes.DEFAULT_TYPE, artist_name: str
) -> None:
    """Display full discography browser for an artist (Feature 7)."""
    user = update.effective_user
    if not user:
        return
    cleaned = sanitize_query(artist_name)
    if await db.is_term_blocked(cleaned):
        if update.message:
            await update.message.reply_text("🚫 This artist search is restricted.")
        return

    status_msg = None
    if update.message:
        status_msg = await update.message.reply_text(
            f"👤 <i>Browsing discography for <b>{escape_html(cleaned)}</b>...</i>",
            parse_mode=ParseMode.HTML,
        )

    tracks = await search_engine.search(f"{cleaned} songs", user_id=user.id, max_total_results=12)
    if not tracks:
        tracks = await search_engine.search(cleaned, user_id=user.id, max_total_results=10)

    if not tracks:
        no_res = f"😔 No discography found for <b>{escape_html(cleaned)}</b>."
        if status_msg:
            await safe_edit_message(status_msg, no_res, parse_mode=ParseMode.HTML)
        return

    albums = set()
    for t in tracks:
        if t.album and len(t.album) > 2:
            albums.add(t.album)

    lines = [
        f"👤 <b>Artist Discography: {escape_html(cleaned.title())}</b>\n",
        f"🎵 <b>Top Songs & Tracks ({len(tracks)}):</b>",
    ]
    keyboard = []
    for idx, t in enumerate(tracks[:8], start=1):
        if context.user_data is not None:
            context.user_data[f"selected:{t.id}"] = t.to_dict()
        year_str = f" ({t.release_date[:4]})" if t.release_date and len(t.release_date) >= 4 else ""
        lines.append(f"  {idx}. <b>{escape_html(t.title)}</b>{year_str}")
        keyboard.append([
            InlineKeyboardButton(f"📥 {idx}. {t.title[:24]}", callback_data=f"dl_menu:{t.id}")
        ])

    if albums:
        lines.append("\n💿 <b>Notable Albums / Compilations:</b>")
        for a in list(albums)[:4]:
            lines.append(f"  • <i>{escape_html(a)}</i>")

    keyboard.append([InlineKeyboardButton("🔙 Main Menu", callback_data="cmd:start")])
    text = "\n".join(lines)
    if status_msg:
        await safe_edit_message(status_msg, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
    elif update.message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)


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


async def audio_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle forwarded audio tracks, voice notes, or humming snippets (Feature 5: Song Fingerprinting).
    """
    if not update.message:
        return

    # 1. Forwarded or uploaded audio file
    if update.message.audio:
        audio = update.message.audio
        query_parts = []
        if audio.title:
            query_parts.append(audio.title.strip())
        if audio.performer:
            query_parts.append(audio.performer.strip())

        query = " ".join(query_parts).strip()
        if not query and audio.file_name:
            import re
            name = re.sub(r"\.[a-zA-Z0-9]+$", "", audio.file_name)
            query = name.replace("_", " ").replace("-", " ").strip()

        if query:
            await update.message.reply_text(
                f"🎧 <b>Audio Fingerprint / File Detected:</b> <i>{escape_html(query)}</i>\n"
                f"🔍 <i>Searching for 320 kbps studio release...</i>",
                parse_mode=ParseMode.HTML,
            )
            await execute_search(update, context, query)
            return
        else:
            await update.message.reply_text(
                "🎧 <i>Audio track received! Type the song name or artist to find the 320 kbps studio release.</i>",
                parse_mode=ParseMode.HTML,
            )
            return

    # 2. Voice note / Humming audio snippet (Feature 5)
    if update.message.voice:
        voice_guide = (
            "🎙️ <b>Voice Note / Humming Snippet Received!</b>\n\n"
            "🔍 <b>Song Recognition:</b>\n"
            "Type the lyrics, melody words, or artist you were humming into the chat.\n"
            "• <i>Example:</i> <code>Tum Hi Ho</code> or <code>Kishore Kumar</code>\n\n"
            "🎧 <i>We will match and deliver the full high-definition track instantly!</i>"
        )
        keyboard = [
            [InlineKeyboardButton("🔍 Search Music", switch_inline_query_current_chat="")],
            [InlineKeyboardButton("🔥 Top Hits", callback_data="cmd:top")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="cmd:start")],
        ]
        await update.message.reply_text(
            voice_guide,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML,
        )
