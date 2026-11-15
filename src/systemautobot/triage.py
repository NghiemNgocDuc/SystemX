"""Severity and routing. The severity rules live in one table here, not in Grok.

Grok may raise a severity for an unfamiliar error. It can never lower one.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from typing import Callable

from .models import Problem, Route, Severity, utcnow
from .settings import Settings

GB = 1024 ** 3
_RANK = {None: 0, Severity.MINOR: 1, Severity.MAJOR: 2}

Rule = Callable[[Problem, Settings], "Severity | None"]


def _disk(p: Problem, s: Settings) -> Severity | None:
    free, pct = p.evidence["free"], p.evidence["percent_free"]
    below_warning = free < s.warn_free_gb * GB
    # On a big drive 5% can still be 20+ GB, so the percent only counts below the warning level.
    if free < s.major_free_gb * GB or (pct < s.major_free_percent and below_warning):
        return Severity.MAJOR
    if below_warning:
        return Severity.MINOR
    return None


def _network(p: Problem, s: Settings) -> Severity | None:
    return Severity.MAJOR if p.evidence.get("broken") else None


def _frozen(p: Problem, s: Settings) -> Severity | None:
    if not p.evidence.get("hung"):
        return None
    return Severity.MAJOR if p.evidence.get("foreground") else Severity.MINOR


def _disk_failing(p: Problem, s: Settings) -> Severity | None:
    return Severity.MAJOR if p.evidence.get("failing") else None


def _print(p: Problem, s: Settings) -> Severity | None:
    return Severity.MINOR if p.evidence.get("stuck") else None


def _security(p: Problem, s: Settings) -> Severity | None:
    if not p.evidence.get("bad"):
        return None
    return Severity.MINOR if p.evidence.get("issue") == "signatures" else Severity.MAJOR


def _error_dialog(p: Problem, s: Settings) -> Severity | None:
    return Severity.MINOR if p.evidence.get("seen") else None


def _unknown(p: Problem, s: Settings) -> Severity | None:
    return Severity.MINOR


# playbook_id -> rule. Every playbook must have an entry; tests enforce it.
RULES: dict[str, Rule] = {
    "demo_cleanup": lambda p, s: Severity.MINOR if p.evidence.get("remaining_bytes", 0) else None,
    "disk_space": _disk,
    "network": _network,
    "frozen_app": _frozen,
    "disk_failing": _disk_failing,
    "print_queue": _print,
    "security": _security,
    "error_dialog": _error_dialog,
    "unknown": _unknown,
}

# playbook_id -> "is the new reading worse than the one the person snoozed?"
WORSE: dict[str, Callable[[dict, dict], bool]] = {
    "disk_space": lambda old, new: new["free"] < old["free"] * 0.8,
}


def rank(sev: Severity | None) -> int:
    return _RANK[sev]


class Triage:
    def __init__(self, settings: Settings, count_fixed: Callable[[str, timedelta], int] = lambda fp, w: 0,
                 clock: Callable[[], datetime] = utcnow):
        self.settings = settings
        self._count_fixed = count_fixed
        self._clock = clock
        self.waiting: dict[str, Problem] = {}          # fingerprint -> problem waiting on the person
        self._snoozed: dict[str, tuple[datetime, dict]] = {}
        self.never: set[str] = set(settings.never)     # remembered "Never for this"
        self._popups: deque[datetime] = deque()

    # ---- severity ---------------------------------------------------------

    def assess(self, problem: Problem) -> Severity | None:
        rule = RULES.get(problem.playbook_id)
        if rule is None:
            raise KeyError(f"No severity rule for playbook {problem.playbook_id!r}")
        problem.severity = rule(problem, self.settings)
        return problem.severity

    def raise_severity(self, problem: Problem, suggested: Severity) -> Severity | None:
        """Grok's suggestion. Applied only if it is higher than the rule's."""
        if rank(suggested) > rank(problem.severity):
            problem.severity = suggested
        return problem.severity

    # ---- routing ----------------------------------------------------------

    def route(self, problem: Problem) -> Route:
        if problem.severity is None:
            return Route.IGNORED
        fp = problem.fingerprint
        if fp in self.never:
            return Route.SNOOZED
        now = self._clock()
        snooze = self._snoozed.get(fp)
        if snooze:
            until, old_evidence = snooze
            worse = WORSE.get(problem.playbook_id, lambda o, n: False)(old_evidence, problem.evidence)
            if now < until and not worse:
                return Route.SNOOZED
            del self._snoozed[fp]
        existing = self.waiting.get(fp)
        if existing is not None:
            if rank(problem.severity) <= rank(existing.severity):
                existing.evidence = problem.evidence  # keep the freshest reading
                return Route.DUPLICATE
            # It got worse (minor -> major): route again below.
        problem.recurring = (
            self._count_fixed(fp, self.settings.recurrence_window) >= self.settings.recurrence_limit
        )
        self.waiting[fp] = problem
        if problem.severity is Severity.MINOR:
            return Route.DIGEST
        while self._popups and now - self._popups[0] > timedelta(hours=1):
            self._popups.popleft()
        if len(self._popups) >= self.settings.major_popups_per_hour:
            return Route.GROUPED
        self._popups.append(now)
        return Route.POPUP

    def not_now(self, problem: Problem) -> None:
        self._snoozed[problem.fingerprint] = (self._clock() + self.settings.not_now_quiet, dict(problem.evidence))
        self.waiting.pop(problem.fingerprint, None)

    def never_for(self, fingerprint: str) -> None:
        self.never.add(fingerprint)
        if fingerprint not in self.settings.never:
            self.settings.never.append(fingerprint)  # saved by the settings store
        self.waiting.pop(fingerprint, None)

    def resolved(self, fingerprint: str) -> Problem | None:
        return self.waiting.pop(fingerprint, None)
