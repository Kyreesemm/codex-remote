# Coded by Kyreesemm (KRM Tech Software), 2026
# This is an open-source project hosted and maintained on GitHub.
# Distributed under the terms of the MIT License

# ANSI escape sequence cleanup and interactive prompt detection.
from __future__ import annotations

import re

ANSI_ESCAPE_PATTERN = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

# Also remove standalone control characters such as BEL and backspace loops.
CONTROL_CHARS_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

PROMPT_PATTERNS = [
    re.compile(r"\[y/n\]", re.IGNORECASE),
    re.compile(r"\(y/n\)", re.IGNORECASE),
    re.compile(r"do you want to proceed\?", re.IGNORECASE),
    re.compile(r"allow.*command\?", re.IGNORECASE),
    re.compile(r"apply these changes\?", re.IGNORECASE),
    re.compile(r"press enter to continue", re.IGNORECASE),
    re.compile(r"overwrite.*\?", re.IGNORECASE),
    re.compile(r"continue\?\s*$", re.IGNORECASE),
]


def clean_ansi(text: str) -> str:
    # Remove terminal escape sequences and control characters.
    text = ANSI_ESCAPE_PATTERN.sub("", text)
    text = CONTROL_CHARS_PATTERN.sub("", text)
    return text


def detect_prompt(text: str) -> bool:
    # Check whether the cleaned output tail looks like a confirmation prompt.
    cleaned = clean_ansi(text).strip()
    return any(p.search(cleaned) for p in PROMPT_PATTERNS)


def escape_markdown_code(text: str) -> str:
    # Escape triple backticks inside a code block to preserve Markdown.
    return text.replace("```", "'''")
