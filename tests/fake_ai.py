"""Scripted AI transports for tests. Nothing here calls a real provider."""

from __future__ import annotations

import json
from typing import Any

from systemautobot.grok.transport import AIUnavailable


def call(name: str, args: dict | str | None = None, call_id: str | None = None) -> dict:
    raw = args if isinstance(args, str) else json.dumps(args or {})
    return {"type": "function_call", "call_id": call_id or f"c_{name}", "name": name, "arguments": raw}


def say(text: str) -> dict:
    return {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}


def reasoning(blob: str = "ENCRYPTED") -> dict:
    return {"type": "reasoning", "id": "rs_1", "encrypted_content": blob, "summary": []}


class ScriptedTransport:
    """Returns one scripted response per round and records every request it was sent."""

    replay = False

    def __init__(self, rounds: list[list[dict] | Exception]):
        self.rounds = list(rounds)
        self.requests: list[dict[str, Any]] = []

    def create(self, **request: Any) -> dict[str, Any]:
        self.requests.append(json.loads(json.dumps(request, default=str)))
        if not self.rounds:
            return {"output": [say("Done.")], "usage": {"total_tokens": 1}}
        nxt = self.rounds.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return {"output": nxt, "usage": {"total_tokens": 10}}


def unavailable(kind: str) -> AIUnavailable:
    return AIUnavailable(kind, f"{kind} message")
