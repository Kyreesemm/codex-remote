# Coded by Kyreesemm (KRM Tech Software), 2026
# This is an open-source project hosted and maintained on GitHub.
# Distributed under the terms of the MIT License

# Parser for structured output from `codex exec --json`.
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .i18n import translate


@dataclass
class CodexUsage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class CodexOutputParser:
    # Collect the answer, token usage, and current status from Codex events.

    def __init__(self, language: str = "ru") -> None:
        self.language = language
        self._pending = ""
        self.answer_parts: list[str] = []
        self.thread_id: str | None = None
        self.usage = CodexUsage()
        self.error: str | None = None
        self.status = self._tr("Preparing the task...", "Подготавливаю задачу...")

    def _tr(self, english: str, russian: str) -> str:
        return russian if self.language == "ru" else english

    def feed(self, chunk: bytes) -> None:
        self._pending += chunk.decode("utf-8", errors="replace")
        lines = self._pending.splitlines(keepends=True)
        self._pending = ""
        if lines and not lines[-1].endswith(("\n", "\r")):
            self._pending = lines.pop()
        for line in lines:
            self._event(line.strip())

    def finish(self) -> None:
        if self._pending.strip():
            self._event(self._pending.strip())
        self._pending = ""

    @property
    def answer(self) -> str:
        return "\n\n".join(part.strip() for part in self.answer_parts if part.strip())

    def _event(self, line: str) -> None:
        if not line:
            return
        try:
            event: dict[str, Any] = json.loads(line)
        except json.JSONDecodeError:
            return

        event_type = event.get("type")
        if event_type == "thread.started":
            self.status = self._tr("Connecting to the work thread...", "Подключаюсь к рабочему потоку...")
            thread_id = event.get("thread_id")
            if isinstance(thread_id, str):
                self.thread_id = thread_id
        elif event_type == "turn.started":
            self.status = self._tr("Analyzing the task...", "Анализирую задачу...")
        elif event_type == "turn.completed":
            usage = event.get("usage") or {}
            self.usage.input_tokens = int(usage.get("input_tokens", 0) or 0)
            self.usage.cached_input_tokens = int(usage.get("cached_input_tokens", 0) or 0)
            self.usage.output_tokens = int(usage.get("output_tokens", 0) or 0)
            self.status = self._tr("Preparing the final answer...", "Формирую итоговый ответ...")
        elif event_type == "error":
            self.error = str(event.get("message") or event.get("error") or "Unknown error")
            self.status = (f"Error: {self.error}" if self.language == "en" else f"Ошибка: {self.error}")
        elif event_type in {"item.started", "item.completed"}:
            item = event.get("item") or {}
            self._update_item_status(item, event_type == "item.started")
            if item.get("type") == "error":
                self.error = str(item.get("message") or item.get("error") or "Unknown error")
                self.status = (f"Error: {self.error}" if self.language == "en" else f"Ошибка: {self.error}")
            elif item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                self.answer_parts.append(item["text"])
        elif event_type == "response.output_text.done":
            text = event.get("text")
            if isinstance(text, str):
                self.answer_parts.append(text)

    def _update_item_status(self, item: dict[str, Any], started: bool) -> None:
        # Convert a Codex service event into a short Telegram status.
        item_type = item.get("type")
        if item_type == "reasoning" and isinstance(item.get("text"), str):
            # Codex reasoning often arrives as several Markdown-formatted lines.
            # The last non-empty line is the current status.
            lines = [
                re.sub(r"[*_`]+", "", part).strip()
                for part in str(item["text"]).splitlines()
            ]
            status = next((line for line in reversed(lines) if line), "")
            if status:
                self.status = status[:500]
            return

        statuses = {
            "command_execution": (
                ("Running a command..." if started else "Checking command results...") if self.language == "en" else ("Выполняю команду..." if started else "Проверяю результат команды...")
            ),
            "file_change": (
                ("Changing files..." if started else "Reviewing file changes...") if self.language == "en" else ("Вношу изменения в файлы..." if started else "Проверяю внесённые изменения...")
            ),
            "mcp_tool_call": (
                ("Calling a tool..." if started else "Processing tool results...") if self.language == "en" else ("Обращаюсь к инструменту..." if started else "Обрабатываю результат инструмента...")
            ),
            "web_search": (
                ("Searching for information..." if started else "Processing search results...") if self.language == "en" else ("Ищу информацию..." if started else "Обрабатываю результаты поиска...")
            ),
            "agent_message": "Формирую ответ..." if self.language == "ru" else "Preparing the answer...",
        }
        status = statuses.get(item_type)
        if status:
            self.status = status
