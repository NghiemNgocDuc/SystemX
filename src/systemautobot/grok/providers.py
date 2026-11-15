"""AI providers the person can choose on first run. Grok stays the default.

Model names are not hard-coded beyond a starting suggestion: the app lists models live
from the provider with the person's key, and always accepts a typed model name.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    base_url: str
    api: str               # "responses" (xAI, OpenAI) or "chat" (Chat Completions compatible)
    default_model: str     # a suggestion; the person can pick any model the key can use
    key_page: str          # where to get a key
    key_hint: str = ""
    key_prefix: str = ""   # how this provider's keys start, to catch a key pasted under the wrong provider
    prefer: tuple[str, ...] = ()  # good models (they can use tools), tried in order after the list loads


PROVIDERS: dict[str, Provider] = {p.id: p for p in [
    Provider("xai", "xAI Grok", "https://api.x.ai/v1", "responses", "grok-4.7",
             "https://console.x.ai", "starts with xai-", "xai-"),
    Provider("openai", "OpenAI", "https://api.openai.com/v1", "responses", "",
             "https://platform.openai.com/api-keys", "starts with sk-", "sk-"),
    Provider("anthropic", "Anthropic Claude", "https://api.anthropic.com/v1/", "chat", "claude-sonnet-5-5",
             "https://console.anthropic.com/settings/keys", "starts with sk-ant-", "sk-ant-"),
    Provider("gemini", "Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai/", "chat", "",
             "https://aistudio.google.com/apikey", "starts with AIza", "AIza"),
    Provider("groq", "Groq (fast open models)", "https://api.groq.com/openai/v1", "chat", "llama-3.3-70b-versatile",
             "https://console.groq.com/keys", "starts with gsk_", "gsk_",
             # Groq also serves models that can't use tools (the app needs tools), so steer to ones that can.
             ("llama-3.3-70b-versatile", "openai/gpt-oss-120b", "moonshotai/kimi-k2-instruct", "qwen/qwen3-32b")),
    Provider("openrouter", "OpenRouter (many models)", "https://openrouter.ai/api/v1", "chat", "",
             "https://openrouter.ai/keys", "starts with sk-or-", "sk-or-"),
    Provider("custom", "Other OpenAI-compatible service", "", "chat", "",
             "", "any key your service gives you"),
]}

DEFAULT_PROVIDER = "xai"


def get(provider_id: str) -> Provider:
    try:
        return PROVIDERS[provider_id]
    except KeyError:
        raise ValueError(f"Unknown provider {provider_id!r}")


def guess(key: str) -> Provider | None:
    """Which provider a key belongs to, from how it starts. None if it doesn't look like any we know."""
    key = (key or "").strip()
    known = sorted((p for p in PROVIDERS.values() if p.key_prefix), key=lambda p: len(p.key_prefix), reverse=True)
    return next((p for p in known if key.startswith(p.key_prefix)), None)


def mismatch(provider_id: str, key: str) -> str | None:
    """A plain sentence when the key clearly belongs to another provider, else None."""
    if provider_id == "custom":
        return None  # a custom service can hand out any key shape
    owner = guess(key)
    if owner and owner.id != provider_id:
        return (f"That looks like a {owner.label} key, but {get(provider_id).label} is picked. "
                f"Change Provider to {owner.label}.")
    return None


def public_list() -> list[dict]:
    return [{"id": p.id, "label": p.label, "default_model": p.default_model, "key_page": p.key_page,
             "key_hint": p.key_hint, "key_prefix": p.key_prefix, "prefer": list(p.prefer),
             "needs_base_url": p.id == "custom"} for p in PROVIDERS.values()]
