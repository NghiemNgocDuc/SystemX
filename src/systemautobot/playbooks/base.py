"""What every playbook provides. One playbook per common problem."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..models import FixOption, ManualStep, Problem, Signal, StepResult, Verification
from ..safety.guard import Guard
from ..settings import Settings
from ..tools import checks as real_checks
from ..tools.fixes import AdminLauncher


@dataclass
class Toolbox:
    """Everything a playbook may use. Tests swap in fakes."""

    guard: Guard
    launcher: AdminLauncher
    settings: Settings
    checks: Any = real_checks          # module or object with the same functions
    temp_roots: list[Path] = field(default_factory=list)


class Playbook:
    id: str = ""

    def __init__(self, tools: Toolbox):
        self.tools = tools

    def match(self, signal: Signal) -> Problem | None:
        """Return a Problem for signals this playbook owns (even healthy readings,
        so triage can see a problem went away), or None if the signal isn't ours."""
        raise NotImplementedError

    def refresh(self, problem: Problem) -> Problem | None:
        """Look again right now. Return a fresh Problem, or None if this can't be checked."""
        raise NotImplementedError

    def options(self, problem: Problem) -> list[FixOption]:
        return []

    def manual_steps(self, problem: Problem) -> list[ManualStep]:
        return []

    def can_recheck(self, problem: Problem) -> bool:
        """Whether Check again can tell, by looking, that the person's own steps worked."""
        return True

    def recheck(self, problem: Problem) -> Problem | None:
        """Look again after the person's own steps. Same as refresh() unless a playbook knows better."""
        return self.refresh(problem)

    def verify(self, problem: Problem, fresh: Problem | None, results: list[StepResult]) -> tuple[Verification, str] | None:
        """Override for special cases. None means use the generic severity comparison."""
        return None
