"""How requests reach the AI: Responses API (xAI, OpenAI), Chat Completions (Claude, Gemini,
Groq, OpenRouter, others), or a recorded replay for demo backup.

The tool loop always speaks the Responses shape: input items in, {"output": [...], "usage": {...}}
out. ChatTransport translates both ways, so one loop serves every provider.
Every transport raises AIUnavailable with a person-facing message when the AI can't be used.
"""

from __future__ import annotations

import json
import math
import re
import threading
from pathlib import Path
from typing import Any, Protocol

from .providers import Provider


def offline_line(label: str = "Grok") -> str:
    return f"I can't reach {label} right now. I didn't change anything."


class AIUnavailable(Exception):
    def __init__(self, kind: str, message: str, detail: str = "", reason: str = "",
                 retry_after: float | None = None):
        super().__init__(message)
        self.kind = kind          # no_key, bad_key, rate_limited, server, offline, bad_request, no_tools
        self.message = message
        self.detail = detail
        self.reason = reason      # the provider's own one-line explanation, secrets removed
        self.retry_after = retry_after  # seconds the provider asked us to wait (rate limits), if it said


GrokUnavailable = AIUnavailable  # older name


class Transport(Protocol):
    replay: bool

    def create(self, **request: Any) -> dict[str, Any]: ...


def _openai_client(api_key: str, base_url: str, timeout: float):
    from openai import OpenAI

    # Retries are ours (one, with backoff), so the SDK's are off.
    return OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0)


def _reason(e: Exception) -> str:
    """The provider's own message from an error body, trimmed and with any key removed."""
    from ..safety.redact import trim

    body = getattr(e, "body", None)
    msg = ""
    if isinstance(body, dict):
        err = body.get("error")  # {"error": {"message": ...}}, {"error": "..."} (xAI), or {"message": ...}
        msg = str((err.get("message") if isinstance(err, dict) else err) or body.get("message") or "")
    elif isinstance(body, str):
        msg = body
    msg = re.sub(r"\s+in organization `?org_\w+`?", "", msg)  # the account id adds nothing for the person
    return trim(" ".join(msg.split()), 260)


_TRY_AGAIN = re.compile(r"try again in\s+([0-9hms.]+)", re.I)
_OUT_OF_QUOTA = re.compile(r"insufficient_quota|exceeded your current quota", re.I)


def _retry_after(e: Exception, text: str) -> float | None:
    """Seconds the provider asked us to wait: the Retry-After header, else its "try again in 1m26.4s"."""
    headers = getattr(getattr(e, "response", None), "headers", None) or {}
    try:
        raw = headers.get("retry-after")
        if raw is not None:
            return max(0.0, float(raw))
    except (TypeError, ValueError, AttributeError):
        pass
    m = _TRY_AGAIN.search(text)
    if not m:
        return None
    scale = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}
    parts = re.findall(r"([\d.]+)(ms|h|m|s)", m.group(1))
    try:
        return sum(float(n) * scale[u] for n, u in parts) if parts else None
    except ValueError:
        return None


def _limit_words(text: str) -> str:
    t = text.lower()
    if "per day" in t or "(tpd)" in t or "(rpd)" in t:
        return "today's limit"
    if "tokens per minute" in t or "(tpm)" in t:
        return "its per-minute token limit"
    if "requests per minute" in t or "(rpm)" in t:
        return "its per-minute request limit"
    return "a rate limit"


def _rate_limited(e: Exception, label: str) -> "AIUnavailable":
    full = str(e)
    if _OUT_OF_QUOTA.search(full):
        return AIUnavailable("rate_limited", f"Your {label} account is out of quota. Check your plan and billing "
                                             f"with {label}.", full, _reason(e))
    wait = _retry_after(e, full)
    if wait is None:
        when = "Try again in a minute."
    elif wait <= 90:
        when = f"It clears in about {max(1, math.ceil(wait))} seconds."
    else:
        when = f"It clears in about {math.ceil(wait / 60)} minutes."
    return AIUnavailable("rate_limited", f"Your {label} key hit {_limit_words(full)}. {when}", full, _reason(e),
                         retry_after=wait)


_KEY_WORDS = re.compile(r"api[ _-]?key|incorrect key|invalid key|unauthori[sz]ed", re.I)
_TOOL_WORDS = re.compile(r"\btools?\b|function call", re.I)
_UNSUPPORTED = re.compile(r"support|not available|unavailable", re.I)


def _map_errors(fn, label: str):
    import openai

    try:
        return fn()
    except openai.BadRequestError as e:
        reason = _reason(e)
        if _KEY_WORDS.search(reason):  # xAI answers a wrong key with 400, not 401
            raise AIUnavailable("bad_key", f"The {label} key isn't working. Check it in Settings. "
                                           "I didn't change anything.", str(e), reason)
        if _TOOL_WORDS.search(reason) and _UNSUPPORTED.search(reason):
            raise AIUnavailable("no_tools", f"That {label} model can't use tools, and I need tools to look at your PC. "
                                            "Pick another model.", str(e), reason)
        raise AIUnavailable("bad_request", f"{label} didn't accept the request.", str(e), reason)
    except openai.AuthenticationError as e:
        raise AIUnavailable("bad_key", f"The {label} key isn't working. Check it in Settings. I didn't change anything.",
                            str(e), _reason(e))
    except openai.PermissionDeniedError as e:
        raise AIUnavailable("bad_key", f"The {label} key can't be used right now (no access or no credit). "
                                       "I didn't change anything.", str(e), _reason(e))
    except openai.NotFoundError as e:
        raise AIUnavailable("bad_request", f"{label} doesn't know that model. Pick another in Settings.", str(e),
                            _reason(e))
    except openai.RateLimitError as e:
        raise _rate_limited(e, label)
    except (openai.APIConnectionError, openai.APITimeoutError) as e:
        raise AIUnavailable("offline", offline_line(label), str(e))
    except openai.APIStatusError as e:
        raise AIUnavailable("server", f"{label} is having trouble right now. I'll try again later.", str(e))


class ResponsesTransport:
    """xAI and OpenAI: POST /responses, store: false."""

    replay = False

    def __init__(self, provider: Provider, api_key: str, base_url: str | None = None, timeout: float = 60.0):
        self.label = provider.label
        self._client = _openai_client(api_key, base_url or provider.base_url, timeout)
        self._provider, self._key, self._base_url = provider, api_key, base_url

    def models(self) -> list[str]:
        """Checks the key on its own (no model involved). Raises bad_key if it's wrong."""
        return list_models(self._provider, self._key, self._base_url)

    def create(self, **request: Any) -> dict[str, Any]:
        return _map_errors(lambda: self._client.responses.create(**request).model_dump(exclude_none=True), self.label)


XaiTransport = ResponsesTransport  # older name


class ChatTransport:
    """Chat Completions compatible services. Translates the Responses shape both ways."""

    replay = False

    def __init__(self, provider: Provider, api_key: str, base_url: str | None = None, timeout: float = 60.0):
        self.label = provider.label
        self._client = _openai_client(api_key, base_url or provider.base_url, timeout)
        self._provider, self._key, self._base_url = provider, api_key, base_url

    def models(self) -> list[str]:
        """Checks the key on its own (no model involved). Raises bad_key if it's wrong."""
        return list_models(self._provider, self._key, self._base_url)

    def create(self, **request: Any) -> dict[str, Any]:
        kwargs = to_chat_request(request)
        resp = _map_errors(lambda: self._client.chat.completions.create(**kwargs).model_dump(exclude_none=True),
                           self.label)
        return from_chat_response(resp)


def _strip_schema(schema: Any) -> Any:
    """Some services reject additionalProperties. Validation is ours anyway."""
    if isinstance(schema, dict):
        return {k: _strip_schema(v) for k, v in schema.items() if k != "additionalProperties"}
    if isinstance(schema, list):
        return [_strip_schema(v) for v in schema]
    return schema


def to_chat_request(request: dict[str, Any]) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    pending_calls: list[dict[str, Any]] = []

    def flush_calls():
        if pending_calls:
            messages.append({"role": "assistant", "content": None, "tool_calls": list(pending_calls)})
            pending_calls.clear()

    for item in request.get("input", []):
        kind = item.get("type")
        if kind == "function_call":
            pending_calls.append({"id": item["call_id"], "type": "function",
                                  "function": {"name": item["name"], "arguments": item.get("arguments", "{}")}})
            continue
        flush_calls()
        if kind == "function_call_output":
            messages.append({"role": "tool", "tool_call_id": item["call_id"], "content": item.get("output", "")})
        elif kind == "message" or "role" in item:
            role = {"developer": "system"}.get(item.get("role", "user"), item.get("role", "user"))
            content = item.get("content", "")
            if isinstance(content, list):
                content = "".join(c.get("text", "") for c in content if isinstance(c, dict))
            if content:
                messages.append({"role": role, "content": content})
        # reasoning items have no Chat Completions equivalent; they are dropped
    flush_calls()
    out: dict[str, Any] = {"model": request["model"], "messages": messages}
    tools = request.get("tools")
    if tools:
        out["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                                          "parameters": _strip_schema(t.get("parameters", {}))}}
                        for t in tools]
    return out


def from_chat_response(resp: dict[str, Any]) -> dict[str, Any]:
    choice = (resp.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    output: list[dict[str, Any]] = []
    thought = msg.get("reasoning") or msg.get("reasoning_content")
    if isinstance(thought, str) and thought.strip():
        output.append({"type": "reasoning", "summary": [{"type": "summary_text", "text": thought.strip()}]})
    if msg.get("content"):
        output.append({"type": "message", "role": "assistant",
                       "content": [{"type": "output_text", "text": msg["content"]}]})
    for call in msg.get("tool_calls") or []:
        fn = call.get("function") or {}
        output.append({"type": "function_call", "call_id": call.get("id", ""), "name": fn.get("name", ""),
                       "arguments": fn.get("arguments") or "{}"})
    usage = resp.get("usage") or {}
    return {"output": output, "usage": {"input_tokens": usage.get("prompt_tokens", 0),
                                        "output_tokens": usage.get("completion_tokens", 0),
                                        "total_tokens": usage.get("total_tokens", 0)}}


def make_transport(provider: Provider, api_key: str, base_url: str | None = None) -> Transport:
    cls = ResponsesTransport if provider.api == "responses" else ChatTransport
    return cls(provider, api_key, base_url)


# Speech, image, embedding and safety-filter models can't hold a conversation; keep them out of the picker.
_NOT_CHAT = re.compile(r"whisper|tts|transcribe|embed|dall-e|moderation|guard|orpheus", re.I)


def list_models(provider: Provider, api_key: str, base_url: str | None = None) -> list[str]:
    """Models this key can use, live from the provider. Empty if the service doesn't list them."""
    client = _openai_client(api_key, base_url or provider.base_url, 20.0)
    try:
        ids = [m.id for m in _map_errors(lambda: list(client.models.list()), provider.label)]
    except AIUnavailable as e:
        if e.kind in ("bad_key", "offline"):
            raise
        return []
    return sorted({i.removeprefix("models/") for i in ids if not _NOT_CHAT.search(i)})


class RecordingTransport:
    """Wraps a real transport and saves each response, to build a demo replay file."""

    def __init__(self, inner: Transport, path: Path):
        self.inner, self.path = inner, path
        self.replay = inner.replay
        self._lock = threading.Lock()

    def create(self, **request: Any) -> dict[str, Any]:
        resp = self.inner.create(**request)
        with self._lock:
            data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else []
            data.append(resp)
            self.path.write_text(json.dumps(data, indent=1), encoding="utf-8")
        return resp


class ReplayTransport:
    """Plays back recorded responses in order. Shown as "Replay" in the app."""

    replay = True

    def __init__(self, responses: list[dict[str, Any]]):
        self._responses = list(responses)
        self._i = 0
        self._lock = threading.Lock()

    @classmethod
    def from_file(cls, path: Path) -> "ReplayTransport":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def create(self, **request: Any) -> dict[str, Any]:
        with self._lock:
            if self._i >= len(self._responses):
                raise AIUnavailable("offline", offline_line("the replay"), "replay exhausted")
            resp = self._responses[self._i]
            self._i += 1
            return resp
