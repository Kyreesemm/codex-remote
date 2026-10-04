# Coded by Kyreesemm (KRM Tech Software), 2026
# This is an open-source project hosted and maintained on GitHub.
# Distributed under the terms of the MIT License

# File-based activity log for the Telegram bot and launched commands.
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path


class ActivityLogger:
    # One append-only log for the entire bot process lifetime.

    def __init__(self, logs_dir: Path) -> None:
        self.logs_dir = logs_dir.expanduser().resolve()
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%d-%m-%Y-%H-%M-%S")
        path = self.logs_dir / f"session-{stamp}.log"
        counter = 1
        while path.exists():
            path = self.logs_dir / f"session-{stamp}-{counter}.log"
            counter += 1
        self.path = path
        self._lock = asyncio.Lock()
        self._closed = False

    async def start(self) -> None:
        await self._write("SESSION", "Бот запущен")

    async def event(self, kind: str, text: str, *, user_id: int | None = None, chat_id: int | None = None) -> None:
        context = []
        if user_id is not None:
            context.append(f"user_id={user_id}")
        if chat_id is not None:
            context.append(f"chat_id={chat_id}")
        prefix = f" [{', '.join(context)}]" if context else ""
        await self._write(kind, f"{prefix} {text}")

    async def close(self) -> None:
        if not self._closed:
            await self._write("SESSION", "Бот остановлен")
            self._closed = True

    def files(self) -> list[Path]:
        return sorted(
            (path for path in self.logs_dir.glob("*.log") if path.is_file()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )

    async def clear(self, name: str | None = None) -> int:
        # Delete logs while preserving the live process log file.
        async with self._lock:
            targets = self.files()
            if name is not None:
                targets = [path for path in targets if path.name == name]
            removed = 0
            for path in targets:
                if path == self.path:
                    path.write_text("", encoding="utf-8")
                else:
                    path.unlink(missing_ok=True)
                removed += 1
            return removed

    async def _write(self, kind: str, text: str) -> None:
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        async with self._lock:
            with self.path.open("a", encoding="utf-8") as file:
                file.write(f"[{timestamp}] [{kind}] {text}\n")
                file.flush()
