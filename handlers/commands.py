"""
Core command handlers for SS Retro Bot (/start, /help, /top, /recent, /favorites, /settings, etc.).
Protected with safe callback acknowledgement and idempotent message editing.
"""

from __future__ import annotations

import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonCommands, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import config
from database import db
from services.providers.registry import registry
from services.search_engine import search_engine
from utils.formatting import escape_html, format_duration, format_track_summary
from utils.telegram_helpers import safe_answer_callback, safe_edit_message

logger = logging.getLogger(__name__)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command with rich welcome card and category exploration."""
    user = update.effective_user
    if not user:
        return

    # Record or update user
    await db.upsert_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        language_code=user.language_code,
    )
    await db.record_event("start_command", user.id)

    # Ensure native Telegram Menu Button (☰) is actively visible for this user
    if update.effective_chat:
        try:
            await context.bot.set_chat_menu_button(
                chat_id=update.effective_chat.id,
                menu_button=MenuButtonCommands(),
            )
        except Exception:
            pass

    # Check for deep-link track identifier (e.g. /start dl_yt_La4gIKzB5zk)
    if context.args and isinstance(context.args, list) and len(context.args) > 0 and isinstance(context.args[0], str):
        param = context.args[0].strip()
        if param.startswith("dl_"):
            track_id = param[3:].strip()
            track = await search_engine.get_track_by_id(track_id)
            if track:
                if context.user_data is not None:
                    context.user_data[f"selected:{track.id}"] = track.to_dict()

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
                    [InlineKeyboardButton("📥 Download (320 kbps MP3)", callback_data=f"dl:{track.id}")],
                    [InlineKeyboardButton("🎛️ Audio FX Studio", callback_data=f"fx_menu:{track.id}")],
                ]
                if track.external_url:
                    keyboard.append([InlineKeyboardButton("🎧 Listen on Official Platform", url=track.external_url)])
                keyboard.append([InlineKeyboardButton(fav_btn_text, callback_data=fav_cb_data)])
                keyboard.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="cmd:start")])

                if update.message:
                    await update.message.reply_text(
                        "\n".join(card_lines),
                        reply_markup=InlineKeyboardMarkup(keyboard),
                        parse_mode=ParseMode.HTML,
                    )
                    return

    name = escape_html(user.first_name or "Music Lover")
    welcome_text = (
        f"👋 <b>Welcome, {name}!</b>\n\n"
        f"🎧 <b>SS Retro Bot</b> is your universal music discovery and authorized audio delivery platform.\n\n"
        f"🔍 <b>What can you search?</b>\n"
        f"• Hindi & Bollywood hits\n"
        f"• Odia, Bengali, Punjabi, Telugu, Tamil & regional music\n"
        f"• International pop, rock, electronic & classical\n"
        f"• Devotional, vintage retro classics, remixes & independent songs\n\n"
        f"💡 <i>Type any song or artist name directly in the chat, or use commands below:</i>"
    )

    keyboard = [
        [
            InlineKeyboardButton("🔍 Search Music", switch_inline_query_current_chat=""),
            InlineKeyboardButton("🔥 Top Hits", callback_data="cmd:top"),
            InlineKeyboardButton("📈 Charts", callback_data="cmd:charts"),
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

    if config.is_admin(user.id):
        keyboard.append([InlineKeyboardButton("🛡 Admin Control Panel", callback_data="cmd:admin")])

    if update.callback_query:
        await safe_answer_callback(update.callback_query)
        await safe_edit_message(
            update.callback_query,
            welcome_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML,
        )
    elif update.message:
        await update.message.reply_text(
            welcome_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML,
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command explaining commands and search features."""
    help_text = (
        "📚 <b>SS Retro Bot — User Guide</b>\n\n"
        "<b>Available Commands:</b>\n"
        "• <code>/search &lt;query&gt;</code> — Search songs, artists, albums, or lyrics\n"
        "• <code>/song &lt;title&gt;</code> — Dedicated track search\n"
        "• <code>/artist &lt;name&gt;</code> — Discover songs by artist\n"
        "• <code>/album &lt;name&gt;</code> — Search music albums\n"
        "• <code>/top</code> — Explore featured & trending music\n"
        "• <code>/recent</code> — View your recently delivered tracks\n"
        "• <code>/favorites</code> — Access your saved favorite tracks\n"
        "• <code>/settings</code> — Configure search preferences & page size\n"
        "• <code>/help</code> — Show this guide\n\n"
        "🎧 <b>Real-World Music Features:</b>\n"
        "• <b>Live Search:</b> Type any song name (Hindi, Bollywood, Odia, Punjabi, English, etc.) for instant matches.\n"
        "• <b>YouTube Link Converter:</b> Paste any YouTube URL directly into the chat to convert video to high-fidelity MP3.\n"
        "• <b>Crystal Clear Audio:</b> Full-length MP3 files (up to 320 kbps) delivered directly to Telegram.\n"
        "• <b>Admin Tools:</b> <code>/users</code> to view registered users, <code>/broadcast</code> to send announcements."
    )

    if update.callback_query:
        await safe_answer_callback(update.callback_query)
        await safe_edit_message(
            update.callback_query,
            help_text,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Back to Menu", callback_data="cmd:start")]]
            ),
            parse_mode=ParseMode.HTML,
        )
    elif update.message:
        await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)


async def top_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /top command showing popular or featured tracks."""
    user_id = update.effective_user.id if update.effective_user else None
    if user_id:
        await db.record_event("top_command", user_id)

    status_msg = None
    if update.message:
        status_msg = await update.message.reply_text("🔄 <i>Fetching top featured tracks...</i>", parse_mode=ParseMode.HTML)
    elif update.callback_query:
        await safe_answer_callback(update.callback_query)
        status_msg = update.callback_query.message

    # Aggregate top tracks from active providers
    tracks = []
    for provider in registry.get_configured():
        try:
            top = await provider.get_top_tracks(limit=5)
            if top:
                tracks.extend(top)
        except Exception as e:
            logger.debug("Error fetching top tracks from %s: %s", provider.name, e)

    if not tracks:
        # Fallback to searching popular hits
        tracks = await search_engine.search("top hits", user_id=user_id, max_total_results=10)

    if not tracks:
        text = "😔 No featured tracks are currently available. Please try searching with <code>/search</code>."
        if status_msg:
            await safe_edit_message(status_msg, text, parse_mode=ParseMode.HTML)
        return

    lines = ["🔥 <b>Top Trending & Featured Tracks:</b>\n"]
    keyboard = []
    for idx, track in enumerate(tracks[:8], start=1):
        lines.append(format_track_summary(track.to_dict(), index=idx))
        keyboard.append([
            InlineKeyboardButton(
                f"{idx}. {track.title[:25]}",
                callback_data=f"track:{track.id}",
            )
        ])

    keyboard.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="cmd:start")])

    text = "\n".join(lines)
    if status_msg:
        await safe_edit_message(
            status_msg,
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML,
        )


async def recent_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /recent command displaying recently selected tracks."""
    user = update.effective_user
    if not user:
        return

    recents = await db.get_recent_tracks(user.id, limit=10)
    if not recents:
        text = "🕒 <b>Recently Delivered Tracks:</b>\n\nYou haven't selected any tracks yet. Try searching for a song!"
        keyboard = [[InlineKeyboardButton("🔍 Search Now", switch_inline_query_current_chat="")]]
        if update.callback_query:
            await safe_answer_callback(update.callback_query)
            await safe_edit_message(
                update.callback_query,
                text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML,
            )
        elif update.message:
            await update.message.reply_text(
                text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML,
            )
        return

    lines = ["🕒 <b>Your Recent Tracks:</b>\n"]
    keyboard = []
    for idx, track in enumerate(recents, start=1):
        lines.append(format_track_summary(track, index=idx))
        track_id = track.get("id")
        if track_id:
            keyboard.append([
                InlineKeyboardButton(
                    f"{idx}. {track.get('title', 'Track')[:25]}",
                    callback_data=f"track:{track_id}",
                )
            ])

    keyboard.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="cmd:start")])
    text = "\n".join(lines)

    if update.callback_query:
        await safe_answer_callback(update.callback_query)
        await safe_edit_message(
            update.callback_query,
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML,
        )
    elif update.message:
        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML,
        )


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /history command displaying user search and download records."""
    user = update.effective_user
    if not user:
        return

    recents = await db.get_recent_tracks(user.id, limit=6)
    searches = await db.get_search_history(user.id, limit=6)

    if not recents and not searches:
        text = (
            "🕒 <b>Your Search & Download Records:</b>\n\n"
            "You have no search or download records yet.\n"
            "Start searching for songs or paste a YouTube link to build your history!"
        )
        keyboard = [[InlineKeyboardButton("🔍 Search Music", switch_inline_query_current_chat="")]]
        if update.callback_query:
            await safe_answer_callback(update.callback_query)
            await safe_edit_message(
                update.callback_query, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML
            )
        elif update.message:
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return

    lines = ["🕒 <b>Your Search & Download Records:</b>\n"]
    keyboard = []

    if recents:
        lines.append("🎧 <b>Recent Audio Downloads:</b>")
        for idx, track in enumerate(recents, start=1):
            lines.append(format_track_summary(track, index=idx))
            track_id = track.get("id")
            if track_id:
                keyboard.append([
                    InlineKeyboardButton(
                        f"📥 {idx}. {track.get('title', 'Track')[:24]}",
                        callback_data=f"track:{track_id}",
                    )
                ])
    else:
        lines.append("🎧 <b>Recent Audio Downloads:</b>\n<i>No downloads yet.</i>\n")

    if searches:
        lines.append("\n🔍 <b>Recent Searches:</b>")
        for q in searches:
            lines.append(f"• <i>{escape_html(q)}</i>")

    keyboard.append([
        InlineKeyboardButton("🔍 New Search", switch_inline_query_current_chat=""),
        InlineKeyboardButton("🔙 Back to Menu", callback_data="cmd:start"),
    ])

    text = "\n".join(lines)
    if update.callback_query:
        await safe_answer_callback(update.callback_query)
        await safe_edit_message(
            update.callback_query, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML
        )
    elif update.message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)


async def favorites_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /favorites command displaying user's saved library."""
    user = update.effective_user
    if not user:
        return

    favorites = await db.get_favorites(user.id, limit=20)
    if not favorites:
        text = (
            "⭐ <b>Your Favorite Tracks:</b>\n\n"
            "You haven't saved any tracks yet.\n"
            "When viewing any song details card, click <b>⭐ Add to Favorites</b> to save it here!"
        )
        keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="cmd:start")]]
        if update.callback_query:
            await safe_answer_callback(update.callback_query)
            await safe_edit_message(
                update.callback_query,
                text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML,
            )
        elif update.message:
            await update.message.reply_text(
                text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML,
            )
        return

    lines = ["⭐ <b>Your Saved Favorites:</b>\n"]
    keyboard = []
    for idx, track in enumerate(favorites, start=1):
        lines.append(format_track_summary(track, index=idx))
        track_id = track.get("id")
        if track_id:
            keyboard.append([
                InlineKeyboardButton(
                    f"{idx}. {track.get('title', 'Track')[:25]}",
                    callback_data=f"track:{track_id}",
                )
            ])

    keyboard.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="cmd:start")])
    text = "\n".join(lines)

    if update.callback_query:
        await safe_answer_callback(update.callback_query)
        await safe_edit_message(
            update.callback_query,
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML,
        )
    elif update.message:
        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML,
        )


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /settings command for managing user preferences."""
    user = update.effective_user
    if not user:
        return

    settings = await db.get_user_settings(user.id)
    rpp = settings.get("results_per_page", config.default_results_per_page)
    lang = settings.get("preferred_language", "all")

    text = (
        "⚙ <b>User Settings & Preferences</b>\n\n"
        f"• <b>Results per page:</b> <code>{rpp}</code>\n"
        f"• <b>Preferred Language:</b> <code>{lang.capitalize()}</code>\n\n"
        "Customize how search results and navigation are displayed:"
    )

    keyboard = [
        [
            InlineKeyboardButton("📄 3 per page", callback_data="set:rpp:3"),
            InlineKeyboardButton("📄 5 per page", callback_data="set:rpp:5"),
            InlineKeyboardButton("📄 8 per page", callback_data="set:rpp:8"),
        ],
        [
            InlineKeyboardButton("🌐 All Languages", callback_data="set:lang:all"),
            InlineKeyboardButton("🇮🇳 Hindi", callback_data="set:lang:hindi"),
            InlineKeyboardButton("🌸 Odia", callback_data="set:lang:odia"),
        ],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="cmd:start")],
    ]

    if update.callback_query:
        await safe_answer_callback(update.callback_query)
        await safe_edit_message(
            update.callback_query,
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML,
        )
    elif update.message:
        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML,
        )


async def charts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /charts command presenting curated regional and global music charts."""
    user = update.effective_user
    if user:
        await db.record_event("charts_command", user.id)

    charts_text = (
        "📈 <b>SS Retro Bot — Curated Music Charts</b> 🌟\n\n"
        "Explore trending playlists and top musical charts across genres:\n\n"
        "• 🇮🇳 <b>Bollywood Hot 10</b> — Trending Hindi cinema tracks\n"
        "• 🌸 <b>Odia Superhits</b> — Modern hits & nostalgic Odia classics\n"
        "• 💥 <b>Punjabi Hits</b> — High-energy beats & chartbusters\n"
        "• 🌍 <b>Global Billboard</b> — Worldwide trending hits\n"
        "• 📻 <b>90s Golden Retro</b> — Unforgettable evergreen melodies\n"
        "• 🎸 <b>Rock & Indie Legends</b> — Guitar anthems & indie vibes\n\n"
        "<i>Select a chart below to browse and download:</i>"
    )

    keyboard = [
        [
            InlineKeyboardButton("🇮🇳 Bollywood Hot 10", callback_data="qsearch:Bollywood Hits"),
            InlineKeyboardButton("🌸 Odia Superhits", callback_data="qsearch:Odia Songs"),
        ],
        [
            InlineKeyboardButton("💥 Punjabi Hits", callback_data="qsearch:Punjabi Hits"),
            InlineKeyboardButton("🌍 Global Billboard", callback_data="qsearch:Billboard Hot 100"),
        ],
        [
            InlineKeyboardButton("📻 90s Golden Retro", callback_data="qsearch:90s Hindi Retro"),
            InlineKeyboardButton("🎸 Rock & Indie", callback_data="qsearch:Rock Hits"),
        ],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="cmd:start")],
    ]

    if update.callback_query:
        await safe_answer_callback(update.callback_query)
        await safe_edit_message(
            update.callback_query,
            charts_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML,
        )
    elif update.message:
        await update.message.reply_text(
            charts_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML,
        )
