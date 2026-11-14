"""User-facing settings and the timing knobs demo mode shortens."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time, timedelta


@dataclass
class Settings:
    warn_free_gb: int = 20                 # 10, 20, or 40
    major_free_gb: float = 2.0             # major under this...
    major_free_percent: float = 5.0        # ...or under this percent
    end_of_day: time = time(18, 0)
    poll_interval: timedelta = timedelta(minutes=5)
    hung_after: timedelta = timedelta(seconds=30)
    major_popups_per_hour: int = 3
    not_now_quiet: timedelta = timedelta(hours=4)
    recurrence_limit: int = 3
    recurrence_window: timedelta = timedelta(days=14)
    max_attempts_per_day: int = 2
    unknown_per_digest: int = 5
    fixes_paused: bool = True              # safe default until a person turns fixes on
    demo: bool = False

    @classmethod
    def demo_mode(cls) -> "Settings":
        return cls(poll_interval=timedelta(seconds=10), demo=True)
