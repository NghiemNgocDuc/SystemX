"""Autopilot: the harness that lets the app, and the AI agent, fix critical problems with no click.

Off unless the person switches it on. For each critical problem the loop is:

  1. Observe  The watcher found it and triage called it critical.
  2. Decide   A built-in repair if the problem has one. Otherwise the AI agent looks (its tools
              only read) and may pick ONE repair from the app's fixed list.
  3. Guard    The harness, never the agent, decides whether that repair may run unattended:
                - only repairs marked safe: nothing that can lose work (closing a frozen program
                  only if the person switched that on as well), and no administrator rights,
                  unless the person set up no-prompt admin repairs: then five quick ones may run
                - never while fixes are paused, and nothing that deletes on a disk that may be failing
                - one automatic try per problem per day, and a few per hour in total
  4. Act      Through the same runner as every other fix: same re-check first, same history record.
  5. Verify   The same check runs again.
  6. Report   A Windows notification says what was done. Activity has every step.

Whatever Autopilot may not run, or could not fix, goes to the person exactly as before.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from contextlib import nullcontext
from datetime import date, datetime, timedelta
from typing import Callable

from . import admin_setup

from .models import ApprovedVia, Card, FixOption, Outcome, Problem, Verification, utcnow
from .tools.fixes import UNATTENDED

log = logging.getLogger("systemautobot.autopilot")

SAFE, UNSAVED = "safe", "unsaved"  # FixOption.auto values Autopilot may run ("ask" always asks)


class Autopilot:
    """Sits between the pipeline and the person. It is the pipeline's Presenter: a critical
    problem comes here first, and is handed on to `human` (the notification and the Allow box)
    whenever Autopilot is off, not allowed to act, or didn't fix it."""

    def __init__(self, pipeline, service, settings, human, *,
                 tell: Callable[[str, str], bool] | None = None,
                 clock: Callable[[], datetime] = utcnow, background: bool = True,
                 launcher=None, admin_ready: Callable[[], bool] = admin_setup.installed):
        self.pipeline = pipeline
        self.service = service
        self.settings = settings
        self.human = human
        self.tell = tell or (lambda title, body: False)
        self.clock = clock
        self.background = background
        self.launcher = launcher        # runs admin steps; has the no-prompt mode
        self.admin_ready = admin_ready  # is the no-prompt setup installed and unchanged?
        self._tried: dict[str, date] = {}       # fingerprint -> the day Autopilot last tried it
        self._recent: deque[datetime] = deque()  # when automatic fixes ran, for the hourly limit
        self._lock = threading.Lock()
        self.decisions: deque[dict] = deque(maxlen=30)  # what it did, or why it asked

    def on(self) -> bool:
        return bool(self.settings.autopilot) and not self.pipeline.runner.paused

    # ---- the pipeline's Presenter -----------------------------------------------------------

    def show_popup(self, card: Card) -> None:
        if not self.on():
            return self.human.show_popup(card)
        card.auto = "working"
        if self.background:  # the pipeline holds its lock here; the work must not
            threading.Thread(target=self._work, args=(card,), name="autopilot", daemon=True).start()
        else:
            self._work(card)

    def show_grouped(self, cards: list[Card]) -> None:
        if not self.on():
            return self.human.show_grouped(cards)
        for card in cards:
            self.show_popup(card)

    def show_digest(self, items, went_away) -> None:
        self.human.show_digest(items, went_away)

    def closed(self, card: Card, reason: str) -> None:
        self.human.closed(card, reason)

    # ---- the guard ------------------------------------------------------------------------------

    def may_run(self, option: FixOption, problem: Problem) -> str | None:
        """None if Autopilot may run this repair on its own, otherwise the reason it must ask."""
        if option.needs_admin:
            if not self.settings.autopilot_admin or self.launcher is None:
                return "This repair needs administrator rights, so Windows has to ask you."
            try:
                entries = [s.tool.split(":", 1)[1] for s in option.build_steps(problem) if s.needs_admin]
            except Exception:
                entries = [""]
            if any(e not in UNATTENDED for e in entries):
                return "This admin repair isn't one of the few I can run without the Windows prompt."
            if not self.admin_ready():
                return "No-prompt admin repairs aren't set up on this PC (Settings, Autopilot)."
        if option.auto == UNSAVED and not self.settings.autopilot_close_frozen:
            return "This repair closes a program, which can lose unsaved work, so I'm asking first."
        if option.auto not in (SAFE, UNSAVED):
            return "This repair isn't one I run on my own."
        failing = any(f.startswith("disk_failing:") for f in self.pipeline.triage.waiting)
        if failing and problem.playbook_id != "disk_failing" and "Delete" in option.title:
            return "Your disk may be failing, so I won't delete anything on my own."
        return None

    # ---- one critical problem -----------------------------------------------------------------

    def _work(self, card: Card) -> None:
        try:
            verdict, why = self._attempt(card)
        except Exception:
            log.exception("autopilot failed on %s", card.problem.fingerprint)
            verdict, why = "ask", "Something went wrong while I was working on it."
        card.auto = ""
        self.decisions.append({"at": self.clock().isoformat(), "title": card.problem.title,
                               "verdict": verdict, "why": why})
        if verdict == "ask" and not card.used:
            card.auto_note = why
            self.human.show_popup(card)  # the person decides, exactly as without Autopilot

    def _attempt(self, card: Card) -> tuple[str, str]:
        problem, fp = card.problem, card.problem.fingerprint
        now = self.clock()
        with self._lock:
            if self._tried.get(fp) == now.date():
                return "ask", "I already tried this once today, so now it's your call."
            while self._recent and now - self._recent[0] > timedelta(hours=1):
                self._recent.popleft()
            if len(self._recent) >= self.settings.auto_fixes_per_hour:
                return "ask", "I've reached my limit of automatic fixes for this hour."
        if card.option is None and self.service is not None and self.service.status()["configured"]:
            self.service.diagnose(card)  # the agent looks (read-only) and may pick one repair from the list
        option = card.option
        if option is None:
            return "ask", "There's no automatic fix for this."
        blocked = self.may_run(option, problem)
        if blocked:
            return "ask", blocked
        with self._lock:
            self._tried[fp] = now.date()
            self._recent.append(now)
        no_prompt = self.launcher.no_prompt() if (option.needs_admin and self.launcher) else nullcontext()
        with no_prompt:  # admin steps go to the pre-installed tasks, never to a Windows prompt
            result = self.pipeline.approve(card.id, self.pipeline.runner.token, ApprovedVia.AUTO)
        if result.outcome is Outcome.REFUSED:
            return "ask", result.message
        if result.outcome is Outcome.WENT_AWAY:
            return "fixed", "It went away on its own."
        if result.outcome is Outcome.DONE and result.verification is Verification.FIXED:
            self.tell(f"Fixed automatically: {problem.title}", f"{option.title}. Click to see what changed.")
            return "fixed", option.title
        if result.outcome is Outcome.DONE:  # ran, but there's no check that can confirm it
            self.tell(f"I tried a fix: {problem.title}", f"{option.title}. I couldn't confirm it worked. "
                                                         "Click to see what I did.")
            return "tried", result.message
        # It didn't work. The next check raises the problem again, and that one goes to the person.
        self.tell(f"Still needs you: {problem.title}", f"I tried “{option.title}” and it didn't fix it. "
                                                       "Click to see what I did.")
        return "tried", result.message
