"""The fix pipeline: Watch -> Match -> Triage -> Ask -> Fix -> Verify -> Record.

The UI (tray, popups, end-of-day list) is a Presenter. The pipeline never decides
for the person: every fix goes through Runner.execute with an approval.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol

from .digest import Digest, DigestItem
from .history import History
from .models import (
    ApprovedVia,
    Card,
    FixedBy,
    Outcome,
    Problem,
    Route,
    Signal,
    StepResult,
    Verification,
    utcnow,
)
from .playbooks import Playbook, Toolbox, build_all
from .safety.guard import default_guard
from .settings import Settings
from .tools import checks as real_checks
from .tools.fixes import AdminLauncher, RunResult, Runner
from .triage import Triage, rank

log = logging.getLogger("systemautobot.pipeline")


class Presenter(Protocol):
    def show_popup(self, card: Card) -> None: ...
    def show_grouped(self, cards: list[Card]) -> None: ...
    def show_digest(self, items: list[DigestItem], went_away: list[DigestItem]) -> None: ...
    def closed(self, card: Card, reason: str) -> None: ...


class NullPresenter:
    def show_popup(self, card): pass
    def show_grouped(self, cards): pass
    def show_digest(self, items, went_away): pass
    def closed(self, card, reason): pass


_EXE_NAME = re.compile(r"^[\w .()+-]{1,60}\.exe$", re.IGNORECASE)


def _app_name(fields: dict[str, str]) -> str:
    """A program name for the title, only from fields Windows uses for it and only if it
    looks like an .exe name. Event text is untrusted: it must never reach the title."""
    for key in ("AppName", "Application", "0"):
        name = Path(fields.get(key, "").strip()).name
        if _EXE_NAME.match(name):
            return name
    return ""


class Pipeline:
    def __init__(
        self,
        playbooks: list[Playbook],
        triage: Triage,
        digest: Digest,
        runner: Runner,
        history: History,
        presenter: Presenter | None = None,
        *,
        popups_should_wait: Callable[[], bool] = lambda: False,
        clock: Callable[[], datetime] = utcnow,
    ):
        self.playbooks = {p.id: p for p in playbooks}
        self.triage = triage
        self.digest = digest
        self.runner = runner
        self.history = history
        self.presenter = presenter or NullPresenter()
        self.popups_should_wait = popups_should_wait
        self.clock = clock
        self.pending_popups: list[Card] = []
        self.grouped: list[Card] = []
        self.open_cards: dict[str, Card] = {}         # fingerprint -> card waiting on the person
        self.unknown_diagnoser: Callable[[Problem], None] | None = None  # Grok, Phase 4
        self._lock = threading.RLock()

    # ---- watch -> match -> triage -> ask ------------------------------------

    def handle(self, signal: Signal) -> Route:
        with self._lock:
            playbook, problem = self._match(signal)
            if problem is None:
                return Route.IGNORED
            self.triage.assess(problem)
            if problem.severity is None:
                self._went_away(problem.fingerprint)
                return Route.IGNORED
            route = self.triage.route(problem)
            if route in (Route.POPUP, Route.GROUPED, Route.DIGEST):
                card = self.propose(problem)
                if route is Route.DIGEST:
                    self.digest.add(card)
                elif route is Route.POPUP:
                    self.digest.remove(problem.fingerprint)  # it got worse: ask now instead
                    self.pending_popups.append(card)
                else:
                    self.grouped.append(card)
                self.flush_popups()
            return route

    def _match(self, signal: Signal) -> tuple[Playbook | None, Problem | None]:
        for pb in self.playbooks.values():
            problem = pb.match(signal)
            if problem is not None:
                return pb, problem
        if signal.source == "event":
            d = signal.data
            app = _app_name(d.get("fields", {}))
            return None, Problem(
                playbook_id="unknown",
                fingerprint=f"unknown:{d.get('provider')}:{d.get('event_id')}:{app.lower()}",
                title=f"{app} reported an error." if app else "A Windows component reported an error.",
                evidence={"provider": d.get("provider"), "event_id": d.get("event_id"), "app": app},
                signal=signal,
            )
        return None, None

    def propose(self, problem: Problem) -> Card:
        pb = self.playbooks.get(problem.playbook_id)
        options = pb.options(problem) if pb else []
        manual = pb.manual_steps(problem) if pb else []
        tried_out = self.history.attempts_today(problem.fingerprint) >= self.runner.max_attempts
        option = None if (problem.recurring or tried_out or not options) else options[0]
        card = self.runner.register(Card(problem=problem, option=option, manual_steps=manual))
        self.open_cards[problem.fingerprint] = card
        if pb is None and self.unknown_diagnoser:
            self.unknown_diagnoser(problem)
        return card

    def flush_popups(self) -> None:
        if self.popups_should_wait():
            return
        while self.pending_popups:
            self.presenter.show_popup(self.pending_popups.pop(0))
        if self.grouped:
            cards, self.grouped = self.grouped, []
            self.presenter.show_grouped(cards)

    def tick(self) -> None:
        """Called on a timer: show waiting popups, and the end-of-day list when due."""
        with self._lock:
            self.flush_popups()
            if self.digest.due():
                self.show_digest()

    def show_digest(self) -> None:
        with self._lock:
            show, gone = self.digest.collect(lambda p: self.refresh(p) is not None)
            for item in gone:
                self._record_went_away(item.problem, item.card)
            self.digest.mark_shown()
            if show or gone:
                self.presenter.show_digest(show, gone)

    # ---- re-check and verify --------------------------------------------------

    def refresh(self, problem: Problem) -> Problem | None:
        """Look again now. None if it's fine. Unknown errors can't be re-checked, so they stay."""
        pb = self.playbooks.get(problem.playbook_id)
        if pb is None:
            return problem
        fresh = pb.refresh(problem)
        self.triage.assess(fresh)
        return fresh if fresh.severity is not None else None

    def _verify(self, problem: Problem, results: list[StepResult]) -> tuple[Verification, str]:
        pb = self.playbooks.get(problem.playbook_id)
        if pb is None:
            return Verification.NOT_FIXED, "I can't check this one automatically."
        fresh = pb.refresh(problem)
        self.triage.assess(fresh)
        special = pb.verify(problem, fresh, results)
        if special:
            return special
        if fresh.severity is None:
            return Verification.FIXED, "I checked again and it's fixed."
        if rank(fresh.severity) < rank(problem.severity):
            return Verification.FIXED, f"It's no longer urgent. {fresh.title}"
        return Verification.NOT_FIXED, f"That didn't fix it. {fresh.title}"

    # ---- the person's answers ------------------------------------------------

    def approve(self, card_id: str, token: str, via: ApprovedVia) -> RunResult:
        card = self.runner.cards.get(card_id)
        result = self.runner.execute(card_id, token, via, refresh=self.refresh, verify=self._verify)
        if card is not None and result.outcome is not Outcome.REFUSED:
            with self._lock:
                fp = card.problem.fingerprint
                self.open_cards.pop(fp, None)
                self.digest.remove(fp)
                # Clear it either way. If it isn't fixed, the next reading raises it again,
                # and propose() offers manual steps once the daily attempt limit is reached.
                self.triage.resolved(fp)
        return result

    def not_now(self, card_id: str) -> None:
        with self._lock:
            card = self.runner.cards.get(card_id)
            if card is None:
                return
            fp = card.problem.fingerprint
            if fp in self.digest.items:
                self.digest.roll_over(fp)
            else:
                self.digest.add(card)
            self.triage.not_now(card.problem)

    def never(self, card_id: str) -> None:
        with self._lock:
            card = self.runner.cards.get(card_id)
            if card is None:
                return
            self.triage.never_for(card.problem.fingerprint)
            self.digest.remove(card.problem.fingerprint)
            self.open_cards.pop(card.problem.fingerprint, None)

    def manual_done(self, card_id: str, opened: list[str] | None = None, note: str | None = None) -> Verification:
        """The person followed the steps and pressed Check again."""
        card = self.runner.cards.get(card_id)
        if card is None:
            raise KeyError(card_id)
        fresh = self.refresh(card.problem)
        verdict = Verification.FIXED if fresh is None else Verification.NOT_FIXED
        fix_id = self.history.open_fix(card.problem, fixed_by=FixedBy.PERSON, approved_via=ApprovedVia.MANUAL,
                                       approved_at=self.clock(), card_id=card.id)
        self.history.add_manual(fix_id, [s.text for s in card.manual_steps], opened or [], note)
        if verdict is Verification.FIXED:
            self.history.close_fix(fix_id, Outcome.DONE, "You fixed it. Files touched: not tracked, "
                                                          "you made these changes outside the app.")
            with self._lock:
                self.triage.resolved(card.problem.fingerprint)
                self.digest.remove(card.problem.fingerprint)
                self.open_cards.pop(card.problem.fingerprint, None)
        else:
            self.history.close_fix(fix_id, Outcome.COULDNT, "Still broken after your steps.")
        self.history.add_verification(fix_id, verdict, fresh.title if fresh else "")
        return verdict

    # ---- went away on its own --------------------------------------------------

    def _went_away(self, fingerprint: str) -> None:
        problem = self.triage.resolved(fingerprint)
        card = self.open_cards.pop(fingerprint, None)
        self.digest.remove(fingerprint)
        if problem is not None and card is not None:
            self.pending_popups = [c for c in self.pending_popups if c.id != card.id]
            self._record_went_away(problem, card)
            self.presenter.closed(card, "It went away on its own.")

    def _record_went_away(self, problem: Problem, card: Card) -> None:
        fix_id = self.history.open_fix(problem, fixed_by=FixedBy.NOBODY, approved_via=None, card_id=card.id)
        self.history.close_fix(fix_id, Outcome.WENT_AWAY, "It went away on its own, so I didn't change anything.")

    # ---- startup ------------------------------------------------------------------

    def recover_interrupted(self) -> list[str]:
        """Repairs that started but never finished (power loss, crash)."""
        ids = self.history.unclosed()
        for fix_id in ids:
            self.history.close_fix(fix_id, Outcome.INTERRUPTED,
                                   "A repair was interrupted. Finished steps are listed and can be undone.")
        return ids


def build_pipeline(settings: Settings, history: History, presenter: Presenter | None = None,
                   checks=real_checks) -> Pipeline:
    import os

    guard = default_guard()
    temp_roots = [Path(os.environ.get("TEMP", ""))] if os.environ.get("TEMP") else []
    tools = Toolbox(guard=guard, launcher=AdminLauncher(), settings=settings, checks=checks, temp_roots=temp_roots)
    triage = Triage(settings, count_fixed=history.count_fixed)
    runner = Runner(history, paused=settings.fixes_paused, max_attempts_per_day=settings.max_attempts_per_day)
    return Pipeline(build_all(tools), triage, Digest(settings), runner, history, presenter,
                    popups_should_wait=checks.popups_should_wait)
