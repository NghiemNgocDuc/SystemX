"""Scrub secrets (the xAI key) from anything written to logs, history, or sent to Grok."""

from __future__ import annotations

import re
import threading

_lock = threading.Lock()
_secrets: set[str] = set()

# Provider key shapes (xAI, OpenAI, Anthropic, OpenRouter, Google, Groq). Catch ones we were never told about too.
_KEY_PATTERN = re.compile(r"xai-[A-Za-z0-9_\-]{16,}|sk-[A-Za-z0-9_\-]{20,}|AIza[0-9A-Za-z_\-]{30,}|gsk_[A-Za-z0-9]{20,}")
REDACTED = "[REDACTED]"


def register_secret(value: str) -> None:
    if value and len(value) >= 8:
        with _lock:
            _secrets.add(value)


def clear_secrets() -> None:
    with _lock:
        _secrets.clear()


def redact(text: str | None) -> str:
    if not text:
        return text or ""
    with _lock:
        secrets = sorted(_secrets, key=len, reverse=True)
    for s in secrets:
        text = text.replace(s, REDACTED)
    return _KEY_PATTERN.sub(REDACTED, text)


def trim(text: str | None, limit: int = 500) -> str:
    text = redact(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"
