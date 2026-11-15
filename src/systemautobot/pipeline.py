"""The fix pipeline: Watch -> Match -> Triage -> Ask -> Fix -> Verify -> Record.

The UI (tray, popups, end-of-day list) is a Presenter. The pipeline never decides
for the person: every fix goes through Runner.execute with an approval.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol

from . import fix_report
from .digest import Digest, DigestItem
from .history import History
from .models import (
    ApprovedVia,
    Card,
    FixedBy,
    ManualStep,
    Outcome,
    Problem,
    Route,
    Severity,
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

# Problems with no built-in fix, which the AI explains (and may propose one fix for).
AI_EXPLAINED = ("unknown", "error_dialog")


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


_SOURCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,47}$")


def _source_name(provider: str | None) -> str:
    """The event's source (for example DistributedCOM), only if it looks like a plain name.
    Anything else is left out, so event data can't put words into the title. (Registering a new
    event source takes administrator rights, and the message text never gets here.)"""
    name = (provider or "").strip()
    for prefix in ("Microsoft-Windows-", "Microsoft-"):
        if name.startswith(prefix):
            name = name[len(prefix):]
    return name if _SOURCE.match(name) else ""


def unknown_title(app: str, provider: str | None, event_id) -> str:
    if app:
        return f"{app} reported an error."
    source = _source_name(provider)
    where = f"{source}, event {int(event_id)}" if source and str(event_id).isdigit() else source
    return f"Windows reported an error ({where})." if where else "A Windows component reported an error."


# Built-in steps for errors no playbook knows. The AI's steps replace these when it answers.
UNKNOWN_STEPS = (
    ("Press Fix for me: the AI looks into it, step by step, and asks before changing anything.", None),
    ("If nothing on your PC seems wrong, it's safe to leave. Windows logs many errors that don't matter.", None),
    ("If something stopped working, restart the PC, then press Check again.", None),
    ("Still happening after a restart? Install any waiting Windows updates.", "ms-settings:windowsupdate"),
)


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
        self.diagnoser: Callable[[Card], None] | None = None  # the AI fills unfamiliar-error cards
        self._digest_busy = False  # one end-of-day list at a time
        self.chat_cards: dict[str, Card] = {}
        self.last_digest: list[DigestItem] = []
        self.last_digest_gone: list[DigestItem] = []
        self.readings: dict[str, tuple[dict, datetime]] = {}  # latest check results by name
        self.state_path: Path | None = None    # where waiting problems are kept across restarts
        self._saved_sig: tuple | None = None
        self._lock = threading.RLock()

    # ---- watch -> match -> triage -> ask ------------------------------------

    def handle(self, signal: Signal) -> Route:
        with self._lock:
            if signal.source == "check" and (signal.name in ("disk_space", "network", "disk_health")
                                             or signal.name.startswith("security_")):
                self.readings[signal.name] = (signal.data, signal.seen_at)  # for the at-a-glance tiles
            playbook, problem = self._match(signal)
            if problem is None:
                return Route.IGNORED
            if problem.playbook_id in self.triage.settings.off:
                # The person switched this kind of problem off: drop anything still waiting, ask nothing.
                fp = problem.fingerprint
                self.triage.resolved(fp)
                self.digest.remove(fp)
                card = self.open_cards.pop(fp, None)
                if card:
                    self.pending_popups = [c for c in self.pending_popups if c.id != card.id]
                    self.presenter.closed(card, "You switched this off.")
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
                title=unknown_title(app, d.get("provider"), d.get("event_id")),
                evidence={"provider": d.get("provider"), "event_id": d.get("event_id"), "app": app},
                signal=signal,
            )
        return None, None

    def propose(self, problem: Problem) -> Card:
        pb = self.playbooks.get(problem.playbook_id)
        options = pb.options(problem) if pb else []
        if problem.playbook_id != "disk_failing" and any(f.startswith("disk_failing:") for f in self.triage.waiting):
            # On a failing disk, offer nothing that writes a lot to it.
            options = [o for o in options if not o.needs_admin and "Delete" not in o.title]
        manual = pb.manual_steps(problem) if pb else []
        if not manual and problem.playbook_id == "unknown":
            manual = [ManualStep(text, target) for text, target in UNKNOWN_STEPS]
        tried_out = self.history.attempts_today(problem.fingerprint) >= self.runner.max_attempts
        option = None if (problem.recurring or tried_out or not options) else options[0]
        card = self.runner.register(Card(problem=problem, option=option, manual_steps=manual))
        self.open_cards[problem.fingerprint] = card
        return card

    def add_chat_card(self, card: Card) -> Card:
        """A card from chat: the person asked, so it is shown right away, not triaged."""
        with self._lock:
            self.runner.register(card)
            self.chat_cards[card.id] = card
            return card

    def cards_view(self) -> list[dict]:
        """Everything waiting on the person, for the main window."""
        with self._lock:
            cards = list(self.open_cards.values()) + list(self.chat_cards.values())
            seen, out = set(), []
            for c in cards:
                if c.id not in seen and not c.used:
                    seen.add(c.id)
                    d = c.to_dict()
                    d["in_digest"] = c.problem.fingerprint in self.digest.items
                    out.append(d)
            return out

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
            due = self.digest.due()
        if due:
            self.show_digest()  # outside the lock: it re-checks and may ask the AI, which takes time
        with self._lock:
            self.save_waiting()

    # ---- waiting problems survive a restart ----------------------------------

    def save_waiting(self) -> None:
        """Write what is waiting on the person, only when it changed."""
        if self.state_path is None:
            return
        live = [c for c in self.open_cards.values() if not c.used]
        shown = self.digest.last_shown.isoformat() if self.digest.last_shown else None
        sig = (shown,) + tuple(sorted((c.problem.fingerprint, c.id, bool(c.explanation)) for c in live))
        if sig == self._saved_sig:
            return
        items = []
        for c in live:
            p, d = c.problem, self.digest.items.get(c.problem.fingerprint)
            items.append({
                "playbook_id": p.playbook_id, "fingerprint": p.fingerprint, "title": p.title,
                "evidence": json.loads(json.dumps(p.evidence, default=str)),
                "severity": p.severity.value if p.severity else None, "source": p.source,
                "detected_at": p.detected_at.isoformat(), "text": p.signal.text if p.signal else "",
                "in_digest": d is not None, "rollovers": d.rollovers if d else 0, "explanation": c.explanation,
            })
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"digest_last_shown": shown, "items": items}, indent=1), encoding="utf-8")
        tmp.replace(self.state_path)
        self._saved_sig = sig

    def restore_waiting(self) -> int:
        """At startup: bring back what was waiting, checking each one again first."""
        if self.state_path is None or not self.state_path.is_file():
            return 0
        try:
            saved = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        if isinstance(saved, list):  # first version of the file
            saved = {"items": saved}
        if saved.get("digest_last_shown"):  # the end-of-day list already shown today stays shown
            from datetime import date

            self.digest.last_shown = date.fromisoformat(saved["digest_last_shown"])
        items = saved.get("items", [])
        restored = 0
        with self._lock:
            for it in items:
                if it.get("playbook_id") in self.triage.settings.off:
                    continue
                title = it["title"]
                ev = it.get("evidence", {})
                if it["playbook_id"] == "unknown" and title == "A Windows component reported an error.":
                    title = unknown_title(ev.get("app", ""), ev.get("provider"), ev.get("event_id"))
                problem = Problem(it["playbook_id"], it["fingerprint"], title, evidence=ev,
                                  source=it.get("source", "watcher"),
                                  detected_at=datetime.fromisoformat(it["detected_at"]),
                                  signal=Signal("restored", "restored", text=it.get("text", "")))
                if it["playbook_id"] in self.playbooks:
                    fresh = self.refresh(problem)
                    if fresh is None:
                        continue  # fixed while the app was closed
                    problem.evidence = fresh.evidence
                self.triage.assess(problem)
                if it.get("severity") == "major" and problem.severity is not None:
                    problem.severity = Severity.MAJOR  # never quietly downgrade an urgent problem
                if problem.severity is None:
                    continue
                self.triage.waiting[problem.fingerprint] = problem
                card = self.propose(problem)
                card.explanation = it.get("explanation", "")
                if it.get("in_digest") or problem.severity is Severity.MINOR:
                    self.digest.add(card)
                    self.digest.items[problem.fingerprint].rollovers = it.get("rollovers", 0)
                else:
                    self.pending_popups.append(card)
                restored += 1
        return restored

    def show_digest(self) -> None:
        """Re-check what's waiting, have the AI explain unfamiliar errors, then show the list.

        The re-checks (some run PowerShell) and the AI (up to a minute a question) run without the
        pipeline lock, so the windows, their buttons and the watcher keep working meanwhile."""
        with self._lock:
            if self._digest_busy:
                return
            self._digest_busy = True
            items = list(self.digest.items.values())
        try:
            still: dict[str, bool] = {}
            for item in items:
                try:
                    still[item.problem.fingerprint] = self.refresh(item.problem) is not None
                except Exception:
                    log.exception("re-check failed")
                    still[item.problem.fingerprint] = True  # keep it; better shown twice than lost
            if self.diagnoser:  # unfamiliar errors are explained in one batch, just before the list
                for item in self._to_explain(items, still):
                    try:
                        self.diagnoser(item.card)
                    except Exception:
                        log.exception("diagnosis failed")
            with self._lock:
                show, gone = self.digest.collect(lambda p: still.get(p.fingerprint, True))
                for item in gone:
                    self._record_went_away(item.problem, item.card)
                self.digest.mark_shown()
                self.last_digest, self.last_digest_gone = show, gone
            if show or gone:
                self.presenter.show_digest(show, gone)
        finally:
            with self._lock:
                self._digest_busy = False

    def _to_explain(self, items: list[DigestItem], still: dict[str, bool]) -> list[DigestItem]:
        """The items the AI should explain: the same cap on unfamiliar errors as the list itself."""
        live = [i for i in items if still.get(i.problem.fingerprint) and i.problem.playbook_id in AI_EXPLAINED]
        unknown = sorted((i for i in live if i.problem.playbook_id == "unknown"),
                         key=lambda i: i.added_at, reverse=True)[: self.digest.settings.unknown_per_digest]
        return [i for i in live if i.problem.playbook_id != "unknown"] + unknown

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
            return Verification.CANT_CHECK, "I can't check this one automatically. Tell me if it's still happening."
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
        if card is not None and not card.used and card.expired(self.clock()) and \
                via in (ApprovedVia.FIX_THIS, ApprovedVia.FIX_IT_FOR_ME):
            # The person pressed Fix on a card that's still on screen. Cards expire so an old popup
            # can't be approved by mistake; here the runner re-checks the problem before acting anyway.
            card.created_at = self.clock()
        by_ai = card is not None and card.proposed_by == "grok"
        result = self.runner.execute(card_id, token, via, refresh=self.refresh, verify=self._verify,
                                     fixed_by=FixedBy.GROK if by_ai else FixedBy.PLAYBOOK,
                                     grok=card.grok if by_ai else None)
        if card is not None:
            card.result = {"outcome": result.outcome.value, "message": result.message, "fix_id": result.fix_id,
                           "verification": result.verification.value if result.verification else None,
                           "report": self.fix_report(result.fix_id)}
        if card is not None and result.outcome is not Outcome.REFUSED:
            with self._lock:
                fp = card.problem.fingerprint
                self.open_cards.pop(fp, None)
                self.chat_cards.pop(card.id, None)
                self.digest.remove(fp)
                # Clear it either way. If it isn't fixed, the next reading raises it again,
                # and propose() offers manual steps once the daily attempt limit is reached.
                self.triage.resolved(fp)
        return result

    def fix_report(self, fix_id: str | None) -> dict | None:
        """What the fix did (steps, changes, re-check), from the history record, for the card."""
        if not fix_id:
            return None
        try:
            return fix_report.build(self.history.get(fix_id))
        except Exception:  # the report is a courtesy; the fix itself already finished
            log.exception("could not build the fix report")
            return None

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
            self.chat_cards.pop(card.id, None)

    def manual_done(self, card_id: str, opened: list[str] | None = None, note: str | None = None) -> Verification:
        """The person followed the steps and pressed Check again."""
        return self.check_again(card_id, opened, note)[0]

    def check_again(self, card_id: str, opened: list[str] | None = None, note: str | None = None,
                    sorted_by_person: bool = False) -> tuple[Verification, str]:
        """Check again after the person's own steps.

        Fixed: the card closes. Still happening: it stays. Some problems can't be looked at again
        (a past crash, an unfamiliar Windows error), so the answer is CANT_CHECK and the person can
        say "it's sorted" (sorted_by_person) to close it themselves."""
        card = self.runner.cards.get(card_id)
        if card is None:
            raise KeyError(card_id)
        problem = card.problem
        pb = self.playbooks.get(problem.playbook_id)
        if not sorted_by_person and (pb is None or not pb.can_recheck(problem)):
            return Verification.CANT_CHECK, ("I can't check this kind of problem by looking. "
                                             "If it's sorted, tell me and I'll close it.")
        fresh = None
        if not sorted_by_person:
            fresh = pb.recheck(problem)
            if fresh is not None:
                self.triage.assess(fresh)
                if fresh.severity is None:
                    fresh = None
        verdict = Verification.FIXED if fresh is None else Verification.NOT_FIXED
        fix_id = self.history.open_fix(problem, fixed_by=FixedBy.PERSON, approved_via=ApprovedVia.MANUAL,
                                       approved_at=self.clock(), card_id=card.id)
        self.history.add_manual(fix_id, [s.text for s in card.manual_steps], opened or [], note)
        if verdict is Verification.FIXED:
            self.history.close_fix(fix_id, Outcome.DONE,
                                   ("You said it's sorted. " if sorted_by_person else "You fixed it. ")
                                   + "Files touched: not tracked, you made these changes outside the app.")
            with self._lock:
                self.triage.resolved(problem.fingerprint)
                self.digest.remove(problem.fingerprint)
                self.open_cards.pop(problem.fingerprint, None)
                self.chat_cards.pop(card.id, None)
        else:
            self.history.close_fix(fix_id, Outcome.COULDNT, "Still broken after your steps.")
        self.history.add_verification(fix_id, verdict, "you said it's sorted" if sorted_by_person
                                      else (fresh.title if fresh else ""))
        if sorted_by_person:
            message = "Okay, I've closed it. If it happens again, I'll let you know."
        elif verdict is Verification.FIXED:
            message = ("The error message is gone. If it comes back, I'll show it again."
                       if problem.playbook_id == "error_dialog" else "I checked again and it's fixed. Nice work.")
        else:
            message = f"Still happening: {fresh.title} Try the next step, or ask me in the chat."
        return verdict, message

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
    pipe = Pipeline(build_all(tools), triage, Digest(settings), runner, history, presenter,
                    popups_should_wait=checks.popups_should_wait)
    pipe.tools = tools
    return pipe
