"""Error messages on screen: a program shows an error box. The person is looking at it right now,
so the card shows on Home at once with "Fix for me"; it never pops up a second window.

The box's text is untrusted. It stays in signal.text, which the AI receives wrapped as data.
It never reaches the title, the fingerprint text, or the evidence."""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..models import ManualStep, Problem, Signal, Verification
from .base import Playbook


class ErrorDialog(Playbook):
    id = "error_dialog"

    def match(self, signal: Signal) -> Problem | None:
        if signal.source != "check" or signal.name != "error_dialog":
            return None
        d = signal.data
        name = d.get("name") or ""
        app = Path(name).stem or "A program"
        digest = hashlib.sha1((name + "|" + signal.text).encode("utf-8", "replace")).hexdigest()[:10]
        return Problem(self.id, f"{self.id}:{name.lower()}:{digest}", f"{app} showed an error message.",
                       evidence={"app": name, "seen": True}, signal=signal)

    def refresh(self, problem: Problem) -> Problem:
        return problem  # the person may have closed the box; the error is still worth explaining

    def recheck(self, problem: Problem) -> Problem | None:
        """After the person's steps: is that same error box still on screen? Gone means sorted;
        if it comes back, the watcher reports it again."""
        for d in getattr(self.tools.checks, "error_dialogs", lambda: [])():
            text = d.get("text", "")
            data = {k: v for k, v in d.items() if k != "text"}
            again = self.match(Signal("check", "error_dialog", data, text=text))
            if again and again.fingerprint == problem.fingerprint:
                return problem
        return None

    def verify(self, problem, fresh, results):
        return Verification.CANT_CHECK, "I can't check this one automatically. Tell me if the error comes back."

    def manual_steps(self, problem: Problem) -> list[ManualStep]:
        return [
            ManualStep("Press Fix for me to have the AI look into it, or copy the message into Ask."),
            ManualStep("Close and reopen the program, then try again."),
            ManualStep("If it keeps happening, update or reinstall the program.", "ms-settings:appsfeatures"),
        ]
