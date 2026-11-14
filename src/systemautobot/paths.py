"""Where SystemAutoBot keeps its data. Tests pass their own base folder."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    base: Path

    @classmethod
    def default(cls) -> "AppPaths":
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return cls(Path(local) / "SystemAutoBot")

    @property
    def history_db(self) -> Path:
        return self.base / "history.db"

    @property
    def history_dir(self) -> Path:
        return self.base / "history"

    @property
    def logs_dir(self) -> Path:
        return self.base / "logs"

    @property
    def app_log(self) -> Path:
        return self.logs_dir / "app.log"

    def ensure(self) -> "AppPaths":
        for p in (self.base, self.history_dir, self.logs_dir):
            p.mkdir(parents=True, exist_ok=True)
        return self


def helper_results_dir() -> Path:
    """Admin helper results live under ProgramData so they are found even when a
    different (admin) account approved the Windows prompt, and only admins can write there."""
    root = os.environ.get("ProgramData", r"C:\ProgramData")
    return Path(root) / "SystemAutoBot" / "runs"
