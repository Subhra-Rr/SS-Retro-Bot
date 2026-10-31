"""
Callback query handler for interactive inline keyboard buttons, pagination,
track inspection, audio delivery, favorites, and settings updates.
Defensively protected against stale queries, timeout races, and unexpected errors.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import config
from database import db
from handlers.commands import (
    favorites_command,
    help_command,
    recent_command,
    settings_command,
    top_command,
)
from handlers.music import build_search_view, execute_search
from services.providers.base import UnifiedTrack
from services.providers.registry import registry
from services.search_engine import search_engine
from services.telegram_delivery import audio_delivery
from utils.formatting import escape_html, format_duration, format_file_size
from utils.telegram_helpers import safe_answer_callback, safe_delete_message, safe_edit_message

logger = logging.getLogger(__name__)


def _clean_track_title(title: str) -> str:
    """Strip extraneous release tags for cleaner audio stream search."""
    cleaned = re.sub(r"\(.*?soundtrack.*?\)", "", title, flags=re.IGNORECASE)
    cleaned = re.sub(r"\(.*?original.*?\)", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\[.*?\]", "", cleaned)
    return cleaned.strip() or title


async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route callback queries based on prefix with comprehensive error protection."""
    query = update.callback_query
    if not query or not query.data:
        return

    data = query.data
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return

    # No-op button (e.g. page indicator)
    if data == "noop":
        await safe_answer_callback(query)
        return

    # Command menu navigations
    if data.startswith("cmd:"):
        cmd = data.split(":", 1)[1]
        if cmd == "start":
            await safe_answer_callback(query)
            name = escape_html(user.first_name or "Music Lover")
            welcome_text = (
                f"👋 <b>Welcome back, {name}!</b>\n\n"
                f"🎧 <b>SS Retro Bot</b> — Universal Music Discovery & Audio Delivery Platform.\n\n"
                f"🔍 <i>Type any song or artist name to search instantly.</i>"
            )
            keyboard = [
                [
                    InlineKeyboardButton("🔍 Search Music", switch_inline_query_current_chat=""),
                    InlineKeyboardButton("🔥 Top Hits", callback_data="cmd:top"),
                ],
                [
                    InlineKeyboardButton("⭐ Favorites", callback_data="cmd:favorites"),
                    InlineKeyboardButton("🕒 Recent", callback_data="cmd:recent"),
                ],
                [
                    InlineKeyboardButton("🇮🇳 Hindi Hits", callback_data="qsearch:Hindi"),
                    InlineKeyboardButton("🌸 Odia Songs", callback_data="qsearch:Odia"),
                    InlineKeyboardButton("🎸 Rock", callback_data="qsearch:Rock"),
                ],
                [
                    InlineKeyboardButton("⚙ Settings", callback_data="cmd:settings"),
                    InlineKeyboardButton("ℹ Help & Guide", callback_data="cmd:help"),
                ],
            ]
            await safe_edit_message(
                query,
                welcome_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML,
            )
        elif cmd == "help":
            await help_command(update, context)
        elif cmd == "top":
            await top_command(update, context)
        elif cmd == "recent":
            await recent_command(update, context)
        elif cmd == "favorites":
            await favorites_command(update, context)
        elif cmd == "settings":
            await settings_command(update, context)
        return

    # Quick search shortcut
    if data.startswith("qsearch:"):
        term = data.split(":", 1)[1]
        await execute_search(update, context, term)
        return

    # Pagination navigation: page:{query_hash}:{page_num}
    if data.startswith("page:"):
        parts = data.split(":")
        if len(parts) == 3:
            query_hash = parts[1]
            page_num = int(parts[2])
            cached = context.user_data.get(f"search:{query_hash}") if context.user_data else None
            if cached:
                tracks = [UnifiedTrack.from_dict(d) for d in cached.get("tracks", [])]
                settings = await db.get_user_settings(user.id)
                page_size = settings.get("results_per_page", config.default_results_per_page)
                text, reply_markup = build_search_view(
                    cached.get("query", ""), tracks, page=page_num, page_size=page_size
                )
                await safe_answer_callback(query)
                await safe_edit_message(query, text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
            else:
                await safe_answer_callback(query, "Search session expired. Please search again.", show_alert=True)
        return

    # Track detail card: track:{track_id}
    if data.startswith("track:"):
        track_id = data.split(":", 1)[1]
        await safe_answer_callback(query, "Loading track...")

        # Find track in user search cache or query provider
        track: UnifiedTrack | None = None
        if context.user_data:
            for k, v in context.user_data.items():
                if k.startswith("search:") and isinstance(v, dict):
                    for t in v.get("tracks", []):
                        if t.get("id") == track_id:
                            track = UnifiedTrack.from_dict(t)
                            break
                if track:
                    break

        if not track:
            track = await search_engine.get_track_by_id(track_id)

        if not track:
            await safe_edit_message(
                query,
                "❌ Track information is no longer available.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🔙 Back to Menu", callback_data="cmd:start")]]
                ),
            )
            return

        is_fav = await db.is_favorite(user.id, track.id)
        fav_btn_text = "❌ Remove Favorite" if is_fav else "⭐ Add to Favorites"
        fav_cb_data = f"fav_del:{track.id}" if is_fav else f"fav_add:{track.id}"

        duration_str = format_duration(track.duration)

        card_lines = [
            f"🎵 <b>{escape_html(track.title)}</b>",
            f"👤 <b>Artist:</b> {escape_html(track.artist)}",
            f"💿 <b>Album:</b> {escape_html(track.album or 'Single')}",
            f"⏱ <b>Duration:</b> {duration_str}",
            f"🌐 <b>Provider:</b> {escape_html(track.provider_name)}",
            f"⚖ <b>License:</b> <code>{escape_html(track.license_type or 'Authorized')}</code>",
        ]

        if track.release_date:
            card_lines.append(f"📅 <b>Released:</b> {escape_html(track.release_date)}")
        if track.genre:
            card_lines.append(f"🏷 <b>Genre:</b> {escape_html(track.genre)}")

        card_lines.append("\n✅ <b>Complete audio track is ready for download.</b>")
        keyboard = [
            [InlineKeyboardButton("📥 Download Audio (MP3)", callback_data=f"dl:{track.id}")]
        ]

        if track.external_url:
            keyboard.append([
                InlineKeyboardButton("🎧 Listen on Official Platform", url=track.external_url)
            ])

        keyboard.append([InlineKeyboardButton(fav_btn_text, callback_data=fav_cb_data)])
        keyboard.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="cmd:start")])

        # Cache track in user_data for quick download lookup
        if context.user_data is not None:
            context.user_data[f"selected:{track.id}"] = track.to_dict()

        await safe_edit_message(
            query,
            "\n".join(card_lines),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML,
        )
        return

    # Full audio download & delivery: dl:{track_id}
    if data.startswith("dl:"):
        track_id = data.split(":", 1)[1]
        await safe_answer_callback(query, "Preparing audio download...")

        # Retrieve track
        track: UnifiedTrack | None = None
        if context.user_data and f"selected:{track_id}" in context.user_data:
            track = UnifiedTrack.from_dict(context.user_data[f"selected:{track_id}"])
        else:
            track = await search_engine.get_track_by_id(track_id)

        if not track:
            await safe_answer_callback(query, "Could not find track information.", show_alert=True)
            return

        status_msg = None
        try:
            status_msg = await context.bot.send_message(
                chat_id=chat.id,
                text=f"⏳ <i>Downloading audio for '<b>{escape_html(track.title)}</b>'...</i>",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

        try:
            # If track has no direct full audio (e.g. from iTunes/Spotify), find matching stream
            if not track.has_full_audio or not track.audio_url:
                yt_provider = registry.get_provider("YouTube Music")
                if yt_provider:
                    cleaned_title = _clean_track_title(track.title)
                    search_term = f"{cleaned_title} {track.artist}".strip()
                    yt_results = await yt_provider.search(search_term, limit=1)
                    if not yt_results and cleaned_title != track.title:
                        yt_results = await yt_provider.search(cleaned_title, limit=1)

                    if yt_results:
                        track.audio_url = yt_results[0].audio_url
                        track.has_full_audio = True
                        track.allow_redistribution = True

            if not track.has_full_audio or not track.audio_url:
                if status_msg:
                    await safe_edit_message(status_msg, "❌ Could not locate an audio stream for this track.")
                return

            success, err, msg = await audio_delivery.deliver_track(
                bot=context.bot,
                chat_id=chat.id,
                track=track,
                user_id=user.id,
            )

            # Clean up the downloading status indicator message
            if status_msg:
                await safe_delete_message(status_msg)

            if not success and err:
                err_lower = err.lower()
                if "time" not in err_lower and "out" not in err_lower:
                    await context.bot.send_message(
                        chat_id=chat.id,
                        text=f"❌ <b>Delivery notice:</b> {escape_html(err)}",
                        parse_mode=ParseMode.HTML,
                    )
        except Exception as e:
            logger.error("Download handling exception for %s: %s", track_id, e, exc_info=True)
            if status_msg:
                await safe_delete_message(status_msg)
            await context.bot.send_message(
                chat_id=chat.id,
                text=f"❌ <b>Download notice:</b> {escape_html(str(e))}",
                parse_mode=ParseMode.HTML,
            )
        return

    # Add to favorites: fav_add:{track_id}
    if data.startswith("fav_add:"):
        track_id = data.split(":", 1)[1]
        track = None
        if context.user_data and f"selected:{track_id}" in context.user_data:
            track = UnifiedTrack.from_dict(context.user_data[f"selected:{track_id}"])
        else:
            track = await search_engine.get_track_by_id(track_id)

        if track:
            added = await db.add_favorite(user.id, track.to_dict())
            if added:
                await safe_answer_callback(query, "⭐ Added to your favorites!", show_alert=False)
            else:
                await safe_answer_callback(query, "Already in your favorites.", show_alert=False)
        else:
            await safe_answer_callback(query, "Could not locate track.", show_alert=True)
        return

    # Remove from favorites: fav_del:{track_id}
    if data.startswith("fav_del:"):
        track_id = data.split(":", 1)[1]
        removed = await db.remove_favorite(user.id, track_id)
        if removed:
            await safe_answer_callback(query, "Removed from favorites.", show_alert=False)
            await favorites_command(update, context)
        else:
            await safe_answer_callback(query, "Track was not in favorites.", show_alert=False)
        return

    # User settings change: set:{key}:{val}
    if data.startswith("set:"):
        parts = data.split(":")
        if len(parts) == 3:
            key, val = parts[1], parts[2]
            if key == "rpp":
                await db.update_user_setting(user.id, "results_per_page", int(val))
                await safe_answer_callback(query, f"Results per page updated to {val}!")
            elif key == "lang":
                await db.update_user_setting(user.id, "preferred_language", val)
                await safe_answer_callback(query, f"Language filter updated to {val}!")
            await settings_command(update, context)
        return
