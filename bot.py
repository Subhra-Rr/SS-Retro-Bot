"""
SS Retro Bot — Main Application Entrypoint.
Initializes configuration, database, providers, handlers, and starts Telegram polling.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from telegram import Update
from telegram.error import BadRequest, Conflict, NetworkError, TelegramError, TimedOut
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    InlineQueryHandler,
    MessageHandler,
    filters,
)

from config import BASE_DIR, config
from database import db
from handlers import (
    admin_command,
    album_command,
    artist_command,
    favorites_command,
    handle_callback_query,
    help_command,
    inline_query_handler,
    recent_command,
    search_command,
    settings_command,
    song_command,
    start_command,
    text_search_handler,
    top_command,
)
from services.providers.registry import registry
from utils.logging_config import setup_logging

logger = logging.getLogger("ss_retro_bot")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler capturing unexpected Telegram exceptions."""
    err = context.error
    if not err:
        return

    err_str = str(err).lower()
    # Suppress non-fatal or expected Telegram UI conditions and timeout acknowledgments
    if (
        isinstance(err, (BadRequest, TimedOut, NetworkError, Conflict))
        or ("time" in err_str and "out" in err_str)
    ):
        if (
            "not modified" in err_str
            or "query is too old" in err_str
            or "message to edit not found" in err_str
            or "timed out" in err_str
            or "timeout" in err_str
            or "chat not found" in err_str
            or "conflict" in err_str
        ):
            logger.debug("Suppressed transient Telegram update error: %s", err)
            return

    logger.error("Exception while handling an update: %s", err, exc_info=err)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ Could not complete this request. Please try again shortly."
            )
        except Exception:
            pass


def build_application() -> Application:
    """Construct and configure the Telegram Application instance with extended timeouts for audio uploads."""
    app = (
        Application.builder()
        .token(config.telegram_bot_token)
        .read_timeout(60.0)
        .write_timeout(180.0)
        .connect_timeout(30.0)
        .pool_timeout(60.0)
        .build()
    )

    # Command handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("song", song_command))
    app.add_handler(CommandHandler("artist", artist_command))
    app.add_handler(CommandHandler("album", album_command))
    app.add_handler(CommandHandler("top", top_command))
    app.add_handler(CommandHandler("recent", recent_command))
    app.add_handler(CommandHandler("favorites", favorites_command))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CommandHandler("admin", admin_command))

    # Interactive callback query router
    app.add_handler(CallbackQueryHandler(handle_callback_query))

    # Inline mode search
    app.add_handler(InlineQueryHandler(inline_query_handler))

    # Natural conversation / plain text search handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_search_handler))

    # Error handling
    app.add_error_handler(error_handler)

    return app


async def startup_check() -> bool:
    """Validate database, directories, and provider status."""
    logger.info("Initializing database...")
    await db.init_db()

    configured = registry.get_configured()
    logger.info(
        "Active providers (%d): %s",
        len(configured),
        ", ".join(p.name for p in configured),
    )
    audio_providers = registry.get_audio_providers()
    logger.info(
        "Full audio delivery providers (%d): %s",
        len(audio_providers),
        ", ".join(p.name for p in audio_providers),
    )
    return True


def main() -> None:
    """Application main runner."""
    parser = argparse.ArgumentParser(description="SS Retro Bot — Telegram Music Search & Delivery")
    parser.add_argument("--check", action="store_true", help="Run startup and configuration checks without starting polling")
    args = parser.parse_args()

    # Initialize logging
    setup_logging(config.log_level, log_file_path=BASE_DIR / "data" / "bot.log")
    logger.info("Starting SS Retro Bot...")

    # Run startup verification
    asyncio.run(startup_check())

    if args.check:
        is_valid, errors = config.validate(allow_placeholder_token=True)
        print("\n" + "=" * 60)
        print("SS RETRO BOT — STARTUP VERIFICATION COMPLETED")
        print("=" * 60)
        print(f"Database:       {config.database_path} [OK]")
        print(f"Download Dir:   {config.temp_download_dir} [OK]")
        print(f"Max File Size:  {config.max_file_size_mb} MB")
        print(f"Configured:     {len(registry.get_configured())} provider(s)")
        for p in registry.get_all():
            st = "ACTIVE" if p.is_configured() else "DORMANT (unconfigured)"
            print(f"  • {p.name:<25} [{st}]")
        print("=" * 60 + "\n")
        return

    # Validate token for live polling
    is_valid, errors = config.validate()
    if not is_valid:
        logger.critical("Configuration validation failed:")
        for err in errors:
            logger.critical("  • %s", err)
        print("\n❌ Configuration Error:")
        for err in errors:
            print(f"  • {err}")
        print("\nPlease update your .env file with a valid TELEGRAM_BOT_TOKEN from @BotFather.\n")
        sys.exit(1)

    logger.info("Configuration validated. Initializing Telegram application...")
    app = build_application()

    logger.info("Starting Telegram polling. Press Ctrl+C to terminate.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
