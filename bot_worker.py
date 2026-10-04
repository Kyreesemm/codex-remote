# Coded by Kyreesemm (KRM Tech Software), 2026
# This is an open-source project hosted and maintained on GitHub.
# Distributed under the terms of the MIT License


# Codex Remote entry point for the Telegram bot service.
# Run with: python bot_worker.py.
# Configuration is loaded from .env through pydantic-settings.
from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from codex_remote.config import load_settings
from codex_remote.activity_log import ActivityLogger
from codex_remote.handlers.codex import register_codex_handlers
from codex_remote.handlers.navigation import register_navigation_handlers
from codex_remote.middleware import AdminWhitelistMiddleware
from codex_remote.session import SessionManager
from codex_remote.service_control import consume_reload_notice


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        stream=sys.stdout,
    )


async def main() -> None:
    settings = load_settings()
    setup_logging(settings.log_level)
    logger = logging.getLogger("codex_remote")

    logger.info("Запуск Codex Remote. Каталог по умолчанию: %s", settings.initial_cwd)
    logger.info("Разрешённые admin ID: %s", settings.allowed_admin_ids)
    activity_logger = ActivityLogger(settings.logs_dir)
    await activity_logger.start()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode="Markdown"),
    )
    reload_chat_id = consume_reload_notice()
    if reload_chat_id is not None:
        try:
            await bot.send_message(reload_chat_id, "✅ *Сервис успешно перезапущен и работает.*", parse_mode="Markdown")
        except Exception:
            logging.getLogger("codex_remote").exception("Не удалось отправить уведомление о перезапуске")
    dp = Dispatcher()

    whitelist = AdminWhitelistMiddleware(
        allowed_ids=set(settings.allowed_admin_ids), activity_logger=activity_logger
    )
    dp.message.middleware(whitelist)
    dp.callback_query.middleware(whitelist)

    sessions = SessionManager(initial_cwd=settings.initial_cwd)

    dp.include_router(register_navigation_handlers(sessions, settings, activity_logger))
    dp.include_router(register_codex_handlers(bot, sessions, settings, activity_logger))

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await activity_logger.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
