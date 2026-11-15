"""User-facing settings, saved to settings.json (never the API key), and the timing knobs
demo mode shortens."""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import time, timedelta
from pathlib import Path


@dataclass
class Settings:
    # AI provider (the key itself lives in Windows Credential Manager)
    provider: str = "xai"
    model: str = "grok-4.7"
    base_url: str = ""                     # only for "custom"
    setup_done: bool = False
    # thresholds and timing
    warn_free_gb: int = 20                 # 10, 20, or 40
    major_free_gb: float = 2.0
    major_free_percent: float = 5.0
    end_of_day: time = time(18, 0)
    poll_interval: timedelta = timedelta(minutes=5)
    hung_after: timedelta = timedelta(seconds=30)
    stuck_print_after: timedelta = timedelta(minutes=10)
    slow_check_interval: timedelta = timedelta(minutes=10)  # PowerShell-based checks
    dialog_poll: timedelta = timedelta(seconds=3)
    ai_daily_limit: int = 50              # AI questions per day; 0 means no limit
    major_popups_per_hour: int = 3
    not_now_quiet: timedelta = timedelta(hours=4)
    recurrence_limit: int = 3
    recurrence_window: timedelta = timedelta(days=14)
    max_attempts_per_day: int = 2
    unknown_per_digest: int = 5
    history_days: int = 365
    fixes_paused: bool = False
    autopilot: bool = False                # fix critical problems with no click (safe repairs only)
    autopilot_close_frozen: bool = False   # ...including closing frozen programs (unsaved work is lost)
    autopilot_admin: bool = False          # ...and a few admin repairs with no Windows prompt (set up once)
    auto_fixes_per_hour: int = 3
    never: list[str] = field(default_factory=list)   # remembered "Never for this"
    off: list[str] = field(default_factory=list)     # kinds of problems the person doesn't want handled
    demo: bool = False

    @classmethod
    def demo_mode(cls) -> "Settings":
        return cls(poll_interval=timedelta(seconds=5), stuck_print_after=timedelta(seconds=30),
                   hung_after=timedelta(seconds=10), slow_check_interval=timedelta(seconds=60), demo=True)


# Only these are saved; the rest are code defaults or per-launch flags.
SAVED = ("provider", "model", "base_url", "setup_done", "warn_free_gb", "end_of_day",
         "history_days", "fixes_paused", "never", "off", "ai_daily_limit", "autopilot", "autopilot_close_frozen", "autopilot_admin")

# The kinds of problems the person can switch on or off, in the order the app shows them.
PROBLEM_KINDS = [
    {"id": "disk_space", "label": "Drive almost full", "detail": "Delete temporary files, empty the Recycle Bin"},
    {"id": "network", "label": "No internet or wrong clock", "detail": "Refresh the connection, fix DNS, set the clock"},
    {"id": "frozen_app", "label": "Frozen programs", "detail": "Close and reopen, restart the taskbar"},
    {"id": "print_queue", "label": "Stuck printing", "detail": "Cancel the stuck job, restart the print service"},
    {"id": "disk_failing", "label": "Disk failing", "detail": "Disk errors or a bad health report: tell me to back up"},
    {"id": "security", "label": "Windows Security and firewall", "detail": "Protection off, old virus definitions, firewall off"},
    {"id": "error_dialog", "label": "Error messages on screen", "detail": "Notice error boxes and offer to explain them"},
    {"id": "unknown", "label": "Other errors", "detail": "Ask your AI to explain and suggest a fix"},
]
PROBLEM_IDS = {k["id"] for k in PROBLEM_KINDS}


class SettingsStore:
    def __init__(self, path: Path, settings: Settings):
        self.path = path
        self.settings = settings
        self._lock = threading.Lock()

    def load(self) -> Settings:
        if self.path.is_file():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
            for key in SAVED:
                if key not in data:
                    continue
                value = data[key]
                if key == "end_of_day":
                    h, m = map(int, str(value).split(":"))
                    value = time(h, m)
                setattr(self.settings, key, value)
        return self.settings

    def save(self) -> None:
        s = asdict(self.settings)
        out = {k: s[k] for k in SAVED}
        out["end_of_day"] = self.settings.end_of_day.strftime("%H:%M")
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(out, indent=1), encoding="utf-8")
            tmp.replace(self.path)

    def update(self, **changes) -> Settings:
        for k, v in changes.items():
            if k not in SAVED:
                raise KeyError(k)
            setattr(self.settings, k, v)
        self.save()
        return self.settings

    def public(self) -> dict:
        s = self.settings
        return {"provider": s.provider, "model": s.model, "base_url": s.base_url, "setup_done": s.setup_done,
                "warn_free_gb": s.warn_free_gb, "end_of_day": s.end_of_day.strftime("%H:%M"),
                "history_days": s.history_days, "fixes_paused": s.fixes_paused, "never": list(s.never),
                "off": list(s.off), "problem_kinds": PROBLEM_KINDS, "ai_daily_limit": s.ai_daily_limit, "demo": s.demo,
                "autopilot": s.autopilot, "autopilot_close_frozen": s.autopilot_close_frozen,
                "autopilot_admin": s.autopilot_admin}
