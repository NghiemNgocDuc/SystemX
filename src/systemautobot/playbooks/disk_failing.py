"""A failing disk. Software can't repair hardware, and write-heavy repairs can make data loss worse.
The fix does what software safely can: opens Windows Backup, then has Windows check the drive and
repair file errors (an online scan, not a full surface scan). It never claims the drive is cured."""

from __future__ import annotations

from ..models import FixOption, ManualStep, Problem, Signal, StepStatus, Verification
from ..tools.fixes import admin_step, open_page_step
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

    def options(self, problem: Problem) -> list[FixOption]:
        launcher, opener = self.tools.launcher, self.tools.open_page
        return [FixOption(
            title="Check the drive and open Backup",
            explain="I'll open Windows Backup so you can save your files, then have Windows check the drive and "
                    "repair the file errors it can. Windows will ask for permission.",
            warnings=["Start the backup first. A worn-out drive can't be repaired by software and may stop at any time."],
            build_steps=lambda p: [open_page_step("ms-settings:backup", opener), admin_step("check_disk", launcher)],
            needs_admin=True, no_undo_text="Nothing to undo: this checks the drive and repairs file errors.",
            auto="safe",
        )]

    def verify(self, problem, fresh, results):
        if problem.fingerprint.endswith(":health"):
            return None  # the drive's own health is read again and compared
        health = ""
        try:
            disks = self.tools.checks.disk_health()
            bad = [d for d in disks if d.get("health") and d["health"].lower() != "healthy"]
            if bad:
                health = f" Windows reports the drive's health as {bad[0]['health']}: plan to replace it."
            elif disks:
                health = " Windows reports the drive itself as healthy."
        except Exception:  # the health read is extra information, never a reason to fail
            pass
        checked = any(r.status is StepStatus.DONE and r.command and "chkdsk" in r.command.lower() for r in results)
        return Verification.CANT_CHECK, (("Windows checked the drive." if checked else "") + health
                                         + " Software can't undo wear, so finish the backup. I'll tell you if new "
                                           "disk errors appear.").strip()

    def can_recheck(self, problem: Problem) -> bool:
        return problem.fingerprint.endswith(":health")  # past error events can't be looked at again

    def manual_steps(self, problem: Problem) -> list[ManualStep]:
        return [
            ManualStep("Back up your files now, to an external drive or cloud storage.", "ms-settings:backup"),
            ManualStep("Don't install or update anything until the backup is done."),
            ManualStep("Ask a repair shop or your PC maker to check the drive and replace it if needed."),
        ]
