"""API keys live in Windows Credential Manager, one per provider. Never in a file, log, or the repo."""

from __future__ import annotations

from ..safety.redact import register_secret

SERVICE = "SystemAutoBot"


def _user(provider: str) -> str:
    return f"{provider}_api_key"


def _keyring():
    import keyring

    return keyring


def get_key(provider: str = "xai") -> str | None:
    try:
        key = _keyring().get_password(SERVICE, _user(provider))
    except Exception:
        return None
    if key:
        register_secret(key)
    return key or None


def set_key(provider: str, key: str) -> None:
    key = key.strip()
    if not key:
        raise ValueError("The key is empty.")
    register_secret(key)
    _keyring().set_password(SERVICE, _user(provider), key)


def delete_key(provider: str) -> None:
    try:
        _keyring().delete_password(SERVICE, _user(provider))
    except Exception:
        pass
