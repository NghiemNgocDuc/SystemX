from __future__ import annotations

import os
from pathlib import Path

from ..models import FixOption, ManualStep, Problem, Signal
from ..tools.fixes import close_program_step, start_program_step
from .base import Playbook


def _display(name: str) -> str:
    return Path(name).stem if name else "A program"


class FrozenApp(Playbook):
    id = "frozen_app"

    def match(self, signal: Signal) -> Problem | None:
        if signal.source != "check" or signal.name != "hung_window":
            return None
        return self._problem(signal.data, hung=signal.data.get("hung", True), signal=signal)

    def _problem(self, d: dict, hung: bool, signal: Signal | None = None) -> Problem:
        name = d.get("name", "")
        is_explorer = name.lower() == "explorer.exe"
        title = ("The taskbar or File Explorer is stuck." if is_explorer
                 else f"{_display(name)} is not responding.")
        return Problem(self.id, f"{self.id}:{name.lower()}", title,
                       evidence=dict(d, hung=hung, explorer=is_explorer), signal=signal)

    def refresh(self, problem: Problem) -> Problem:
        pid = problem.evidence.get("pid")
        name = problem.evidence.get("name", "").lower()
        for w in self.tools.checks.hung_windows():
            if w["pid"] == pid or w["name"].lower() == name:
                return self._problem(dict(problem.evidence, **w), hung=True)
        return self._problem(problem.evidence, hung=False)

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
            )]
        return [FixOption(
            title=f"Close and reopen {_display(name)}",
            explain=f"I'll close {_display(name)} and open it again.",
            warnings=[f"Anything you haven't saved in {_display(name)} will be lost."],
            build_steps=lambda p: [close_program_step(guard, p.evidence["pid"], name),
                                   start_program_step(ev.get("exe", ""), _display(name))],
        )]

    def manual_steps(self, problem: Problem) -> list[ManualStep]:
        return [
            ManualStep("Wait a minute. Programs sometimes recover on their own."),
            ManualStep("Press Ctrl+Shift+Esc, pick the program, and press End task."),
        ]
