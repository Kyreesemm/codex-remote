# Coded by Kyreesemm (KRM Tech Software), 2026
# This is an open-source project hosted and maintained on GitHub.
# Distributed under the terms of the MIT License

# User session state model.
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from .pty_manager import PTYProcess


class SessionStatus(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    AWAITING_INPUT = "AWAITING_INPUT"


@dataclass
class UserSession:
    # Isolated state for one administrator identified by user_id.

    current_cwd: Path
    language: str = "ru"
    status: SessionStatus = SessionStatus.IDLE

    # Used by /sh for an interactive process under a pseudo-terminal.
    pty: Optional[PTYProcess] = None

    # Accumulated current-command output after ANSI cleanup.
    output_buffer: str = ""
    # Number of output_buffer characters shown in the current status message.
    shown_len: int = 0
    # ID and chat_id of the editable Telegram status message.
    status_chat_id: Optional[int] = None
    status_message_id: Optional[int] = None
    status_header: str = ""
    status_footer: str = ""
    # Last command launched in the PTY, used in the message header.
    last_command: str = ""
    # Codex CLI thread ID. The next `/codex` command resumes this thread.
    codex_thread_id: Optional[str] = None
    # Accumulated tokens for all completed requests in the current Codex thread.
    codex_total_tokens: int = 0
    # Environment variables for later `/sh` commands, such as an activated venv.
    shell_env: dict[str, str] = field(default_factory=dict)
    active_is_codex: bool = False
    codex_generation: int = 0

    def reset_for_new_run(self, command: str) -> None:
        self.status = SessionStatus.RUNNING
        self.output_buffer = ""
        self.shown_len = 0
        self.status_chat_id = None
        self.status_message_id = None
        self.status_header = ""
        self.status_footer = ""
        self.last_command = command

    def reset_codex_conversation(self) -> None:
        self.codex_thread_id = None
        self.codex_total_tokens = 0
        self.codex_generation += 1

    def reset_conversation(self) -> None:
        # Stop the current work and start a new Codex conversation.
        self.status = SessionStatus.IDLE
        self.output_buffer = ""
        self.shown_len = 0
        self.status_message_id = None
        self.status_chat_id = None
        self.status_header = ""
        self.status_footer = ""
        self.last_command = ""
        self.reset_codex_conversation()
        self.active_is_codex = False


class SessionManager:
    # Session storage by user_id; one process can serve multiple administrators.

    def __init__(self, initial_cwd: Path) -> None:
        self._initial_cwd = initial_cwd
        self._sessions: dict[int, UserSession] = {}

    def get(self, user_id: int) -> UserSession:
        session = self._sessions.get(user_id)
        if session is None:
            session = UserSession(current_cwd=self._initial_cwd)
            self._sessions[user_id] = session
        return session
