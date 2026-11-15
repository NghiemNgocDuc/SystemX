from __future__ import annotations

from ..models import FixOption, ManualStep, Problem, Signal
from ..tools.checks import format_bytes
from ..tools.fixes import delete_temp_step, empty_recycle_bin_step
from .base import Playbook


class DiskSpace(Playbook):
    id = "disk_space"

    def match(self, signal: Signal) -> Problem | None:
        if signal.source != "check" or signal.name != "disk_space":
            return None
        return self._problem(signal.data, signal)

    def _problem(self, d: dict, signal: Signal | None = None) -> Problem:
        return Problem(
            playbook_id=self.id,
            fingerprint=f"{self.id}:{d['drive']}",
            title=f"Your {d['drive']} drive is almost full ({format_bytes(d['free'])} left).",
            evidence=dict(d),
            signal=signal,
        )

    def refresh(self, problem: Problem) -> Problem:
        return self._problem(self.tools.checks.disk_space(problem.evidence["drive"] + "\\"))

    def options(self, problem: Problem) -> list[FixOption]:
        roots = self.tools.temp_roots
        return [
            FixOption(
                title="Delete temporary files",
                explain="I'll delete temporary files and app caches that Windows and your apps rebuild on their own.",
                build_steps=lambda p: [delete_temp_step(self.tools.guard, roots)],
                undo_possible=False, auto="safe",
            ),
            FixOption(
                title="Empty the Recycle Bin",
                explain="I'll permanently delete what's in the Recycle Bin.",
                build_steps=lambda p: [empty_recycle_bin_step()],
                warnings=["Anything in the Recycle Bin will be gone for good."],
                undo_possible=False,
            ),
        ]

    def manual_steps(self, problem: Problem) -> list[ManualStep]:
        return [
            ManualStep("Open Storage settings to see what's using the space.", "ms-settings:storagesense"),
            ManualStep("Move big videos or downloads you want to keep to an external drive or cloud storage."),
            ManualStep("Uninstall apps you don't use.", "ms-settings:appsfeatures"),
        ]
