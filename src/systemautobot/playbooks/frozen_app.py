from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from ..models import FixOption, ManualStep, Problem, Signal
from ..tools.fixes import close_program_step, start_program_step
from .base import Playbook


def _display(name: str) -> str:
    return Path(name).stem if name else "A program"


class FrozenApp(Playbook):
    id = "frozen_app"

    def __init__(self, tools):
        super().__init__(tools)
        self._demo_pids: set[int] = set()

    def match(self, signal: Signal) -> Problem | None:
        if signal.source != "check" or signal.name != "hung_window":
            return None
        return self._problem(signal.data, hung=signal.data.get("hung", True), signal=signal)

    def _problem(self, d: dict, hung: bool, signal: Signal | None = None) -> Problem:
        name = d.get("name", "")
        is_explorer = name.lower() == "explorer.exe"
        from ..demo_support import is_demo_notes

        pid = d.get("pid", 0)
        demo_notes = bool(self.tools.settings.demo and
                          (is_demo_notes(pid) or (not hung and pid in self._demo_pids)))
        if demo_notes and hung:
            self._demo_pids.add(pid)
        elif not hung:
            self._demo_pids.discard(pid)
        title = ("The taskbar or File Explorer is stuck." if is_explorer
                 else f"{'Demo Notes' if demo_notes else _display(name)} is not responding.")
        fingerprint = f"{self.id}:demo_notes:{d['pid']}" if demo_notes else f"{self.id}:{name.lower()}"
        return Problem(self.id, fingerprint, title,
                       evidence=dict(d, hung=hung, explorer=is_explorer, demo_notes=demo_notes), signal=signal)

    def refresh(self, problem: Problem) -> Problem:
        pid = problem.evidence.get("pid")
        name = problem.evidence.get("name", "").lower()
        for w in self.tools.checks.hung_windows():
            if w["pid"] == pid or (not problem.evidence.get("demo_notes") and w["name"].lower() == name):
                return self._problem(dict(problem.evidence, **w), hung=True)
        return replace(problem, evidence=dict(problem.evidence, hung=False))

    def options(self, problem: Problem) -> list[FixOption]:
        ev, guard = problem.evidence, self.tools.guard
        name = ev.get("name", "")
        if ev.get("explorer"):
            explorer = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "explorer.exe")
            return [FixOption(
                title="Restart Windows Explorer",
                explain="I'll restart the taskbar and File Explorer. Your files and open programs aren't affected.",
                build_steps=lambda p: [close_program_step(guard, p.evidence["pid"], name, allow_explorer=True),
                                       start_program_step(explorer, "Windows Explorer")],
                auto="safe",
            )]
        from ..demo_support import reopen_demo_notes_step

        demo_notes = ev.get("demo_notes") and self.tools.settings.demo
        display = "Demo Notes" if demo_notes else _display(name)
        return [FixOption(
            title=f"Close and reopen {display}",
            explain=f"I'll close {display} and open it again.",
            warnings=[f"Anything you haven't saved in {display} will be lost."],
            build_steps=lambda p: [close_program_step(guard, p.evidence["pid"], name),
                                   reopen_demo_notes_step() if demo_notes else start_program_step(ev.get("exe", ""), display)],
            auto="unsaved",
        )]

    def manual_steps(self, problem: Problem) -> list[ManualStep]:
        return [
            ManualStep("Wait a minute. Programs sometimes recover on their own."),
            ManualStep("Press Ctrl+Shift+Esc, pick the program, and press End task."),
        ]
