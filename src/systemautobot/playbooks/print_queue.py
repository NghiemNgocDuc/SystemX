"""A print job stuck in the queue. Minor: it waits for the end-of-day list unless the person asks."""

from __future__ import annotations

from ..models import FixOption, ManualStep, Problem, Signal
from ..tools.fixes import admin_step, cancel_print_job_step
from .base import Playbook


class PrintQueue(Playbook):
    id = "print_queue"
    FINGERPRINT = "print_queue:any"

    def _stuck(self, jobs: list[dict]) -> dict | None:
        limit = self.tools.settings.stuck_print_after.total_seconds()
        for j in jobs:
            if j.get("error") or (j.get("age_seconds") or 0) >= limit:
                return j
        return None

    def _problem(self, jobs: list[dict], signal: Signal | None = None) -> Problem:
        job = self._stuck(jobs)
        if job is None:
            return Problem(self.id, self.FINGERPRINT, "Printing is fine.", evidence={"stuck": False}, signal=signal)
        return Problem(self.id, self.FINGERPRINT, f"A print job is stuck on {job['printer']}.",
                       evidence=dict(job, stuck=True), signal=signal)

    def match(self, signal: Signal) -> Problem | None:
        if signal.source != "check" or signal.name != "print_queue":
            return None
        return self._problem(signal.data.get("jobs", []), signal)

    def refresh(self, problem: Problem) -> Problem:
        return self._problem(self.tools.checks.print_jobs())

    def options(self, problem: Problem) -> list[FixOption]:
        ev, launcher = problem.evidence, self.tools.launcher
        return [
            FixOption(
                title="Cancel the stuck print job",
                explain=f"I'll cancel \"{ev.get('document') or 'the document'}\" on {ev.get('printer')}. "
                        "You can print it again afterwards.",
                build_steps=lambda p: [cancel_print_job_step(p.evidence["printer"], p.evidence["job_id"],
                                                             p.evidence.get("document", ""))],
                no_undo_text="You can't undo this, but you can print the document again.",
            ),
            FixOption(
                title="Restart the print service",
                explain="I'll restart the Windows print service. Windows will ask for permission.",
                build_steps=lambda p: [admin_step("restart_print_spooler", launcher)],
                needs_admin=True, auto="safe",
            ),
        ]

    def manual_steps(self, problem: Problem) -> list[ManualStep]:
        return [
            ManualStep("Check the printer is on, has paper, and shows no error light."),
            ManualStep("Open Printers, pick the printer, open the queue, and cancel the stuck job.", "ms-settings:printers"),
        ]
