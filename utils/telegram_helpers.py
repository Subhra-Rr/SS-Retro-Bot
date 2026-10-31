"""
Safe Telegram API interaction utilities.
Prevents unhandled Telegram exceptions (Message Not Modified, Query Too Old, Stale IDs, etc.)
from disrupting bot operation or polluting logs.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from telegram import CallbackQuery, InlineKeyboardMarkup, Message
from telegram.constants import ParseMode
from telegram.error import BadRequest, NetworkError, TelegramError, TimedOut

logger = logging.getLogger(__name__)


async def safe_answer_callback(
    query: Optional[CallbackQuery],
    text: Optional[str] = None,
    show_alert: bool = False,
    **kwargs: Any,
) -> bool:
    """
    Safely acknowledge a Telegram CallbackQuery without raising on stale or expired queries.
    Returns True if successfully answered, False otherwise.
    """
    if not query:
        return False

    try:
        await query.answer(text=text, show_alert=show_alert, **kwargs)
        return True
    except BadRequest as e:
        err_msg = str(e).lower()
        if "query is too old" in err_msg or "query id is invalid" in err_msg:
            logger.debug("safe_answer_callback ignored stale query: %s", e)
            return False
        logger.warning("safe_answer_callback caught BadRequest: %s", e)
        return False
    except Exception as e:
        logger.debug("safe_answer_callback caught non-fatal exception: %s", e)
        return False


async def safe_edit_message(
    target: Any,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    parse_mode: Optional[str] = ParseMode.HTML,
    **kwargs: Any,
) -> bool:
    """
    Safely edit message text across CallbackQuery, Message, or Update targets.
    Silently ignores Telegram 'Message is not modified' error when user clicks the same view twice.
    Returns True if successfully edited, False otherwise.
    """
    if not target:
        return False

    try:
        if hasattr(target, "edit_message_text"):
            await target.edit_message_text(
                text, reply_markup=reply_markup, parse_mode=parse_mode, **kwargs
            )
            return True
        elif hasattr(target, "edit_text"):
            await target.edit_text(
                text, reply_markup=reply_markup, parse_mode=parse_mode, **kwargs
            )
            return True
        return False
    except BadRequest as e:
        err_msg = str(e).lower()
        if "not modified" in err_msg:
            logger.debug("safe_edit_message ignored 'message not modified'")
            return True
        if "query is too old" in err_msg or "message to edit not found" in err_msg:
            logger.debug("safe_edit_message ignored stale edit: %s", e)
            return False
        logger.warning("safe_edit_message BadRequest: %s", e)
        return False
    except Exception as e:
        logger.debug("safe_edit_message non-fatal exception: %s", e)
        return False


async def safe_delete_message(message: Optional[Message]) -> bool:
    """Safely delete a Telegram message ignoring already-deleted or missing messages."""
    if not message:
        return False
    try:
        await message.delete()
        return True
    except Exception as e:
        logger.debug("safe_delete_message ignored exception: %s", e)
        return False
