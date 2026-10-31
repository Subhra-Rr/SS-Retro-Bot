"""
Administrative commands and diagnostic handlers.
"""

from __future__ import annotations

import logging
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import config
from database import db
from services.providers.registry import registry

logger = logging.getLogger(__name__)


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display administrative analytics and provider system statuses."""
    user = update.effective_user
    if not user:
        return

    # Check admin privileges
    if not config.is_admin(user.id) and config.admin_user_ids:
        await update.message.reply_text("⛔ You do not have permission to use admin commands.")
        return

    stats = await db.get_analytics_summary()

    # Check providers
    provider_lines = []
    for p in registry.get_all():
        status = "✅ ACTIVE" if p.is_configured() else "⚪ DORMANT (unconfigured)"
        full_audio = " [Full Audio Delivery]" if p.can_deliver_full_audio else " [Metadata Discovery]"
        provider_lines.append(f"• <b>{p.name}:</b> {status}{full_audio}")

    text = (
        "🛡 <b>SS Retro Bot — Administrative Dashboard</b>\n\n"
        "📊 <b>Usage Statistics:</b>\n"
        f"• <b>Registered Users:</b> {stats.get('total_users', 0):,}\n"
        f"• <b>Total Searches Executed:</b> {stats.get('total_searches', 0):,}\n"
        f"• <b>Saved Favorites:</b> {stats.get('total_favorites', 0):,}\n"
        f"• <b>Cached Telegram Audio Files:</b> {stats.get('cached_telegram_files', 0):,}\n\n"
        "🔌 <b>Provider Integrations:</b>\n"
        + "\n".join(provider_lines)
        + "\n\n"
        f"⚙ <b>Environment:</b>\n"
        f"• Max Upload Size: {config.max_file_size_mb} MB\n"
        f"• Search Timeout: {config.search_timeout_seconds}s\n"
        f"• Download Timeout: {config.download_timeout_seconds}s\n"
        f"• Database: <code>{config.database_path.name}</code>"
    )

    await update.message.reply_text(text, parse_mode=ParseMode.HTML)
