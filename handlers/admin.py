"""
Administrative commands and diagnostic handlers.
Includes user roster inspection (/users), live broadcasts (/broadcast, /announce),
and system status diagnostics (/admin).
"""

from __future__ import annotations

import datetime
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import config
from database import db
from services.providers.registry import registry
from utils.formatting import escape_html
from utils.telegram_helpers import safe_answer_callback, safe_edit_message

logger = logging.getLogger(__name__)


def _format_timestamp(ts: float | None) -> str:
    """Format unix timestamp into readable date and time."""
    if not ts:
        return "Unknown"
    dt = datetime.datetime.fromtimestamp(ts)
    return dt.strftime("%Y-%m-%d %H:%M")


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display administrative analytics and provider system statuses."""
    user = update.effective_user
    if not user:
        return

    # Check admin privileges
    if not config.is_admin(user.id):
        if update.callback_query:
            await safe_answer_callback(
                update.callback_query,
                "⛔ Access Denied: You are not authorized to use admin features.",
                show_alert=True,
            )
        elif update.message:
            await update.message.reply_text("⛔ Access Denied: You are not authorized to use admin features.")
        return

    stats = await db.get_analytics_summary()

    # Check providers
    provider_lines = []
    for p in registry.get_all():
        status = "✅ ACTIVE" if p.is_configured() else "⚪ DORMANT"
        full_audio = " [Full Audio Delivery]" if p.can_deliver_full_audio else " [Metadata Discovery]"
        provider_lines.append(f"• <b>{p.name}:</b> {status}{full_audio}")

    text = (
        "🛡 <b>SS Retro Bot — Administrative Control Panel</b>\n\n"
        "📊 <b>Platform Metrics:</b>\n"
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

    keyboard = [
        [
            InlineKeyboardButton("👥 View All Users", callback_data="adm:users"),
            InlineKeyboardButton("📢 Send Broadcast", callback_data="adm:broadcast_help"),
        ],
        [
            InlineKeyboardButton("🔄 Refresh Stats", callback_data="adm:refresh"),
            InlineKeyboardButton("🔙 Main Menu", callback_data="cmd:start"),
        ],
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


async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display all users who entered the bot or sent /start."""
    user = update.effective_user
    if not user:
        return

    if not config.is_admin(user.id):
        if update.callback_query:
            await safe_answer_callback(
                update.callback_query,
                "⛔ Access Denied: You are not authorized to use admin features.",
                show_alert=True,
            )
        elif update.message:
            await update.message.reply_text("⛔ Access Denied: You are not authorized to use admin features.")
        return

    users_list = await db.get_all_users(limit=50)
    total_count = len(users_list)

    if not users_list:
        no_users_text = "👥 <b>Registered Users:</b>\n\nNo users have entered the bot yet."
        if update.callback_query:
            await safe_answer_callback(update.callback_query)
            await safe_edit_message(update.callback_query, no_users_text, parse_mode=ParseMode.HTML)
        elif update.message:
            await update.message.reply_text(no_users_text, parse_mode=ParseMode.HTML)
        return

    lines = [f"👥 <b>Registered Users (Recent {total_count} shown):</b>\n"]
    for idx, u in enumerate(users_list, start=1):
        uid = u.get("user_id")
        username = u.get("username")
        fname = u.get("first_name") or "User"
        lname = u.get("last_name") or ""
        full_name = f"{fname} {lname}".strip()
        user_handle = f"@{username}" if username else "No username"
        is_admin_flag = " 👑 <i>Admin</i>" if config.is_admin(uid) else ""

        joined_str = _format_timestamp(u.get("created_at"))
        active_str = _format_timestamp(u.get("last_active"))

        lines.append(
            f"<b>{idx}.</b> <b>{escape_html(full_name)}</b> ({escape_html(user_handle)}){is_admin_flag}\n"
            f"   • <b>ID:</b> <code>{uid}</code>\n"
            f"   • <b>Joined:</b> {joined_str} | <b>Active:</b> {active_str}\n"
        )

    keyboard = [
        [InlineKeyboardButton("📢 Broadcast Announcement", callback_data="adm:broadcast_help")],
        [InlineKeyboardButton("🔙 Back to Admin", callback_data="adm:refresh")],
    ]

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


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Broadcast an official announcement from admin to all registered bot users."""
    user = update.effective_user
    if not user:
        return

    if not config.is_admin(user.id):
        await update.message.reply_text("⛔ Access Denied: You are not authorized to use admin features.")
        return

    if not context.args:
        guide = (
            "📢 <b>Admin Announcement / Broadcast System</b>\n\n"
            "To send an announcement to all registered users, run:\n"
            "<code>/broadcast &lt;your announcement message&gt;</code>\n"
            "or\n"
            "<code>/announce &lt;your announcement message&gt;</code>\n\n"
            "<i>Example:</i>\n"
            "<code>/broadcast Server update complete! Unlimited high-speed audio downloads are active.</code>\n\n"
            "All users will receive the message styled as:\n"
            "📢 <b>Announcement from Admin:</b>\n"
            "&lt;your message&gt;"
        )
        await update.message.reply_text(guide, parse_mode=ParseMode.HTML)
        return

    raw_text = update.message.text or ""
    parts = raw_text.split(maxsplit=1)
    announcement_text = parts[1].strip() if len(parts) > 1 else " ".join(context.args).strip()
    user_ids = await db.get_all_user_ids()

    if not user_ids:
        await update.message.reply_text("❌ No registered users found in the database.")
        return

    status_msg = await update.message.reply_text(
        f"⏳ <i>Broadcasting announcement to {len(user_ids)} users...</i>",
        parse_mode=ParseMode.HTML,
    )

    formatted_message = (
        "📢 <b>Announcement from Admin:</b>\n\n"
        f"{escape_html(announcement_text)}"
    )

    success_count = 0
    failed_count = 0

    for uid in user_ids:
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=formatted_message,
                parse_mode=ParseMode.HTML,
            )
            success_count += 1
        except Exception as e:
            failed_count += 1
            logger.warning("Could not deliver broadcast to user %s: %s", uid, e)

    await safe_edit_message(
        status_msg,
        f"✅ <b>Broadcast Announcement Complete!</b>\n\n"
        f"• <b>Delivered to:</b> {success_count} user(s)\n"
        f"• <b>Failed/Blocked:</b> {failed_count} user(s)\n"
        f"• <b>Message sent:</b>\n<i>{escape_html(announcement_text)}</i>",
        parse_mode=ParseMode.HTML,
    )
