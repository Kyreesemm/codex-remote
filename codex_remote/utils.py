# Coded by Kyreesemm (KRM Tech Software), 2026
# This is an open-source project hosted and maintained on GitHub.
# Distributed under the terms of the MIT License

# Shared handler utilities.
from __future__ import annotations

from aiogram.types import Message

TELEGRAM_CHUNK_LIMIT = 3500


async def send_chunked(message: Message, text: str, *, code: bool = True) -> None:
    # Split long text into chunks that fit Telegram's message limit.
    if not text:
        text = "(пусто)"
    for i in range(0, len(text), TELEGRAM_CHUNK_LIMIT):
        chunk = text[i : i + TELEGRAM_CHUNK_LIMIT]
        if code:
            await message.answer(f"```\n{chunk}\n```", parse_mode="Markdown")
        else:
            await message.answer(chunk)
