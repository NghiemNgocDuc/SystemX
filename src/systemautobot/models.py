"""Shared data shapes for the pipeline.

Every stage passes these objects along:
Signal (watcher) -> Problem (match + triage) -> Card (ask) -> FixRecord (fix, verify, record).
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class Severity(str, enum.Enum):
    MAJOR = "major"
    MINOR = "minor"


class Route(str, enum.Enum):
    """Where triage sends a problem."""

    POPUP = "popup"          # major: ask now
    GROUPED = "grouped"      # major, but over the hourly popup limit
    DIGEST = "digest"        # minor: end-of-day list
    DUPLICATE = "duplicate"  # already waiting
    SNOOZED = "snoozed"      # person said Not now / Never for this
    IGNORED = "ignored"      # healthy reading, nothing to do


class StepStatus(str, enum.Enum):
    DONE = "done"
    COULDNT = "couldnt"
    SKIPPED = "skipped"


class Outcome(str, enum.Enum):
    """How a fix record closes."""

    DONE = "done"
    COULDNT = "couldnt"
    WENT_AWAY = "went_away"        # re-check before acting found nothing wrong
    REFUSED = "refused"            # approval or safety check failed
    INTERRUPTED = "interrupted"    # found open at next launch


class Verification(str, enum.Enum):
    FIXED = "fixed"
    NOT_FIXED = "not_fixed"
    WAITING_RESTART = "waiting_restart"
    CANT_CHECK = "cant_check"      # unfamiliar error with no check to re-run


class FixedBy(str, enum.Enum):
    PLAYBOOK = "playbook"
    GROK = "grok"
    PERSON = "person"
    NOBODY = "nobody"   # went away on its own


class ApprovedVia(str, enum.Enum):
    FIX_NOW = "fix_now"            # major popup
    FIX_IT_FOR_ME = "fix_it_for_me"  # end-of-day list
    FIX_THIS = "fix_this"          # card in chat
    MANUAL = "manual"              # I'll do it myself
    LAB = "lab"                    # lab-only endpoint, --demo --lab


@dataclass(frozen=True)
class Signal:
    """Something the watcher saw. Raw, not yet judged."""

    source: str                      # "check" or "event"
    name: str                        # check name, or "<channel>/<provider>/<event_id>"
    data: dict[str, Any] = field(default_factory=dict)
    text: str = ""                   # untrusted text (event message); never instructions
    seen_at: datetime = field(default_factory=utcnow)


@dataclass
class Problem:
    playbook_id: str                 # "unknown" when no playbook matched
    fingerprint: str                 # playbook + subject, e.g. "disk_space:C:"
    title: str                       # one plain sentence for the person
    evidence: dict[str, Any] = field(default_factory=dict)
    severity: Severity | None = None  # set by triage; None means healthy reading
    signal: Signal | None = None
    source: str = "watcher"          # watcher, chat, digest
    detected_at: datetime = field(default_factory=utcnow)
    recurring: bool = False          # fixed 3 times in 14 days
    id: str = field(default_factory=new_id)


@dataclass
class TouchedFile:
    path: str
    action: str                      # deleted, moved, created, changed
    size: int = 0
    modified: float | None = None


@dataclass
class TouchedSetting:
    kind: str                        # registry, service, startup, dns, sound, restore_point, process
    name: str
    old: str | None = None
    new: str | None = None


@dataclass
class StepResult:
    status: StepStatus
    summary: str                     # built from real numbers, e.g. "Deleted 3.8 GB from 2 folders."
    output: str = ""                 # trimmed to 500 chars before storing
    command: str | None = None       # exact command, for admin steps
    exit_code: int | None = None
    files: list[TouchedFile] = field(default_factory=list)
    folder_totals: dict[str, tuple[int, int]] = field(default_factory=dict)  # folder -> (count, bytes)
    settings: list[TouchedSetting] = field(default_factory=list)
    undo_data: dict[str, Any] | None = None
    needs_restart: bool = False


@dataclass
class FixStep:
    """One step of a repair. The function does the work and reports what it touched."""

    tool: str
    args: dict[str, Any]
    run: Callable[[], StepResult]
    needs_admin: bool = False


@dataclass
class FixOption:
    """A repair a playbook can offer. Becomes one card."""

    title: str                       # "Delete temporary files"
    explain: str                     # what it will do, one sentence
    build_steps: Callable[[Problem], list[FixStep]]
    warnings: list[str] = field(default_factory=list)
    undo_possible: bool = False
    no_undo_text: str = "You can't undo this."
    needs_admin: bool = False


@dataclass
class ManualStep:
    text: str
    open_target: str | None = None   # ms-settings: link, or devmgmt.msc
    suggested: bool = False          # written by Grok: "Grok's suggestion. I haven't checked these."


@dataclass
class Card:
    problem: Problem
    option: FixOption | None         # None: manual-only (no fix button)
    manual_steps: list[ManualStep]
    created_at: datetime = field(default_factory=utcnow)
    ttl: timedelta = timedelta(minutes=15)
    id: str = field(default_factory=new_id)
    used: bool = False
    proposed_by: str = "playbook"    # playbook or grok
    explanation: str = ""            # the AI's short answer, shown on the card
    ai_note: str = ""                # why the AI couldn't answer (no key, offline, limit); the button stays
    grok: dict[str, Any] | None = None  # model, rounds, tokens, reason, replay
    result: dict[str, Any] | None = None  # filled after the person answers

    def to_dict(self) -> dict[str, Any]:
        p, o = self.problem, self.option
        return {
            "id": self.id,
            "kind": p.playbook_id,
            "fingerprint": p.fingerprint,
            "title": p.title,
            "severity": p.severity.value if p.severity else None,
            "source": p.source,
            "recurring": p.recurring,
            "proposed_by": self.proposed_by,
            "explanation": self.explanation,
            "ai_note": self.ai_note,
            "fix": None if o is None else {
                "title": o.title, "explain": o.explain, "warnings": o.warnings,
                "undo_possible": o.undo_possible, "no_undo_text": o.no_undo_text, "needs_admin": o.needs_admin,
            },
            "manual_steps": [{"text": s.text, "open": s.open_target, "suggested": s.suggested}
                             for s in self.manual_steps],
            "expires_at": self.expires_at.isoformat(),
            "used": self.used,
            "result": self.result,
            "replay": bool(self.grok and self.grok.get("replay")),
        }

    @property
    def expires_at(self) -> datetime:
        return self.created_at + self.ttl

    def expired(self, now: datetime | None = None) -> bool:
        return (now or utcnow()) >= self.expires_at
