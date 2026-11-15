from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from systemautobot.history import History
from systemautobot.paths import AppPaths
from systemautobot.safety.guard import Guard
from systemautobot.settings import Settings

GB = 1024 ** 3


class FakeChecks:
    """Same functions as tools.checks, with readings the test controls."""

    def __init__(self):
        self.disk = {"drive": "C:", "free": 100 * GB, "total": 500 * GB, "percent_free": 20.0}
        self.net = {"adapter_up": True, "dns_ok": True, "http_ok": True, "clock_skew_seconds": 1, "error": ""}
        self.hung: list[dict] = []
        self.wait = False

    def set_free(self, free_bytes: int, total: int = 500 * GB):
        self.disk = {"drive": "C:", "free": free_bytes, "total": total,
                     "percent_free": round(100 * free_bytes / total, 1)}

    def disk_space(self, drive="C:\\"):
        return dict(self.disk)

    def network_probe(self, timeout=5.0):
        return dict(self.net)

    def hung_windows(self):
        return list(self.hung)

    def popups_should_wait(self):
        return self.wait


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 14, 15, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


@pytest.fixture
def paths(tmp_path) -> AppPaths:
    return AppPaths(tmp_path / "app").ensure()


@pytest.fixture
def history(paths):
    h = History(paths)
    yield h
    h.close()


@pytest.fixture
def settings() -> Settings:
    return Settings(fixes_paused=False)


@pytest.fixture
def fake_checks() -> FakeChecks:
    return FakeChecks()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def sandbox(tmp_path):
    """A temp root the guard may clean, and a 'Documents' it must never touch."""
    temp = tmp_path / "Temp"
    docs = tmp_path / "Documents"
    temp.mkdir()
    docs.mkdir()
    guard = Guard(allowed_roots=[temp], protected=[docs], protected_exact=[tmp_path])
    return guard, temp, docs


def make_old(path: Path, seconds: int = 7200) -> None:
    import os
    import time

    t = time.time() - seconds
    os.utime(path, (t, t))
