# Coded by Kyreesemm (KRM Tech Software), 2026
# This is an open-source project hosted and maintained on GitHub.
# Distributed under the terms of the MIT License

# Run Codex CLI in headless mode through a PTY and forward output to Telegram.
from __future__ import annotations

import asyncio
import html
import logging
import os
import shlex
import time

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from ..ansi import clean_ansi, detect_prompt
from ..activity_log import ActivityLogger
from ..config import Settings
from ..codex_output import CodexOutputParser
from ..pty_manager import PTYProcess
from ..session import SessionManager, SessionStatus
from ..throttle import OutputThrottler

logger = logging.getLogger(__name__)
router = Router(name="codex")


def _format_elapsed(seconds: float) -> str:
    # Format a duration without fractional seconds.
    total_seconds = max(0, int(seconds))
    minutes, remainder = divmod(total_seconds, 60)
    if minutes:
        return f"{minutes} мин {remainder} сек"
    return f"{remainder} сек"


# The bot must always let Codex modify files in the workspace. This is
# intentionally not read from .env because read-only mode is not allowed,
# including when an existing session is resumed.
CODEX_SANDBOX_MODE = "workspace-write"
CODEX_NO_APPROVAL_CONFIG = 'approval_policy="never"'

CONFIRM_KEYBOARD = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да (y)", callback_data="pty_y"),
            InlineKeyboardButton(text="❌ Нет (n)", callback_data="pty_n"),
        ],
        [InlineKeyboardButton(text="⏎ Enter", callback_data="pty_enter")],
    ]
)


def register_codex_handlers(
    bot: Bot, sessions: SessionManager, settings: Settings,
    activity_logger: ActivityLogger | None = None,
) -> Router:
    async def _launch(
        message: Message,
        argv: list[str],
        display_cmd: str,
        parser: CodexOutputParser | None = None,
        shell_mode: bool = False,
    ) -> None:
        user_id = message.from_user.id
        session = sessions.get(user_id)
        if activity_logger is not None:
            await activity_logger.event("COMMAND", display_cmd, user_id=user_id, chat_id=message.chat.id)

        # The PTY may not have started when the initial message is sent.
        # RUNNING also prevents a second /codex during this short startup gap.
        if (
            session.pty is not None
            and (session.pty.is_running or session.status == SessionStatus.RUNNING)
        ):
            await message.answer(
                "⚠️ Процесс уже выполняется. Дождитесь завершения или "
                "используйте /clear, чтобы принудительно остановить его."
            )
            return

        session.reset_for_new_run(display_cmd)
        session.active_is_codex = parser is not None
        codex_generation = session.codex_generation
        started_at = time.monotonic()
        pty_proc = PTYProcess(cwd=session.current_cwd)
        session.pty = pty_proc

        status_task: asyncio.Task | None = None
        status_message_id: int | None = None
        status_chat_id: int | None = None

        async def edit_codex_status(status: str) -> None:
            if status_message_id is None or status_chat_id is None:
                return
            # After /clear or /model, an old callback must not change the new
            # session state or the message belonging to another run.
            if session.pty is not pty_proc:
                return
            text = (
                "<b>☁️ Codex начал работу.</b>\n\n"
                f"• <i>{html.escape(status)}</i>"
            )
            try:
                await bot.edit_message_text(
                    text,
                    chat_id=status_chat_id,
                    message_id=status_message_id,
                    parse_mode="HTML",
                )
            except TelegramBadRequest as exc:
                if "not modified" not in str(exc).lower():
                    logger.debug("Не удалось обновить статус Codex: %s", exc)
            except Exception:  # noqa: BLE001
                logger.debug("Не удалось обновить статус Codex", exc_info=True)

        async def codex_status_loop(parser: CodexOutputParser) -> None:
            last_status = parser.status
            try:
                while session.pty is pty_proc:
                    await asyncio.sleep(2.5)
                    current_status = parser.status
                    if current_status != last_status:
                        await edit_codex_status(current_status)
                        last_status = current_status
            except asyncio.CancelledError:
                pass

        throttler = OutputThrottler(
            bot=bot,
            session=session,
            chat_id=message.chat.id,
            interval=min(settings.output_flush_interval, 1.5) if shell_mode else settings.output_flush_interval,
            max_len=settings.max_message_length,
        )

        async def on_output(data: bytes) -> None:
            if activity_logger is not None:
                await activity_logger.event(
                    "OUTPUT", data.decode("utf-8", errors="replace"),
                    user_id=user_id, chat_id=message.chat.id,
                )
            if parser is not None:
                parser.feed(data)
                return
            text = data.decode("utf-8", errors="ignore")
            cleaned = clean_ansi(text)
            session.output_buffer += cleaned
            throttler.mark_dirty()

            if session.status == SessionStatus.RUNNING and detect_prompt(
                session.output_buffer[-800:]
            ):
                session.status = SessionStatus.AWAITING_INPUT
                tail = session.output_buffer[-500:].strip()
                await bot.send_message(
                    message.chat.id,
                    f"⚠️ *Требуется подтверждение:*\n```\n{tail}\n```",
                    reply_markup=CONFIRM_KEYBOARD,
                    parse_mode="Markdown",
                )

        async def on_exit(exit_code: int) -> None:
            if status_task is not None:
                status_task.cancel()
                try:
                    await status_task
                except asyncio.CancelledError:
                    pass
            # `/clear` may have reset session.pty while the child process was
            # waiting to exit. The old callback must then do nothing.
            if session.pty is not pty_proc:
                return
            if activity_logger is not None:
                await activity_logger.event("EXIT", f"{display_cmd}: code={exit_code}", user_id=user_id, chat_id=message.chat.id)
            if parser is None:
                if shell_mode:
                    session.status_footer = (
                        f"\n\n• Выполнение завершено, код: {exit_code}"
                    )
                await throttler.stop(final=True)
                if not shell_mode:
                    icon = "🏁" if exit_code == 0 else "💥"
                    await bot.send_message(
                        message.chat.id,
                        f"{icon} *{display_cmd}* завершена. Код выхода: `{exit_code}`",
                        parse_mode="Markdown",
                    )
            else:
                parser.finish()
                await edit_codex_status("Работа завершена.")
                # Store the ID only after Codex has created or confirmed the
                # thread. The next `/codex` command can then resume it.
                if parser.thread_id is not None and codex_generation == session.codex_generation:
                    session.codex_thread_id = parser.thread_id
                elapsed = time.monotonic() - started_at
                answer = parser.answer or parser.error or "(Codex не вернул текстовый ответ.)"
                if exit_code != 0 and parser.error is None:
                    answer = f"Codex завершился с ошибкой (код {exit_code}).\n\n{answer}"
                usage = parser.usage
                task_tokens = usage.total_tokens
                if codex_generation == session.codex_generation:
                    session.codex_total_tokens += task_tokens
                total_tokens = session.codex_total_tokens
                result_text = (
                    "☁️ *Codex завершил задачу.*\n\n"
                    f"{answer}\n\n"
                    f"*• Время:* {_format_elapsed(elapsed)}\n"
                    f"*• Токенов на задачу:* {task_tokens}\n"
                    f"*• Токенов всего:* {total_tokens}"
                )
                # A normal answer fits into one message. If the model returns
                # more than Telegram allows, split only that exceptional case.
                if len(result_text) <= 4096:
                    await message.answer(result_text, parse_mode="Markdown")
                else:
                    await message.answer(
                        "☁️ *Codex завершил задачу.*\n\nОтвет слишком длинный для одного "
                        "сообщения Telegram и будет отправлен частями.",
                        parse_mode="Markdown",
                    )
                    for offset in range(0, len(answer), settings.max_message_length):
                        await message.answer(answer[offset : offset + settings.max_message_length])
                    await message.answer(
                        "*• Время:* {elapsed}\n"
                        "*• Токенов на задачу:* {task}\n"
                        "*• Токенов всего:* {total}".format(
                            elapsed=_format_elapsed(elapsed),
                            task=task_tokens,
                            total=total_tokens,
                        ),
                        parse_mode="Markdown",
                    )
            session.status = SessionStatus.IDLE
            session.pty = None
            session.active_is_codex = False

        if parser is None:
            throttler.start()
            if shell_mode:
                session.status_header = "▶️ *Выполнение команды:*\n"
                await throttler.send_initial()
        else:
            status_message = await message.answer(
                "<b>☁️ Codex начал работу.</b>\n\n"
                f"• <i>{html.escape(parser.status)}</i>",
                parse_mode="HTML",
            )
            status_message_id = status_message.message_id
            status_chat_id = status_message.chat.id
            session.status_chat_id = status_chat_id
            session.status_message_id = status_message_id
            status_task = asyncio.create_task(codex_status_loop(parser))
        # Sending the initial message awaits Telegram. If /clear or /model was
        # handled during that time, do not start the already-cancelled task.
        if session.pty is not pty_proc:
            if status_task is not None:
                status_task.cancel()
            return
        pty_proc.spawn(argv, on_output=on_output, on_exit=on_exit, env=session.shell_env)

    @router.message(Command("codex"))
    async def cmd_codex(message: Message) -> None:
        prompt = (message.text or "").replace("/codex", "", 1).strip()
        if not prompt:
            await message.answer("Использование: `/codex <промпт>`", parse_mode="Markdown")
            return

        # `codex <prompt>` starts the interactive TUI. Its ANSI redraws cannot
        # be displayed correctly in Telegram, and the prompt is not a headless
        # execution request. The bot therefore uses `codex exec`, which emits
        # a regular event and text stream.
        session = sessions.get(message.from_user.id)
        if session.codex_thread_id:
            # `resume` takes the thread ID first and the new prompt afterward.
            # `--sandbox` belongs to `exec`, so it must precede `resume`.
            argv = [
                *shlex.split(settings.codex_command),
                "exec",
                "--sandbox",
                CODEX_SANDBOX_MODE,
            ]
            if settings.codex_approval_mode == "never":
                argv.extend(["-c", CODEX_NO_APPROVAL_CONFIG])
            argv.extend([
                "resume",
                session.codex_thread_id,
                "--json",
            ])
            if settings.codex_skip_git_check:
                argv.append("--skip-git-repo-check")
        else:
            argv = [
                *shlex.split(settings.codex_command),
                "exec",
                "--color",
                "never",
                "--json",
                "--sandbox",
                CODEX_SANDBOX_MODE,
            ]
            if settings.codex_approval_mode == "never":
                argv.extend(["-c", CODEX_NO_APPROVAL_CONFIG])
            if settings.codex_skip_git_check:
                argv.append("--skip-git-repo-check")

        if session.codex_thread_id:
            # `exec resume` has no `--cd` option. The PTY already starts in
            # session.current_cwd, so Codex sees the correct directory.
            argv.extend(["--", prompt])
        else:
            # In exec mode there is nobody to answer interactive approval.
            # Codex provides a dedicated headless flag for workspace-write.
            argv.extend(["--cd", str(session.current_cwd), "--", prompt])
        await _launch(message, argv, f"/codex {prompt}", CodexOutputParser())

    @router.message(Command("sh", "shell"))
    async def cmd_shell(message: Message) -> None:
        # Run an arbitrary shell command under a PTY, for example `/sh htop`.
        raw = (message.text or "").split(maxsplit=1)
        if len(raw) < 2:
            await message.answer("Использование: `/sh <команда>`", parse_mode="Markdown")
            return
        session = sessions.get(message.from_user.id)
        shell_command = raw[1].strip()

        # Each `/sh` starts a new shell process. Preserve the effect of a common
        # `source venv/bin/activate` command so later commands use that venv.
        source_parts = shlex.split(shell_command)
        if len(source_parts) == 2 and source_parts[0] in {"source", "."}:
            source_path = source_parts[1]
            probe = await asyncio.create_subprocess_exec(
                settings.shell_command,
                "-lc",
                f"source {shlex.quote(source_path)} && env -0",
                cwd=str(session.current_cwd),
                env={**os.environ, **session.shell_env},
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            env_output, env_error = await probe.communicate()
            if probe.returncode == 0:
                session.shell_env = {
                    item.split(b"=", 1)[0].decode(): item.split(b"=", 1)[1].decode()
                    for item in env_output.split(b"\0")
                    if b"=" in item
                }
            elif env_error:
                await message.answer(
                    f"❌ Не удалось активировать окружение:\n```\n{env_error.decode(errors='replace').strip()}\n```",
                    parse_mode="Markdown",
                )
                return
        elif shell_command == "deactivate":
            # The deactivate function does not exist in a new shell, so restore
            # the base environment explicitly instead of returning code 127.
            session.shell_env.clear()
            shell_command = ":"

        argv = [settings.shell_command, "-lc", shell_command]
        await _launch(message, argv, raw[1], shell_mode=True)

    @router.callback_query(F.data.startswith("pty_"))
    async def on_pty_callback(callback: CallbackQuery) -> None:
        session = sessions.get(callback.from_user.id)
        if session.pty is None or not session.pty.is_running:
            await callback.answer("Процесс не активен.")
            return

        choice = callback.data.split("_", 1)[1]
        payload = {"y": b"y\n", "n": b"n\n", "enter": b"\n"}.get(choice, b"\n")
        session.pty.write(payload)
        session.status = SessionStatus.RUNNING

        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:  # noqa: BLE001
            pass
        await callback.answer(f"Отправлено: {choice}")

    @router.message(F.text & ~F.text.startswith("/"))
    async def forward_raw_input(message: Message) -> None:
        # Forward ordinary text to stdin while the process is running.
        session = sessions.get(message.from_user.id)
        if session.pty is not None and session.pty.is_running:
            session.pty.write((message.text or "").encode("utf-8") + b"\n")
            session.status = SessionStatus.RUNNING
            await message.answer("⌨️ Отправлено в процесс.", disable_notification=True)
        else:
            await message.answer(
                "Нет активного процесса. Используйте /codex <промпт> или /help."
            )

    return router
