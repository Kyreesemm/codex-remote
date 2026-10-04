# Coded by Kyreesemm (KRM Tech Software), 2026
# This is an open-source project hosted and maintained on GitHub.
# Distributed under the terms of the MIT License

# Strict configuration validation through pydantic-settings.
from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Required settings.
    bot_token: str = Field(..., alias="BOT_TOKEN")
    allowed_admin_ids: Annotated[list[int], NoDecode] = Field(
        ..., alias="ALLOWED_ADMIN_IDS"
    )

    # Optional settings with sensible defaults.
    initial_cwd: Path = Field(default_factory=Path.home, alias="INITIAL_CWD")
    codex_command: str = Field(default="codex", alias="CODEX_COMMAND")
    codex_config_path: Path = Field(
        default_factory=lambda: Path.home() / ".codex" / "config.toml",
        alias="CODEX_CONFIG_PATH",
    )
    logs_dir: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parent.parent / "logs",
        alias="LOGS_DIR",
    )
    shell_command: str = Field(default="/bin/bash", alias="SHELL_COMMAND")

    # Approval values: untrusted | on-failure | on-request | never.
    # "never" is required for headless mode because no approval dialog can be
    # answered while `codex exec` is running.
    codex_approval_mode: str = Field(default="never", alias="CODEX_APPROVAL_MODE")
    # Allow execution outside a Git repository.
    codex_skip_git_check: bool = Field(default=True, alias="CODEX_SKIP_GIT_CHECK")

    # PTY output throttling to protect Telegram Bot API limits.
    output_flush_interval: float = Field(default=1.7, alias="OUTPUT_FLUSH_INTERVAL")
    max_message_length: int = Field(default=3500, alias="MAX_MESSAGE_LENGTH")

    # Other settings.
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @field_validator("allowed_admin_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, value: object) -> list[int]:
        # Accept ALLOWED_ADMIN_IDS as a comma-separated .env value.
        if isinstance(value, str):
            return [int(part.strip()) for part in value.split(",") if part.strip()]
        if isinstance(value, (list, tuple)):
            return [int(v) for v in value]
        raise TypeError("ALLOWED_ADMIN_IDS должен быть строкой '111,222' или списком")

    @field_validator("initial_cwd", mode="before")
    @classmethod
    def _expand_cwd(cls, value: object) -> Path:
        if isinstance(value, Path):
            return value.expanduser().resolve()
        return Path(str(value)).expanduser().resolve()


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


def read_codex_model(config_path: Path) -> str:
    # Read only the top-level `model` from Codex config.toml.
    import tomllib

    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    model = data.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("В конфиге отсутствует верхнеуровневый параметр model")
    return model.strip()


def write_codex_model(config_path: Path, model: str) -> None:
    # Replace only the `model` line while preserving the rest of the config.
    import os
    import re
    import tempfile

    if not re.fullmatch(r"[^\s\"']+", model):
        raise ValueError("Модель должна быть одним значением без пробелов и кавычек")

    content = config_path.read_text(encoding="utf-8")
    replacement = f'model = "{model}"'
    pattern = re.compile(r"(?m)^model\s*=\s*(['\"]).*?\1\s*$")
    updated, count = pattern.subn(replacement, content, count=1)
    if count == 0:
        updated = replacement + "\n" + content

    config_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="config.toml.", dir=config_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
            temp_file.write(updated)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_name, config_path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise
