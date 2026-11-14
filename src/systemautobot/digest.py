"""The end-of-day list for minor problems."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable

from .models import Card, Problem, utcnow
from .settings import Settings


@dataclass
class DigestItem:
    card: Card
    added_at: datetime
    rollovers: int = 0

    @property
    def problem(self) -> Problem:
        return self.card.problem


class Digest:
    def __init__(self, settings: Settings, local_now: Callable[[], datetime] = datetime.now):
        self.settings = settings
        self.local_now = local_now
        self.items: dict[str, DigestItem] = {}
        self.last_shown: date | None = None

    def add(self, card: Card) -> None:
        fp = card.problem.fingerprint
        if fp in self.items:
            self.items[fp].card = card  # freshest reading, keep roll-over count
        else:
            self.items[fp] = DigestItem(card, utcnow())

    def remove(self, fingerprint: str) -> DigestItem | None:
        return self.items.pop(fingerprint, None)

    def due(self) -> bool:
        now = self.local_now()
        return bool(self.items) and self.last_shown != now.date() and now.time() >= self.settings.end_of_day

    def collect(self, still_there: Callable[[Problem], bool]) -> tuple[list[DigestItem], list[DigestItem]]:
        """Re-check every item right before showing. Returns (show, went_away)."""
        show, gone = [], []
        for fp, item in list(self.items.items()):
            if still_there(item.problem):
                show.append(item)
            else:
                gone.append(self.items.pop(fp))
        known = [i for i in show if i.problem.playbook_id != "unknown"]
        unknown = sorted((i for i in show if i.problem.playbook_id == "unknown"),
                         key=lambda i: i.added_at, reverse=True)[: self.settings.unknown_per_digest]
        return known + unknown, gone

    def mark_shown(self) -> None:
        self.last_shown = self.local_now().date()

    def roll_over(self, fingerprint: str) -> None:
        if fingerprint in self.items:
            self.items[fingerprint].rollovers += 1
