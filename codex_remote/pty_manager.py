# Coded by Kyreesemm (KRM Tech Software), 2026
# This is an open-source project hosted and maintained on GitHub.
# Distributed under the terms of the MIT License


# Asynchronous PTY manager for CLI tools such as Codex CLI and arbitrary shells.
# It uses os.forkpty() and non-blocking event-loop reads instead of a busy loop.
from __future__ import annotations

import asyncio
import fcntl
import os
import pty
import signal
import struct
import termios
from pathlib import Path
from typing import Awaitable, Callable, Optional

OutputCallback = Callable[[bytes], Awaitable[None]]
ExitCallback = Callable[[int], Awaitable[None]]


class PTYProcess:
    # One process running under a pseudo-terminal.

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd
        self.master_fd: Optional[int] = None
        self.pid: Optional[int] = None
        self._closed = False
        self._on_output: Optional[OutputCallback] = None
        self._on_exit: Optional[ExitCallback] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    @property
    def is_running(self) -> bool:
        return self.pid is not None and not self._closed

    def spawn(
        self,
        argv: list[str],
        on_output: OutputCallback,
        on_exit: ExitCallback,
        env: Optional[dict[str, str]] = None,
    ) -> None:
        # Fork the process under a PTY and start asynchronous output reading.
        self._loop = asyncio.get_running_loop()
        self._on_output = on_output
        self._on_exit = on_exit

        pid, fd = pty.fork()
        if pid == 0:
            # --- Child process ---
            try:
                os.chdir(str(self.cwd))
                run_env = os.environ.copy()
                run_env["TERM"] = run_env.get("TERM", "xterm-256color")
                if env:
                    run_env.update(env)
                os.execvpe(argv[0], argv, run_env)
            except Exception as exc:  # noqa: BLE001
                os.write(2, f"[codex-remote] exec failed: {exc}\n".encode())
                os._exit(1)
            return  # Unreachable.

        # --- Parent process ---
        self.pid = pid
        self.master_fd = fd
        self._set_nonblocking(fd)
        self.resize()
        self._loop.add_reader(fd, self._read_ready)
        asyncio.create_task(self._wait_child())

    @staticmethod
    def _set_nonblocking(fd: int) -> None:
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    def _read_ready(self) -> None:
        assert self.master_fd is not None
        try:
            data = os.read(self.master_fd, 4096)
        except OSError:
            data = b""
        if not data:
            try:
                assert self._loop is not None
                self._loop.remove_reader(self.master_fd)
            except (ValueError, OSError):
                pass
            return
        if self._on_output is not None:
            asyncio.create_task(self._on_output(data))

    async def _wait_child(self) -> None:
        assert self.pid is not None
        while True:
            await asyncio.sleep(0.3)
            try:
                waited_pid, status = os.waitpid(self.pid, os.WNOHANG)
            except ChildProcessError:
                waited_pid, status = self.pid, 0
            if waited_pid == self.pid:
                self._closed = True
                if self.master_fd is not None:
                    try:
                        assert self._loop is not None
                        self._loop.remove_reader(self.master_fd)
                    except (ValueError, OSError):
                        pass
                    try:
                        os.close(self.master_fd)
                    except OSError:
                        pass
                if os.WIFEXITED(status):
                    exit_code = os.WEXITSTATUS(status)
                elif os.WIFSIGNALED(status):
                    exit_code = -os.WTERMSIG(status)
                else:
                    exit_code = -1
                if self._on_exit is not None:
                    await self._on_exit(exit_code)
                return

    def write(self, data: bytes) -> None:
        # Write data to process stdin, for example b'y\\n'.
        if self.master_fd is not None and self.is_running:
            try:
                os.write(self.master_fd, data)
            except OSError:
                pass

    def resize(self, cols: int = 120, rows: int = 40) -> None:
        if self.master_fd is None:
            return
        try:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
        except OSError:
            pass

    def terminate(self, sig: int = signal.SIGKILL) -> None:
        # Kill the whole process group because the PTY process is its session leader.
        if self.pid is None or self._closed:
            return
        try:
            os.killpg(os.getpgid(self.pid), sig)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(self.pid, sig)
            except (ProcessLookupError, OSError):
                pass
