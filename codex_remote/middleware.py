# Coded by Kyreesemm (KRM Tech Software), 2026
# This is an open-source project hosted and maintained on GitHub.
# Distributed under the terms of the MIT License


# Strict Telegram user_id whitelist middleware.
# It filters incoming messages and callback queries before routing. Requests
# from unauthorized users are silently dropped without confirming the bot.
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

from .activity_log import ActivityLogger

logger = logging.getLogger(__name__)


class AdminWhitelistMiddleware(BaseMiddleware):
    def __init__(self, allowed_ids: set[int], activity_logger: ActivityLogger | None = None) -> None:
        self._allowed_ids = allowed_ids
        self._activity_logger = activity_logger

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)

        if user.id not in self._allowed_ids:
            logger.warning("Отклонён запрос от неавторизованного user_id=%s", user.id)
            return  # Silently ignore unauthorized users.

        if self._activity_logger is not None:
            text = getattr(event, "text", None) or getattr(event, "caption", None)
            callback_data = getattr(event, "data", None)
            if text:
                await self._activity_logger.event(
                    "INCOMING", text, user_id=user.id, chat_id=getattr(event.chat, "id", None)
                )
            elif callback_data:
                await self._activity_logger.event(
                    "INCOMING", f"callback: {callback_data}", user_id=user.id,
                    chat_id=getattr(event.message.chat, "id", None),
                )

        return await handler(event, data)
