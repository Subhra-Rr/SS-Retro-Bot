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
from handlers.admin import admin_command, users_command
from handlers.commands import (
    charts_command,
    favorites_command,
    help_command,
    recent_command,
    settings_command,
    top_command,
)
from handlers.music import build_search_view, execute_search
from services.audio_effects import EFFECT_PRESETS, audio_effects_engine
from services.downloader import audio_downloader
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


async def _execute_download_and_delivery(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    track_id: str,
    quality: str = "320",
    output_format: str = "mp3",
) -> None:
    """Execute audio download and delivery workflow with live progress meter and recommendations."""
    user = query.from_user
    chat = query.message.chat if query.message else None
    if not user or not chat:
        return

    await safe_answer_callback(query, f"Preparing {output_format.upper()} audio download...")

    track: UnifiedTrack | None = None
    if context.user_data and f"selected:{track_id}" in context.user_data:
        track = UnifiedTrack.from_dict(context.user_data[f"selected:{track_id}"])
    else:
        track = await search_engine.get_track_by_id(track_id)

    if not track:
        try:
            recent = await db.get_recent_tracks(user.id, limit=30)
            for r in recent:
                if str(r.get("id")) == str(track_id):
                    track = UnifiedTrack.from_dict(r)
                    break
        except Exception:
            pass

    if not track:
        await safe_answer_callback(query, "Could not find track information. Please try searching again.", show_alert=True)
        return

    if track.duration and track.duration > 7200:
        dur_mins = track.duration // 60
        await safe_answer_callback(
            query,
            f"This compilation is {dur_mins}m long and exceeds Telegram's 50MB file size limit. Please select an individual song.",
            show_alert=True,
        )
        return

    status_msg = None
    try:
        status_msg = await context.bot.send_message(
            chat_id=chat.id,
            text=(
                f"⏬ <i>Starting download for '<b>{escape_html(track.title)}</b>'...</i>\n"
                f"[▱▱▱▱▱▱▱▱▱▱] <b>0%</b> • [{quality} kbps {output_format.upper()}]"
            ),
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        logger.warning("Could not send download status message: %s", e)

    try:
        # Match stream on YouTube if needed
        if not track.has_full_audio or not track.audio_url:
            try:
                yt_provider = registry.get_provider("YouTube Music")
                if yt_provider:
                    cleaned_title = _clean_track_title(track.title)
                    search_term = f"{cleaned_title} {track.artist}".strip()
                    yt_results = await yt_provider.search(search_term, limit=3)
                    if not yt_results and cleaned_title != track.title:
                        yt_results = await yt_provider.search(cleaned_title, limit=3)

                    if yt_results:
                        matched = yt_results[0]
                        track.audio_url = matched.audio_url
                        track.has_full_audio = True
                        track.allow_redistribution = True
                        track.provider_name = "YouTube Music"
                        if matched.duration:
                            track.duration = matched.duration
            except Exception as ex:
                logger.warning("Could not match stream on YouTube for %s: %s", track.title, ex)

        if not track.has_full_audio or not track.audio_url:
            if status_msg:
                await safe_edit_message(
                    status_msg,
                    f"❌ Could not find a stream for '<b>{escape_html(track.title)}</b>'. "
                    "Try typing the song name directly to download from YouTube.",
                    parse_mode=ParseMode.HTML,
                )
            return

        success, err, msg = await audio_delivery.deliver_track(
            bot=context.bot,
            chat_id=chat.id,
            track=track,
            user_id=user.id,
            status_msg=status_msg,
            quality=quality,
            output_format=output_format,
        )

        if success and msg:
            if status_msg:
                await safe_delete_message(status_msg)

            # Feature 6: Similar Songs / Recommendations
            try:
                similar_tracks = await search_engine.search(track.artist, user_id=user.id, max_total_results=4)
                rec_keyboard = []
                for st in similar_tracks:
                    if st.id != track.id:
                        rec_keyboard.append([
                            InlineKeyboardButton(f"🎵 {st.title[:24]}", callback_data=f"track:{st.id}")
                        ])
                rec_keyboard.append([
                    InlineKeyboardButton("🏷️ Edit ID3 Tags", callback_data=f"tagedit_menu:{track.id}"),
                    InlineKeyboardButton("➕ Add to Playlist", callback_data=f"pl_choose:{track.id}"),
                ])
                rec_text = f"✨ <b>More tracks by {escape_html(track.artist)}:</b>"
                await context.bot.send_message(
                    chat_id=chat.id,
                    text=rec_text,
                    reply_markup=InlineKeyboardMarkup(rec_keyboard),
                    parse_mode=ParseMode.HTML,
                )
            except Exception as rec_err:
                logger.debug("Failed sending recommendations: %s", rec_err)
        else:
            err_text = err or "Could not retrieve audio stream from provider."
            if status_msg:
                await safe_edit_message(
                    status_msg,
                    f"⚠️ <b>Delivery notice for '<i>{escape_html(track.title)}</i>':</b>\n\n"
                    f"<i>{escape_html(err_text)}</i>\n\n"
                    f"💡 <i>Tip: Try searching with another keyword or selecting a different result.</i>",
                    parse_mode=ParseMode.HTML,
                )
            else:
                await context.bot.send_message(
                    chat_id=chat.id,
                    text=f"⚠️ <b>Delivery notice:</b> {escape_html(err_text)}",
                    parse_mode=ParseMode.HTML,
                )
    except Exception as e:
        logger.error("Download handling exception for %s: %s", track_id, e, exc_info=True)
        if status_msg:
            await safe_edit_message(
                status_msg,
                f"⚠️ <b>Download notice:</b> {escape_html(str(e))}\n\n"
                f"Please try again shortly or select another track.",
                parse_mode=ParseMode.HTML,
            )


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
                    InlineKeyboardButton("📈 Charts", callback_data="cmd:charts"),
                ],
                [
                    InlineKeyboardButton("⭐ Favorites", callback_data="cmd:favorites"),
                    InlineKeyboardButton("🕒 Recent", callback_data="cmd:recent"),
                    InlineKeyboardButton("📁 Playlists", callback_data="cmd:playlist"),
                    InlineKeyboardButton("📊 Stats", callback_data="cmd:stats"),
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
        elif cmd == "charts":
            await charts_command(update, context)
        elif cmd == "recent":
            await recent_command(update, context)
        elif cmd == "favorites":
            await favorites_command(update, context)
        elif cmd == "playlist":
            from handlers.playlist import playlist_command
            await playlist_command(update, context)
        elif cmd == "stats":
            from handlers.stats import stats_command
            await stats_command(update, context)
        elif cmd == "settings":
            await settings_command(update, context)
        elif cmd == "admin":
            await admin_command(update, context)
        return

    # Admin sub-actions (adm:users, adm:broadcast_help, adm:refresh)
    if data.startswith("adm:"):
        sub = data.split(":", 1)[1]
        if sub == "users":
            await users_command(update, context)
        elif sub == "refresh":
            await admin_command(update, context)
        elif sub == "broadcast_help":
            guide = (
                "📢 <b>Admin Announcement / Broadcast System</b>\n\n"
                "To send an announcement to all registered users who entered the bot, run:\n\n"
                "<code>/broadcast &lt;your message&gt;</code>\n\n"
                "<i>Example:</i>\n"
                "<code>/broadcast Server update complete! Fast audio downloads are active.</code>"
            )
            await safe_answer_callback(query)
            await safe_edit_message(
                query,
                guide,
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🔙 Back to Admin", callback_data="adm:refresh")]]
                ),
                parse_mode=ParseMode.HTML,
            )
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

        if track.duration and track.duration > 7200:
            card_lines.append(f"\n⚠️ <b>Continuous compilation ({duration_str}). Exceeds Telegram's 50MB bot limit.</b>")
        else:
            card_lines.append("\n✅ <b>Complete audio track is ready for download.</b>")
        keyboard = [
            [
                InlineKeyboardButton("📥 Download (Fast)", callback_data=f"dl:{track.id}"),
                InlineKeyboardButton("🎚️ Quality Selector", callback_data=f"dl_menu:{track.id}"),
            ],
            [
                InlineKeyboardButton("🎛️ Audio FX Studio", callback_data=f"fx_menu:{track.id}"),
                InlineKeyboardButton("➕ Add to Playlist", callback_data=f"pl_choose:{track.id}"),
            ],
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

    # Audio Quality & Format Selector menu (Feature 1 & Feature 12)
    if data.startswith("dl_menu:"):
        track_id = data.split(":", 1)[1]
        await safe_answer_callback(query, "Loading Quality Selector...")
        text = (
            "🎚️ <b>Audio Quality & Export Format Selector</b>\n\n"
            "Select your desired bitrate or lossless export format:"
        )
        keyboard = [
            [
                InlineKeyboardButton("💎 320 kbps (Studio MP3)", callback_data=f"dlq:320:mp3:{track_id}"),
                InlineKeyboardButton("🎧 192 kbps (Standard)", callback_data=f"dlq:192:mp3:{track_id}"),
            ],
            [
                InlineKeyboardButton("📻 128 kbps (Compact)", callback_data=f"dlq:128:mp3:{track_id}"),
                InlineKeyboardButton("⚡ 64 kbps (Data Saver)", callback_data=f"dlq:64:mp3:{track_id}"),
            ],
            [
                InlineKeyboardButton("🎵 Lossless FLAC", callback_data=f"dlq:0:flac:{track_id}"),
                InlineKeyboardButton("🎼 Lossless WAV", callback_data=f"dlq:0:wav:{track_id}"),
            ],
            [InlineKeyboardButton("🔙 Back to Track", callback_data=f"track:{track_id}")],
        ]
        await safe_edit_message(query, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return

    # Specific bitrate & format download: dlq:{quality}:{format}:{track_id}
    if data.startswith("dlq:"):
        parts = data.split(":", 3)
        if len(parts) == 4:
            quality, out_fmt, track_id = parts[1], parts[2], parts[3]
            await _execute_download_and_delivery(query, context, track_id, quality=quality, output_format=out_fmt)
        return

    # Default audio download & delivery: dl:{track_id}
    if data.startswith("dl:"):
        track_id = data.split(":", 1)[1]
        settings = await db.get_user_settings(user.id)
        user_q = settings.get("audio_quality", "320")
        if user_q in ("flac", "wav"):
            fmt = user_q
            q = "0"
        else:
            fmt = "mp3"
            q = user_q if user_q in ("64", "128", "192", "320") else "320"
        await _execute_download_and_delivery(query, context, track_id, quality=q, output_format=fmt)
        return

    # Personal Playlist Selector: pl_choose:{track_id} (Feature 13)
    if data.startswith("pl_choose:"):
        track_id = data.split(":", 1)[1]
        playlists = await db.get_playlists(user.id)
        if not playlists:
            await safe_answer_callback(
                query, "No playlists created yet. Create one with: /playlist create <name>", show_alert=True
            )
            return
        keyboard = []
        for pl in playlists:
            keyboard.append([
                InlineKeyboardButton(f"📁 {pl['name']}", callback_data=f"pl_add:{pl['id']}:{track_id}")
            ])
        keyboard.append([InlineKeyboardButton("🔙 Back to Track", callback_data=f"track:{track_id}")])
        await safe_edit_message(
            query,
            "➕ <b>Select a playlist to add this track:</b>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML,
        )
        return

    # Add track to playlist: pl_add:{playlist_id}:{track_id}
    if data.startswith("pl_add:"):
        parts = data.split(":")
        if len(parts) == 3:
            playlist_id = int(parts[1])
            track_id = parts[2]
            track = await search_engine.get_track_by_id(track_id)
            if not track and context.user_data and f"selected:{track_id}" in context.user_data:
                track = UnifiedTrack.from_dict(context.user_data[f"selected:{track_id}"])
            if track:
                added = await db.add_track_to_playlist(playlist_id, track.to_dict())
                if added:
                    await safe_answer_callback(query, "✅ Track added to playlist!", show_alert=False)
                else:
                    await safe_answer_callback(query, "Track is already in this playlist.", show_alert=False)
            await safe_answer_callback(query)
        return

    # Batch / Queue Download mode: batch_init:{query_hash} (Feature 2)
    if data.startswith("batch_init:"):
        query_hash = data.split(":", 1)[1]
        cached = context.user_data.get(f"search:{query_hash}") if context.user_data else None
        if not cached:
            await safe_answer_callback(query, "Search session expired. Please search again.", show_alert=True)
            return
        context.user_data[f"batch_sel:{query_hash}"] = set()
        tracks = cached.get("tracks", [])
        lines = [
            "📦 <b>Batch Download Selection</b>\n",
            "Select songs to download in queue:\n",
        ]
        keyboard = []
        for idx, t in enumerate(tracks[:6]):
            keyboard.append([
                InlineKeyboardButton(f"⬜ {idx+1}. {t.get('title', '')[:24]}", callback_data=f"batch_toggle:{query_hash}:{idx}")
            ])
        keyboard.append([
            InlineKeyboardButton("🚀 Download Selected (0)", callback_data=f"batch_download:{query_hash}"),
            InlineKeyboardButton("🔙 Back to Search", callback_data=f"page:{query_hash}:1"),
        ])
        await safe_answer_callback(query)
        await safe_edit_message(query, "\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return

    # Batch toggle: batch_toggle:{query_hash}:{idx}
    if data.startswith("batch_toggle:"):
        parts = data.split(":")
        if len(parts) == 3:
            query_hash, idx_str = parts[1], parts[2]
            idx = int(idx_str)
            cached = context.user_data.get(f"search:{query_hash}") if context.user_data else None
            selected = context.user_data.get(f"batch_sel:{query_hash}", set())
            if idx in selected:
                selected.remove(idx)
            else:
                selected.add(idx)
            context.user_data[f"batch_sel:{query_hash}"] = selected
            tracks = cached.get("tracks", []) if cached else []
            lines = [
                "📦 <b>Batch Download Selection</b>\n",
                f"Selected: <b>{len(selected)}</b> song(s)\n",
            ]
            keyboard = []
            for i, t in enumerate(tracks[:6]):
                mark = "☑️" if i in selected else "⬜"
                keyboard.append([
                    InlineKeyboardButton(f"{mark} {i+1}. {t.get('title', '')[:24]}", callback_data=f"batch_toggle:{query_hash}:{i}")
                ])
            keyboard.append([
                InlineKeyboardButton(f"🚀 Download Selected ({len(selected)})", callback_data=f"batch_download:{query_hash}"),
                InlineKeyboardButton("🔙 Back to Search", callback_data=f"page:{query_hash}:1"),
            ])
            await safe_answer_callback(query)
            await safe_edit_message(query, "\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return

    # Execute batch queue download: batch_download:{query_hash}
    if data.startswith("batch_download:"):
        query_hash = data.split(":", 1)[1]
        cached = context.user_data.get(f"search:{query_hash}") if context.user_data else None
        selected = context.user_data.get(f"batch_sel:{query_hash}", set()) if context.user_data else set()
        if not selected:
            await safe_answer_callback(query, "Please select at least one song first!", show_alert=True)
            return
        tracks = cached.get("tracks", []) if cached else []
        selected_tracks = [tracks[i] for i in sorted(selected) if i < len(tracks)]
        await safe_answer_callback(query, f"Starting download of {len(selected_tracks)} song(s)...")
        status_msg = await context.bot.send_message(
            chat_id=chat.id,
            text=f"📦 <i>Starting queue download of <b>{len(selected_tracks)}</b> track(s)...</i>",
            parse_mode=ParseMode.HTML,
        )
        for num, t_dict in enumerate(selected_tracks, start=1):
            t = UnifiedTrack.from_dict(t_dict)
            await safe_edit_message(
                status_msg,
                f"📦 <i>Downloading track {num} of {len(selected_tracks)}: '<b>{escape_html(t.title)}</b>'...</i>",
                parse_mode=ParseMode.HTML,
            )
            if not t.has_full_audio or not t.audio_url:
                try:
                    yt_p = registry.get_provider("YouTube Music")
                    if yt_p:
                        cl_t = _clean_track_title(t.title)
                        yt_res = await yt_p.search(f"{cl_t} {t.artist}".strip(), limit=2)
                        if yt_res:
                            t.audio_url = yt_res[0].audio_url
                            t.has_full_audio = True
                            t.provider_name = "YouTube Music"
                            if yt_res[0].duration:
                                t.duration = yt_res[0].duration
                except Exception:
                    pass
            if t.has_full_audio and t.audio_url:
                await audio_delivery.deliver_track(
                    bot=context.bot,
                    chat_id=chat.id,
                    track=t,
                    user_id=user.id,
                )
        await safe_edit_message(
            status_msg,
            f"✅ <b>Batch Download Completed!</b> Delivered {len(selected_tracks)} track(s) successfully.",
            parse_mode=ParseMode.HTML,
        )
        return

    # ID3 Tag Editor Instructions (Feature 10)
    if data.startswith("tagedit_menu:"):
        track_id = data.split(":", 1)[1]
        text = (
            "🏷️ <b>MP3 ID3 Tag Editor</b>\n\n"
            "To customize or correct metadata tags, ID3 tags are automatically formatted "
            "with Title, Artist, Album, and embedded Cover Art.\n"
            "You can also customize your preferred file naming scheme in <code>/settings</code>!"
        )
        keyboard = [[InlineKeyboardButton("🔙 Back to Track", callback_data=f"track:{track_id}")]]
        await safe_answer_callback(query)
        await safe_edit_message(query, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return

    # Audio FX Studio menu: fx_menu:{track_id}
    if data.startswith("fx_menu:"):
        track_id = data.split(":", 1)[1]
        await safe_answer_callback(query, "Loading Audio FX Studio...")

        track: UnifiedTrack | None = None
        if context.user_data and f"selected:{track_id}" in context.user_data:
            track = UnifiedTrack.from_dict(context.user_data[f"selected:{track_id}"])
        else:
            track = await search_engine.get_track_by_id(track_id)

        if not track:
            await safe_answer_callback(query, "Could not locate track.", show_alert=True)
            return

        fx_text = (
            f"🎛️ <b>Audio FX Studio — Studio Acoustic Enhancements</b>\n\n"
            f"🎵 <b>{escape_html(track.title)}</b>\n"
            f"👤 <i>{escape_html(track.artist)}</i>\n\n"
            f"Choose an acoustic modification to render:\n"
            f"• 🌙 <b>Slowed + Reverb</b>: Chill tempo with atmospheric reverb\n"
            f"• 🔊 <b>Bass Boosted</b>: +8dB punchy sub-bass for car/headphones\n"
            f"• 🎧 <b>8D Surround</b>: 360° binaural audio rotating between ears\n"
            f"• ⚡ <b>Nightcore</b>: Fast-tempo (+125%) energetic party pitch"
        )
        keyboard = [
            [
                InlineKeyboardButton("🌙 Slowed + Reverb", callback_data=f"fx_apply:slowed:{track.id}"),
                InlineKeyboardButton("🔊 Bass Boost (+8dB)", callback_data=f"fx_apply:bass:{track.id}"),
            ],
            [
                InlineKeyboardButton("🎧 8D Surround Audio", callback_data=f"fx_apply:8d:{track.id}"),
                InlineKeyboardButton("⚡ Nightcore (Speed Up)", callback_data=f"fx_apply:nightcore:{track.id}"),
            ],
            [InlineKeyboardButton("🔙 Back to Track", callback_data=f"track:{track.id}")],
        ]
        await safe_edit_message(
            query,
            fx_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML,
        )
        return

    # Audio FX Processing & Delivery: fx_apply:{effect_type}:{track_id}
    if data.startswith("fx_apply:"):
        parts = data.split(":", 2)
        if len(parts) != 3:
            return
        effect_type, track_id = parts[1], parts[2]
        preset = EFFECT_PRESETS.get(effect_type)
        if not preset:
            await safe_answer_callback(query, "Unknown effect preset.", show_alert=True)
            return

        await safe_answer_callback(query, f"Applying {preset['name']}...")

        track = None
        if context.user_data and f"selected:{track_id}" in context.user_data:
            track = UnifiedTrack.from_dict(context.user_data[f"selected:{track_id}"])
        else:
            track = await search_engine.get_track_by_id(track_id)

        if not track:
            await safe_answer_callback(query, "Could not find track information.", show_alert=True)
            return

        # Check cached Telegram file_id
        cache_provider_key = f"AudioFX:{effect_type}"
        cached_file_id = await db.get_cached_telegram_file_id(track.id, cache_provider_key)
        if cached_file_id:
            caption = (
                f"{preset['emoji']} <b>{escape_html(track.title)}</b> <i>{preset['tag_suffix']}</i>\n"
                f"👤 <b>Artist:</b> {escape_html(track.artist)}\n"
                f"🎛️ <b>Effect:</b> <code>{preset['name']}</code> • 💎 <i>320 kbps Studio FX</i>"
            )
            try:
                await context.bot.send_audio(
                    chat_id=chat.id,
                    audio=cached_file_id,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    title=f"{track.title} {preset['tag_suffix']}",
                    performer=track.artist,
                )
                return
            except Exception as e:
                logger.debug("Failed sending cached effect audio: %s", e)

        status_msg = await context.bot.send_message(
            chat_id=chat.id,
            text=(
                f"🎛️ <i>Applying <b>{preset['name']}</b> studio filter...</i>\n"
                f"⚡ ▰▰▰▰▰▰▰▰▱▱ <b>80%</b> • [320 KBPS • FFmpeg Studio]"
            ),
            parse_mode=ParseMode.HTML,
        )

        try:
            # If track has no direct full audio (e.g. from iTunes/Spotify), find matching stream on YouTube
            if not track.has_full_audio or not track.audio_url:
                try:
                    yt_provider = registry.get_provider("YouTube Music")
                    if yt_provider:
                        cleaned_title = _clean_track_title(track.title)
                        search_term = f"{cleaned_title} {track.artist}".strip()
                        yt_results = await yt_provider.search(search_term, limit=3)
                        if not yt_results and cleaned_title != track.title:
                            yt_results = await yt_provider.search(cleaned_title, limit=3)

                        if yt_results:
                            matched = yt_results[0]
                            track.audio_url = matched.audio_url
                            track.has_full_audio = True
                            track.allow_redistribution = True
                            track.provider_name = "YouTube Music"
                            if matched.duration:
                                track.duration = matched.duration
                except Exception as ex:
                    logger.warning("Could not match stream on YouTube for %s: %s", track.title, ex)

            # Download base track
            base_file, dl_err = await audio_downloader.download_track(track)
            if not base_file:
                if status_msg:
                    await safe_edit_message(
                        status_msg,
                        f"❌ <b>Effect notice:</b> {escape_html(dl_err or 'Could not download source track.')}\n\n"
                        f"💡 <i>Tip: Try another song or standard download.</i>",
                        parse_mode=ParseMode.HTML,
                    )
                return

            # Apply FFmpeg filter
            fx_file, fx_err = await audio_effects_engine.apply_effect(base_file, effect_type, track)
            audio_downloader.cleanup(base_file)

            if not fx_file:
                if status_msg:
                    await safe_edit_message(
                        status_msg,
                        f"❌ <b>Effect error:</b> {escape_html(fx_err or 'Audio processing failed.')}",
                        parse_mode=ParseMode.HTML,
                    )
                return

            caption = (
                f"{preset['emoji']} <b>{escape_html(track.title)}</b> <i>{preset['tag_suffix']}</i>\n"
                f"👤 <b>Artist:</b> {escape_html(track.artist)}\n"
                f"🎛️ <b>Effect:</b> <code>{preset['name']}</code> • 💎 <i>320 kbps Studio FX</i>"
            )
            safe_name = f"{track.artist} - {track.title} {preset['tag_suffix']}.mp3"

            try:
                with open(fx_file, "rb") as af:
                    msg = await context.bot.send_audio(
                        chat_id=chat.id,
                        audio=af,
                        caption=caption,
                        parse_mode=ParseMode.HTML,
                        title=f"{track.title} {preset['tag_suffix']}",
                        performer=track.artist,
                        filename=safe_name,
                        write_timeout=300.0,
                        read_timeout=120.0,
                    )
                if msg and msg.audio and msg.audio.file_id:
                    await db.cache_telegram_file_id(
                        track_id=track.id,
                        provider_name=cache_provider_key,
                        telegram_file_id=msg.audio.file_id,
                        file_size=fx_file.stat().st_size if fx_file.exists() else 0,
                    )
                if status_msg:
                    await safe_delete_message(status_msg)
            finally:
                audio_effects_engine.cleanup(fx_file)

        except Exception as e:
            logger.error("Audio FX exception: %s", e)
            if status_msg:
                await safe_edit_message(
                    status_msg,
                    f"❌ <b>Effect notice:</b> {escape_html(str(e))}",
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
            elif key == "quality":
                await db.update_user_setting(user.id, "audio_quality", val)
                await safe_answer_callback(query, f"Default audio quality set to {val}!")
            elif key == "fn":
                await db.update_user_setting(user.id, "filename_format", val)
                await safe_answer_callback(query, f"Filename format set to {val}!")
            await settings_command(update, context)
        return
