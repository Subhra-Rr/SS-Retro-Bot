"""
Download statistics handler for /stats command.
Shows total downloads, weekly/monthly counts, top artist, and recent tracks.
"""

from __future__ import annotations

import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from database import db
from utils.formatting import escape_html
from utils.telegram_helpers import safe_answer_callback, safe_edit_message

logger = logging.getLogger(__name__)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /stats command — show user download statistics."""
    user = update.effective_user
    if not user:
        return

    if update.callback_query:
        await safe_answer_callback(update.callback_query)

    stats = await db.get_user_download_stats(user.id)

    total = stats.get("total", 0)
    this_week = stats.get("this_week", 0)
    this_month = stats.get("this_month", 0)
    top_artist = stats.get("top_artist")
    recent = stats.get("recent", [])

    lines = [
        "📊 <b>Your Download Statistics</b>\n",
        f"• <b>Total Downloads:</b> <code>{total}</code>",
        f"• <b>This Week:</b> <code>{this_week}</code>",
        f"• <b>This Month:</b> <code>{this_month}</code>",
    ]

    if top_artist:
        lines.append(f"• <b>Favourite Artist:</b> {escape_html(top_artist)}")

    if recent:
        lines.append("\n<b>Recently Downloaded:</b>")
        for idx, r in enumerate(recent, start=1):
            lines.append(f"  {idx}. {escape_html(r.get('title', 'Unknown'))} — <i>{escape_html(r.get('artist', ''))}</i>")

    if total == 0:
        lines.append("\n<i>You haven't downloaded any tracks yet. Search for a song and download it!</i>")

    text = "\n".join(lines)
    keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="cmd:start")]]

    if update.callback_query:
        await safe_edit_message(
            update.callback_query,
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML,
        )
    elif update.message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
