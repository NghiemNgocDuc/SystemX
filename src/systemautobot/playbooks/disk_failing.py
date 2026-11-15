"""A failing disk. Software can't fix hardware, and write-heavy repairs can make data loss
worse, so this card never has a fix button: it says back up now."""

from __future__ import annotations

from ..models import ManualStep, Problem, Signal
from .base import Playbook

# (provider, event id) pairs Windows logs when a disk is going bad.
DISK_EVENTS = {("disk", 7), ("disk", 51), ("disk", 153), ("Ntfs", 55), ("Microsoft-Windows-Ntfs", 55)}


class DiskFailing(Playbook):
    id = "disk_failing"

    def match(self, signal: Signal) -> Problem | None:
        if signal.source == "check" and signal.name == "disk_health":
            return self._health(signal.data.get("disks", []), signal)
        if signal.source != "event":
            return None
        key = (signal.data.get("provider", ""), signal.data.get("event_id"))
        if key not in DISK_EVENTS:
            return None
        return Problem(self.id, f"{self.id}:disk", "Your disk may be failing. Back up your files now.",
                       evidence={"provider": key[0], "event_id": key[1], "failing": True}, signal=signal)

    def _health(self, disks: list[dict], signal: Signal | None = None) -> Problem:
        """The drive's own verdict. Its own fingerprint, so a healthy reading never clears an
        alarm raised by disk error events."""
        bad = [d for d in disks if d.get("health") and d["health"].lower() != "healthy"]
        if not bad:
            return Problem(self.id, f"{self.id}:health", "Your disks report themselves healthy.",
                           evidence={"failing": False}, signal=signal)
        d = bad[0]
        return Problem(self.id, f"{self.id}:health",
                       f"Your disk ({d['name']}) reports its health as {d['health']}. Back up your files now.",
                       evidence={"failing": True, "disk": d["name"], "health": d["health"]}, signal=signal)

    def refresh(self, problem: Problem) -> Problem:
        if problem.fingerprint.endswith(":health"):
            return self._health(self.tools.checks.disk_health())
        return problem  # a failing disk doesn't heal; it stays until the person deals with it

    def can_recheck(self, problem: Problem) -> bool:
        return problem.fingerprint.endswith(":health")  # past error events can't be looked at again

    def manual_steps(self, problem: Problem) -> list[ManualStep]:
        return [
            ManualStep("Back up your files now, to an external drive or cloud storage.", "ms-settings:backup"),
            ManualStep("Don't install or update anything until the backup is done."),
            ManualStep("Ask a repair shop or your PC maker to check the drive and replace it if needed."),
        ]
