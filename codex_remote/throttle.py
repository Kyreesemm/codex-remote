# Coded by Kyreesemm (KRM Tech Software), 2026
# This is an open-source project hosted and maintained on GitHub.
# Distributed under the terms of the MIT License


# Batch PTY output to avoid flooding the Telegram Bot API.
# Accumulate output and edit one message every `interval` seconds. If the
# output exceeds the message limit, keep the current message and start another.
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from .ansi import escape_markdown_code
from .session import UserSession
from .i18n import translate

logger = logging.getLogger(__name__)


class OutputThrottler:
    def __init__(
        self,
        bot: Bot,
        session: UserSession,
        chat_id: int,
        interval: float,
        max_len: int,
    ) -> None:
        self._bot = bot
        self._session = session
        self._chat_id = chat_id
        self._interval = interval
        self._max_len = max_len
        self._dirty = False
        self._stopped = False
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    def mark_dirty(self) -> None:
        self._dirty = True

    async def send_initial(self) -> None:
        # Create the status message before output appears.
        await self._flush(force=True)

    async def stop(self, final: bool = True) -> None:
        self._stopped = True
        if self._task is not None:
            self._task.cancel()
        if final:
            await self._flush(force=True)

    async def _loop(self) -> None:
        try:
            while not self._stopped:
                await asyncio.sleep(self._interval)
                if self._dirty:
                    await self._flush()
        except asyncio.CancelledError:
            pass

    def _current_chunk(self) -> str:
        # Return the buffer tail that fits in one message.
        buf = self._session.output_buffer
        if len(buf) <= self._max_len:
            return buf
        return translate(self._session.language, "truncated") + buf[-self._max_len :]

    async def _flush(self, force: bool = False) -> None:
        self._dirty = False
        chunk = self._current_chunk()
        if not chunk.strip() and not force:
            return
        header = self._session.status_header or f"🚀 *{self._session.last_command}*\n"
        body = escape_markdown_code(chunk) or translate(self._session.language, "empty")
        text = f"{header}```\n{body}{self._session.status_footer}\n```"

        try:
            if self._session.status_message_id is None:
                msg = await self._bot.send_message(
                    self._chat_id, text, parse_mode="Markdown"
                )
                self._session.status_chat_id = msg.chat.id
                self._session.status_message_id = msg.message_id
            else:
                await self._bot.edit_message_text(
                    text,
                    chat_id=self._session.status_chat_id,
                    message_id=self._session.status_message_id,
                    parse_mode="Markdown",
                )
        except TelegramBadRequest as exc:
            # Ignore "message is not modified". For other formatting errors,
            # retry without Markdown.
            if "not modified" in str(exc).lower():
                return
            try:
                if self._session.status_message_id is None:
                    msg = await self._bot.send_message(self._chat_id, text)
                    self._session.status_chat_id = msg.chat.id
                    self._session.status_message_id = msg.message_id
                else:
                    await self._bot.edit_message_text(
                        text,
                        chat_id=self._session.status_chat_id,
                        message_id=self._session.status_message_id,
                    )
            except TelegramBadRequest as exc2:
                logger.warning("Не удалось обновить сообщение статуса: %s", exc2)
