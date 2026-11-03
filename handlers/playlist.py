"""
Personal Playlist Builder — /playlist command.
Subcommands: create <name>, list, add <name>, view <name>, delete <name>
"""

from __future__ import annotations

import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from database import db
from services.search_engine import search_engine
from utils.formatting import escape_html
from utils.telegram_helpers import safe_answer_callback, safe_edit_message

logger = logging.getLogger(__name__)

USAGE = (
    "<b>Playlist Commands:</b>\n"
    "• <code>/playlist create &lt;name&gt;</code> — Create a new playlist\n"
    "• <code>/playlist list</code> — View all your playlists\n"
    "• <code>/playlist view &lt;name&gt;</code> — View tracks in a playlist\n"
    "• <code>/playlist delete &lt;name&gt;</code> — Delete a playlist\n\n"
    "<i>To add a song to a playlist, use the track detail card after searching.</i>"
)


async def playlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /playlist command and its subcommands."""
    user = update.effective_user
    if not user:
        return

    args = context.args or []
    subcommand = args[0].lower() if args else ""

    # ── /playlist list ────────────────────────────────────────────────────────
    if subcommand == "list" or subcommand == "":
        playlists = await db.get_playlists(user.id)
        if not playlists:
            text = (
                "🎵 <b>Your Playlists</b>\n\n"
                "You have no playlists yet.\n\n"
                + USAGE
            )
        else:
            lines = ["🎵 <b>Your Playlists:</b>\n"]
            for idx, pl in enumerate(playlists, start=1):
                lines.append(f"  {idx}. <b>{escape_html(pl['name'])}</b>")
            lines.append(f"\n<i>Use /playlist view &lt;name&gt; to see tracks.</i>")
            text = "\n".join(lines)

        keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="cmd:start")]]
        if update.callback_query:
            await safe_answer_callback(update.callback_query)
            await safe_edit_message(
                update.callback_query, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML
            )
        elif update.message:
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return

    # ── /playlist create <name> ───────────────────────────────────────────────
    if subcommand == "create":
        name = " ".join(args[1:]).strip() if len(args) > 1 else ""
        if not name:
            await update.message.reply_text(
                "Please provide a playlist name.\n<code>/playlist create My Favourites</code>",
                parse_mode=ParseMode.HTML,
            )
            return
        playlist_id = await db.create_playlist(user.id, name)
        if playlist_id:
            await update.message.reply_text(
                f"Playlist <b>{escape_html(name)}</b> created successfully.",
                parse_mode=ParseMode.HTML,
            )
        else:
            await update.message.reply_text(
                f"A playlist named <b>{escape_html(name)}</b> already exists.",
                parse_mode=ParseMode.HTML,
            )
        return

    # ── /playlist view <name> ─────────────────────────────────────────────────
    if subcommand == "view":
        name = " ".join(args[1:]).strip() if len(args) > 1 else ""
        if not name:
            await update.message.reply_text("Usage: <code>/playlist view &lt;name&gt;</code>", parse_mode=ParseMode.HTML)
            return
        pl = await db.get_playlist_by_name(user.id, name)
        if not pl:
            await update.message.reply_text(
                f"Playlist <b>{escape_html(name)}</b> not found.", parse_mode=ParseMode.HTML
            )
            return
        tracks = await db.get_playlist_tracks(pl["id"])
        if not tracks:
            await update.message.reply_text(
                f"Playlist <b>{escape_html(name)}</b> is empty.", parse_mode=ParseMode.HTML
            )
            return
        lines = [f"🎵 <b>Playlist: {escape_html(name)}</b>\n"]
        keyboard = []
        for idx, t in enumerate(tracks, start=1):
            lines.append(f"  {idx}. {escape_html(t.get('title', 'Unknown'))} — <i>{escape_html(t.get('artist', ''))}</i>")
            if t.get("id"):
                keyboard.append([
                    InlineKeyboardButton(f"{idx}. {t.get('title', '')[:24]}", callback_data=f"track:{t['id']}")
                ])
        keyboard.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="cmd:start")])
        await update.message.reply_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML,
        )
        return

    # ── /playlist delete <name> ───────────────────────────────────────────────
    if subcommand == "delete":
        name = " ".join(args[1:]).strip() if len(args) > 1 else ""
        if not name:
            await update.message.reply_text("Usage: <code>/playlist delete &lt;name&gt;</code>", parse_mode=ParseMode.HTML)
            return
        deleted = await db.delete_playlist(user.id, name)
        if deleted:
            await update.message.reply_text(
                f"Playlist <b>{escape_html(name)}</b> deleted.", parse_mode=ParseMode.HTML
            )
        else:
            await update.message.reply_text(
                f"Playlist <b>{escape_html(name)}</b> not found.", parse_mode=ParseMode.HTML
            )
        return

    # Unknown subcommand
    await update.message.reply_text(USAGE, parse_mode=ParseMode.HTML)
