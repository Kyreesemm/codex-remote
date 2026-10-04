# Coded by Kyreesemm (KRM Tech Software), 2026
# This is an open-source project hosted and maintained on GitHub.
# Distributed under the terms of the MIT License

# User-level systemd service control for Codex Remote.
from __future__ import annotations

import asyncio
import json
from pathlib import Path

SERVICE_NAME = "codex-remote.service"
NOTICE_PATH = Path(__file__).resolve().parent.parent / ".service-reload-notice.json"


async def service_status() -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "systemctl", "--user", "show", SERVICE_NAME,
        "--no-pager", "--property=LoadState,ActiveState,SubState,MainPID,Result",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, (stdout or stderr).decode(errors="replace").strip()


def write_reload_notice(chat_id: int) -> None:
    NOTICE_PATH.write_text(json.dumps({"chat_id": chat_id}), encoding="utf-8")


def consume_reload_notice() -> int | None:
    try:
        data = json.loads(NOTICE_PATH.read_text(encoding="utf-8"))
        NOTICE_PATH.unlink(missing_ok=True)
        chat_id = data.get("chat_id")
        return int(chat_id) if chat_id is not None else None
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


async def restart_service() -> int:
    proc = await asyncio.create_subprocess_exec(
        "systemctl", "--user", "restart", SERVICE_NAME,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    return await proc.wait()
