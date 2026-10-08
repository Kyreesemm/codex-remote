# Coded by Kyreesemm (KRM Tech Software), 2026
# This is an open-source project hosted and maintained on GitHub.
# Distributed under the terms of the MIT License

# Working-directory commands and supporting utilities.
from __future__ import annotations

import asyncio
import logging
import re
import shlex
from pathlib import Path

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import FSInputFile, Message

from ..activity_log import ActivityLogger
from ..ansi import escape_markdown_code
from ..config import Settings, read_codex_model, write_codex_model
from ..session import SessionManager, SessionStatus
from ..service_control import SERVICE_NAME, restart_service, service_status, write_reload_notice
from ..utils import send_chunked as _send_chunked
from ..i18n import language_name, translate

logger = logging.getLogger(__name__)
router = Router(name="navigation")


def _md_escape_path(path: str) -> str:
    return path.replace("`", "'")


def register_navigation_handlers(
    sessions: SessionManager,
    settings: Settings,
    activity_logger: ActivityLogger | None = None,
) -> Router:
    def tr(message: Message, key: str, **values: object) -> str:
        return translate(sessions.get(message.from_user.id).language, key, **values)

    @router.message(Command("start", "help"))
    async def cmd_start(message: Message) -> None:
        text = tr(message, "welcome") if sessions.get(message.from_user.id).language == "en" else (
            "☁️ Добро пожаловать в *Codex Remote*!\n\n"
            "Команды:\n"
            "`/codex <промпт>` — запустить Codex CLI\n"
            "`/pwd` — текущий каталог\n"
            "`/cd <путь>` — сменить каталог\n"
            "`/lsla` — список файлов\n"
            "`/git <subcommand>` — git-команда в текущем каталоге\n"
            "`/model [имя]` — показать или изменить модель Codex\n"
            "`/logs [get|clear]` — просмотр, скачивание и очистка логов\n"
            "`/service [reload]` — состояние или перезапуск сервиса\n"
            "`/clear` — очистить текущий чат и начать новый диалог Codex\n\n"
            "Пока процесс запущен, любое обычное сообщение (без `/`) "
            "отправляется ему на stdin."
        )
        await message.answer(text, parse_mode="Markdown")

    @router.message(Command("language"))
    async def cmd_language(message: Message) -> None:
        session = sessions.get(message.from_user.id)
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) == 1:
            if session.language == "en":
                await message.answer(tr(message, "language_current", language=language_name("en")), parse_mode="Markdown")
            else:
                await message.answer(f"🌐 Текущий язык интерфейса: *{language_name('ru')}*", parse_mode="Markdown")
            return
        selected = parts[1].strip().lower()
        if selected not in {"ru", "en"}:
            await message.answer(tr(message, "language_invalid") if session.language == "en" else "❌ Выберите `ru` или `en`.", parse_mode="Markdown")
            return
        session.language = selected
        if selected == "en":
            await message.answer(tr(message, "language_changed", language=language_name("en")), parse_mode="Markdown")
        else:
            await message.answer(f"✅ Язык интерфейса изменён на *{language_name('ru')}*.", parse_mode="Markdown")

    @router.message(Command("pwd"))
    async def cmd_pwd(message: Message) -> None:
        session = sessions.get(message.from_user.id)
        await message.answer(
            tr(message, "directory", path=_md_escape_path(str(session.current_cwd))) if session.language == "en" else
            f"📂 *Текущий каталог:*\n`{_md_escape_path(str(session.current_cwd))}`",
            parse_mode="Markdown",
        )

    @router.message(Command("cd"))
    async def cmd_cd(message: Message) -> None:
        session = sessions.get(message.from_user.id)
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await message.answer(tr(message, "cd_usage") if session.language == "en" else "Использование: `/cd <путь>`", parse_mode="Markdown")
            return

        target = (session.current_cwd / parts[1]).expanduser().resolve()
        if target.exists() and target.is_dir():
            session.current_cwd = target
            await message.answer(
                tr(message, "cd_changed", path=_md_escape_path(str(session.current_cwd))) if session.language == "en" else f"📂 *Перешли в:*\n`{_md_escape_path(str(session.current_cwd))}`",
                parse_mode="Markdown",
            )
        else:
            await message.answer(tr(message, "dir_missing") if session.language == "en" else "❌ *Каталог не найден.*")

    @router.message(Command("lsla"))
    async def cmd_lsla(message: Message) -> None:
        session = sessions.get(message.from_user.id)
        proc = await asyncio.create_subprocess_exec(
            "ls",
            "-la",
            "--color=never",
            cwd=str(session.current_cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        output = stdout.decode(errors="ignore") or stderr.decode(errors="ignore")
        if activity_logger is not None:
            await activity_logger.event("OUTPUT", output, user_id=message.from_user.id, chat_id=message.chat.id)
        await _send_chunked(message, output)

    @router.message(Command("git"))
    async def cmd_git(message: Message) -> None:
        session = sessions.get(message.from_user.id)
        english = session.language == "en"
        raw_command = (message.text or "").strip()
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) > 1:
            try:
                # Preserve shell quoting rules. For example,
                # `-m "Update site background"` must remain one Git argument.
                args = shlex.split(parts[1])
            except ValueError as exc:
                await message.answer(f"❌ Некорректные кавычки в команде: `{exc}`", parse_mode="Markdown")
                return
        else:
            args = ["status"]

        if not args:
            args = ["status"]

        if activity_logger is not None:
            await activity_logger.event(
                "COMMAND", raw_command, user_id=message.from_user.id, chat_id=message.chat.id
            )

        # Commits from Telegram must not request a GPG password. This option
        # applies only to this command and does not change Git configuration.
        commit_index = next(
            (index for index, value in enumerate(args)
             if value == "commit"),
            None,
        )
        if commit_index is not None:
            # Insert the flag immediately after the subcommand so forms such
            # as `git -c user.name=... commit -m "..."` also work.
            args = [
                *args[: commit_index + 1],
                "--no-gpg-sign",
                *args[commit_index + 1 :],
            ]

        status_message = await message.answer(
            "▶️ *Running Git command:*\n```\n(running...)\n```" if english else "▶️ *Выполнение команды Git:*\n```\n(выполняется...)\n```",
            parse_mode="Markdown",
        )
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=str(session.current_cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        output = (stdout + stderr).decode(errors="ignore")
        if activity_logger is not None:
            await activity_logger.event("OUTPUT", output or "(нет вывода)", user_id=message.from_user.id, chat_id=message.chat.id)
        empty = "(no output)" if english else "(нет вывода)"
        final_text = (
            ("▶️ *Running Git command:*\n```\n" if english else "▶️ *Выполнение команды Git:*\n```\n")
            + escape_markdown_code(output or empty)
            + (f"\n\n• Finished with exit code: {proc.returncode}\n```" if english else f"\n\n• Выполнение завершено, код: {proc.returncode}\n```")
        )
        try:
            await status_message.edit_text(final_text, parse_mode="Markdown")
        except Exception:  # noqa: BLE001
            await status_message.edit_text(
                ("▶️ Running Git command:\n" if english else "▶️ Выполнение команды Git:\n")
                + f"{output or empty}\n\n"
                + (f"• Finished with exit code: {proc.returncode}" if english else f"• Выполнение завершено, код: {proc.returncode}")
            )

    @router.message(Command("logs"))
    async def cmd_logs(message: Message) -> None:
        # Manage log files from the current and previous runs.
        session = sessions.get(message.from_user.id)
        if activity_logger is None:
            await message.answer(tr(message, "logs_unavailable") if session.language == "en" else "❌ Логирование недоступно.")
            return

        parts = (message.text or "").split(maxsplit=2)
        action = parts[1].lower() if len(parts) > 1 else "list"
        argument = parts[2] if len(parts) > 2 else None
        files = activity_logger.files()

        if action in {"list", "ls"}:
            if not files:
                await message.answer(tr(message, "logs_empty") if session.language == "en" else "🗂 *Логи пока отсутствуют.*", parse_mode="Markdown")
                return
            lines = [tr(message, "logs_files") if session.language == "en" else "🗂 *Файлы логов:*"]
            for index, path in enumerate(files, 1):
                size = path.stat().st_size
                marker = (" *(current)*" if session.language == "en" else " *(текущий)*") if path == activity_logger.path else ""
                lines.append(f"`{path.name}` — {size / 1024:.2f} KB{marker}" if session.language == "en" else f"`{path.name}` — {size / 1024:.2f} КБ{marker}")
            await message.answer("\n".join(lines), parse_mode="Markdown")
            return

        if action == "get":
            if argument is None:
                await message.answer(tr(message, "logs_get_usage") if session.language == "en" else "Использование: `/logs get <имя-файла>` или `/logs get latest`", parse_mode="Markdown")
                return
            path = files[0] if argument.lower() == "latest" and files else None
            if path is None:
                if Path(argument).name != argument or not argument.endswith(".log"):
                    await message.answer("❌ Invalid log filename." if session.language == "en" else "❌ Некорректное имя файла лога.")
                    return
                path = activity_logger.logs_dir / argument
            if not path.is_file() or path.parent != activity_logger.logs_dir:
                await message.answer("❌ Log file not found." if session.language == "en" else "❌ Такой файл лога не найден.")
                return
            await message.answer_document(
                FSInputFile(path), caption=f"📄 Log: `{path.name}`" if session.language == "en" else f"📄 Лог: `{path.name}`", parse_mode="Markdown"
            )
            return

        if action == "clear":
            if argument is not None and (Path(argument).name != argument or not argument.endswith(".log")):
                await message.answer("❌ Invalid log filename." if session.language == "en" else "❌ Некорректное имя файла лога.")
                return
            removed = await activity_logger.clear(argument)
            if argument is not None and removed == 0:
                await message.answer("❌ Log file not found." if session.language == "en" else "❌ Такой файл лога не найден.")
            else:
                suffix = ("Current log cleared; its file was kept." if session.language == "en" else "Текущий лог очищен, его файл сохранён.") if argument is None else ("Done." if session.language == "en" else "Готово.")
                await message.answer(f"🧹 {suffix}")
            return

        await message.answer(
            ("Usage:\n`/logs` — list logs\n`/logs get <name>` — download a log\n`/logs get latest` — download the latest log\n`/logs clear [name]` — clear logs" if session.language == "en" else "Использование:\n"
            "`/logs` — список логов\n"
            "`/logs get <имя>` — скачать лог\n"
            "`/logs get latest` — скачать последний лог\n"
            "`/logs clear [имя]` — очистить логи"),
            parse_mode="Markdown",
        )

    @router.message(Command("model"))
    async def cmd_model(message: Message) -> None:
        # Show or change the model in ~/.codex/config.toml.
        parts = (message.text or "").split(maxsplit=1)
        try:
            current_model = read_codex_model(settings.codex_config_path)
        except FileNotFoundError:
            await message.answer(
                tr(message, "config_missing", path=_md_escape_path(str(settings.codex_config_path))) if sessions.get(message.from_user.id).language == "en" else f"❌ Конфиг Codex не найден:\n`{_md_escape_path(str(settings.codex_config_path))}`",
                parse_mode="Markdown",
            )
            return
        except (OSError, ValueError, UnicodeError) as exc:
            await message.answer(tr(message, "model_read_error", error=exc) if sessions.get(message.from_user.id).language == "en" else f"❌ Не удалось прочитать модель: `{exc}`", parse_mode="Markdown")
            return

        if len(parts) == 1:
            await message.answer(
                tr(message, "model_current", model=_md_escape_path(current_model)) if sessions.get(message.from_user.id).language == "en" else f"🤖 *Текущая модель:* `{_md_escape_path(current_model)}`",
                parse_mode="Markdown",
            )
            return

        new_model = parts[1].strip()
        if not re.fullmatch(r"[^\s\"']+", new_model):
            await message.answer(
                (tr(message, "model_usage") if sessions.get(message.from_user.id).language == "en" else "Использование: `/model <имя-модели>`\n"
                "Например: `/model cx/gpt-5.6-luna`",
                ),
                parse_mode="Markdown",
            )
            return
        try:
            write_codex_model(settings.codex_config_path, new_model)
        except (OSError, ValueError, UnicodeError) as exc:
            await message.answer(tr(message, "model_write_error", error=exc) if sessions.get(message.from_user.id).language == "en" else f"❌ Не удалось изменить модель: `{exc}`", parse_mode="Markdown")
            return

        session = sessions.get(message.from_user.id)
        # Apply the new model to a new Codex conversation. Stop an active
        # Codex process as well, but leave ordinary `/sh` processes untouched.
        if session.active_is_codex and session.pty is not None and session.pty.is_running:
            session.pty.terminate()
            session.pty = None
            session.status = SessionStatus.IDLE
            session.active_is_codex = False
        session.reset_codex_conversation()
        await message.answer(
            tr(message, "model_changed", model=_md_escape_path(new_model)) if session.language == "en" else f"✅ *Модель изменена:* `{_md_escape_path(new_model)}`",
            parse_mode="Markdown",
        )

    @router.message(Command("clear"))
    async def cmd_clear(message: Message) -> None:
        session = sessions.get(message.from_user.id)
        # Telegram does not provide an API for reading and fully deleting chat
        # history. Delete the messages from the current run that are available.
        message_ids = []
        if session.status_chat_id == message.chat.id and session.status_message_id is not None:
            message_ids.append(session.status_message_id)
        if session.pty is not None and session.pty.is_running:
            session.pty.terminate()
        session.pty = None
        session.reset_conversation()
        for message_id in message_ids:
            try:
                await message.bot.delete_message(message.chat.id, message_id)
            except Exception:  # noqa: BLE001
                logger.debug("Не удалось удалить сообщение статуса", exc_info=True)
        try:
            await message.delete()
        except Exception:  # noqa: BLE001
            logger.debug("Не удалось удалить сообщение /clear", exc_info=True)
        await message.answer(
            tr(message, "clear_done") if session.language == "en" else "🧹 *Сессия Codex сброшена.*\nСледующее использование команды `/codex` начнёт новый диалог.",
        )

    @router.message(Command("service"))
    async def cmd_service(message: Message) -> None:
        parts = (message.text or "").split(maxsplit=1)
        action = parts[1].strip().lower() if len(parts) > 1 else "status"
        if action in {"status", "state"}:
            code, output = await service_status()
            if code != 0 and not output:
                output = "Could not get systemd service status." if sessions.get(message.from_user.id).language == "en" else "Не удалось получить состояние systemd-сервиса."
            await message.answer(
                (tr(message, "service_status", name=SERVICE_NAME, output=output or "(no data)") if sessions.get(message.from_user.id).language == "en" else f"⚙️ *Состояние сервиса `{SERVICE_NAME}`:*\n```\n"
                f"{output or '(нет данных)'}\n```",
                ),
                parse_mode="Markdown",
            )
            return
        if action == "reload":
            write_reload_notice(message.chat.id)
            await message.answer(tr(message, "service_reload") if sessions.get(message.from_user.id).language == "en" else "🔄 *Сервис начал перезапуск.*\nОжидайте сообщение после запуска новой версии.", parse_mode="Markdown")

            async def do_restart() -> None:
                await asyncio.sleep(0.5)
                await restart_service()

            asyncio.create_task(do_restart())
            return
        await message.answer(
            (tr(message, "service_usage") if sessions.get(message.from_user.id).language == "en" else "Использование:\n`/service` — состояние сервиса\n`/service reload` — перезапуск"),
            parse_mode="Markdown",
        )

    return router
