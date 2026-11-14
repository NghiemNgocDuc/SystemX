"""The repair toolbox and the runner.

Toolbox: small step builders. Each does one thing and reports exactly what it touched.
Runner: the only way a step runs. It refuses without a valid approval, opens a
history record before the first step, re-checks the target, and closes the record
whatever happens.
"""

from __future__ import annotations

import ctypes
import hmac
import json
import logging
import secrets
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from ..history import History
from ..models import (
    ApprovedVia,
    Card,
    FixedBy,
    FixStep,
    Outcome,
    Problem,
    StepResult,
    StepStatus,
    TouchedSetting,
    Verification,
    new_id,
    utcnow,
)
from ..paths import helper_results_dir
from ..safety.guard import Guard, GuardRefusal

from .checks import format_bytes

log = logging.getLogger("systemautobot.fixes")
CREATE_NO_WINDOW = 0x08000000

# ---- non-admin commands: fixed argv, chosen by the app --------------------------

COMMANDS: dict[str, list[str]] = {
    "flush_dns": ["ipconfig", "/flushdns"],
    "renew_address": ["ipconfig", "/renew"],
}


def command_step(name: str) -> FixStep:
    argv = COMMANDS[name]  # KeyError for anything not on the list

    def run() -> StepResult:
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=120,
                                  creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        except (OSError, subprocess.TimeoutExpired) as e:
            return StepResult(StepStatus.COULDNT, f"Couldn't run {argv[0]}.", output=str(e), command=" ".join(argv))
        ok = proc.returncode == 0
        return StepResult(
            StepStatus.DONE if ok else StepStatus.COULDNT,
            {"flush_dns": "Cleared the saved web addresses.",
             "renew_address": "Asked the router for a fresh address."}[name] if ok else f"{argv[0]} failed.",
            output=(proc.stdout + proc.stderr), command=" ".join(argv), exit_code=proc.returncode,
        )

    return FixStep(tool=name, args={}, run=run)


# ---- files -----------------------------------------------------------------------

def delete_temp_step(guard: Guard, roots: list[Path]) -> FixStep:
    def run() -> StepResult:
        files, totals, freed, in_use, recent = [], {}, 0, 0, 0
        for root in roots:
            if not root.exists():
                continue
            rep = guard.delete_contents(root)
            files += rep.files
            totals[str(root)] = (rep.count, rep.bytes_freed)
            freed += rep.bytes_freed
            in_use += rep.skipped_in_use
            recent += rep.skipped_recent
        used = sum(1 for c, _ in totals.values() if c)
        summary = f"Deleted {format_bytes(freed)} of temporary files from {used} folder{'s' if used != 1 else ''}."
        if in_use:
            summary += f" {in_use} files were in use, so I left them."
        return StepResult(StepStatus.DONE, summary, files=files, folder_totals=totals)

    return FixStep(tool="delete_temp_files", args={"folders": [str(r) for r in roots]}, run=run)


def empty_recycle_bin_step() -> FixStep:
    def run() -> StepResult:
        if sys.platform != "win32":
            return StepResult(StepStatus.SKIPPED, "Not on Windows.")

        class SHQUERYRBINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_ulong), ("i64Size", ctypes.c_longlong), ("i64NumItems", ctypes.c_longlong)]

        info = SHQUERYRBINFO(ctypes.sizeof(SHQUERYRBINFO), 0, 0)
        ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info))
        if info.i64NumItems == 0:
            return StepResult(StepStatus.SKIPPED, "The Recycle Bin was already empty.")
        flags = 0x1 | 0x2 | 0x4  # no confirmation, no progress, no sound
        hr = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, flags)
        ok = hr == 0
        return StepResult(
            StepStatus.DONE if ok else StepStatus.COULDNT,
            f"Emptied the Recycle Bin: {info.i64NumItems} items, {format_bytes(info.i64Size)}." if ok
            else "Couldn't empty the Recycle Bin.",
            folder_totals={"Recycle Bin": (int(info.i64NumItems), int(info.i64Size))} if ok else {},
            exit_code=hr,
        )

    return FixStep(tool="empty_recycle_bin", args={}, run=run)


# ---- programs --------------------------------------------------------------------

def close_program_step(guard: Guard, pid: int, name: str, *, allow_explorer: bool = False) -> FixStep:
    def run() -> StepResult:
        import psutil

        try:
            proc = guard.check_close(pid, allow_explorer=allow_explorer)
        except GuardRefusal as e:
            return StepResult(StepStatus.COULDNT, str(e))
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except psutil.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied:
            return StepResult(StepStatus.COULDNT, f"Couldn't close {name}: it runs with higher rights than mine.")
        return StepResult(StepStatus.DONE, f"Closed {name}.",
                          settings=[TouchedSetting("process", name, old=f"running (pid {pid})", new="closed")])

    return FixStep(tool="close_program", args={"pid": pid, "name": name}, run=run)


def start_program_step(exe: str, name: str) -> FixStep:
    """exe comes from the process the app itself just closed, never from Grok."""

    def run() -> StepResult:
        if not exe or not Path(exe).is_file():
            return StepResult(StepStatus.COULDNT, f"Couldn't find {name} to open it again.")
        flags = (0x00000008 | 0x00000200) if sys.platform == "win32" else 0  # DETACHED | NEW_PROCESS_GROUP
        try:
            p = subprocess.Popen([exe], creationflags=flags, close_fds=True)
        except OSError as e:
            return StepResult(StepStatus.COULDNT, f"Couldn't open {name}.", output=str(e))
        return StepResult(StepStatus.DONE, f"Opened {name} again.",
                          settings=[TouchedSetting("process", name, old="closed", new=f"running (pid {p.pid})")])

    return FixStep(tool="open_program", args={"name": name}, run=run)


# ---- admin repairs through sab-helper ----------------------------------------------

class AdminLauncher:
    """Starts sab-helper elevated for one allowlisted entry and reads its result."""

    def __init__(self, results_dir: Path | None = None, timeout: float = 3600):
        self.results_dir = results_dir or helper_results_dir()
        self.timeout = timeout

    def helper_command(self, entry_id: str, run_id: str) -> tuple[str, str]:
        if getattr(sys, "frozen", False):
            exe = str(Path(sys.executable).with_name("sab-helper.exe"))
            return exe, f"{entry_id} {run_id}"
        script = Path(__file__).resolve().parents[2] / "sab_helper" / "helper.py"
        return sys.executable, f'-I "{script}" {entry_id} {run_id}'

    def run(self, entry_id: str) -> StepResult:
        if sys.platform != "win32":
            return StepResult(StepStatus.COULDNT, "Admin repairs need Windows.")
        import pywintypes
        import win32con
        import win32event
        import win32process
        from win32com.shell import shell, shellcon

        run_id = new_id()
        exe, params = self.helper_command(entry_id, run_id)
        try:
            info = shell.ShellExecuteEx(fMask=shellcon.SEE_MASK_NOCLOSEPROCESS, lpVerb="runas",
                                        lpFile=exe, lpParameters=params, nShow=win32con.SW_HIDE)
        except pywintypes.error as e:
            if e.winerror == 1223:  # ERROR_CANCELLED
                return StepResult(StepStatus.COULDNT, "You said no at the Windows prompt, so I didn't change anything.")
            return StepResult(StepStatus.COULDNT, "Couldn't start the admin helper.", output=str(e))
        handle = info["hProcess"]
        win32event.WaitForSingleObject(handle, int(self.timeout * 1000))
        code = win32process.GetExitCodeProcess(handle)
        return self.read_result(run_id, code)

    def read_result(self, run_id: str, exit_code: int) -> StepResult:
        path = self.results_dir / f"{run_id}.json"
        if not path.is_file():
            return StepResult(StepStatus.COULDNT, "The admin helper finished without a result.", exit_code=exit_code)
        data = json.loads(path.read_text(encoding="utf-8"))
        cmds = data.get("steps", [])
        status = StepStatus.DONE if data.get("status") == "done" else StepStatus.COULDNT
        return StepResult(
            status, data.get("summary", ""),
            output="\n".join(c.get("output", "") for c in cmds),
            command="; ".join(c.get("command", "") for c in cmds),
            exit_code=next((c["exit_code"] for c in cmds if c.get("exit_code")), 0),
            settings=[TouchedSetting(**s) for s in data.get("settings", [])],
            undo_data=data.get("undo"), needs_restart=bool(data.get("needs_restart")),
        )


def admin_step(entry_id: str, launcher: AdminLauncher) -> FixStep:
    return FixStep(tool=f"admin:{entry_id}", args={}, run=lambda: launcher.run(entry_id), needs_admin=True)


# ---- runner ------------------------------------------------------------------------

@dataclass
class RunResult:
    outcome: Outcome
    message: str
    fix_id: str | None = None
    verification: Verification | None = None


class Runner:
    def __init__(self, history: History, *, paused: bool = True, max_attempts_per_day: int = 2,
                 clock: Callable[[], datetime] = utcnow):
        self.history = history
        self.paused = paused
        self.max_attempts = max_attempts_per_day
        self._clock = clock
        self.token = secrets.token_urlsafe(32)  # per launch, memory only
        self.cards: dict[str, Card] = {}
        self._busy = threading.Lock()

    def register(self, card: Card) -> Card:
        self.cards[card.id] = card
        return card

    def _refuse(self, reason: str, card: Card | None, card_id: str | None = None) -> RunResult:
        self.history.add_refusal(reason, card_id=card.id if card else card_id,
                                 fingerprint=card.problem.fingerprint if card else None)
        return RunResult(Outcome.REFUSED, reason)

    def execute(
        self,
        card_id: str,
        token: str,
        via: ApprovedVia,
        *,
        refresh: Callable[[Problem], Problem | None],
        verify: Callable[[Problem, list[StepResult]], tuple[Verification, str]],
        fixed_by: FixedBy = FixedBy.PLAYBOOK,
        grok: dict | None = None,
    ) -> RunResult:
        card = self.cards.get(card_id)
        if not isinstance(token, str) or not hmac.compare_digest(token, self.token):
            return self._refuse("Refused: the approval didn't come from this app.", card, card_id)
        if card is None:
            return self._refuse("Refused: no such card.", None, card_id)
        if card.option is None:
            return self._refuse("Refused: this card has no repair to run.", card)
        if card.used:
            return self._refuse("Refused: this card was already used.", card)
        if card.expired(self._clock()):
            return self._refuse("Refused: this card expired. I'll check again first.", card)
        if self.paused:
            return self._refuse("Fixes are paused, so I didn't change anything.", card)
        if self.history.attempts_today(card.problem.fingerprint) >= self.max_attempts:
            return self._refuse("I already tried twice today. Here's how to fix it yourself.", card)
        if not self._busy.acquire(blocking=False):
            return self._refuse("Another repair is running. This one will wait.", card)
        try:
            card.used = True
            approved_at = self._clock()
            fresh = refresh(card.problem)
            if fresh is None:
                fix_id = self.history.open_fix(card.problem, fixed_by=FixedBy.NOBODY, approved_via=via,
                                               approved_at=approved_at, card_id=card.id,
                                               option_title=card.option.title)
                self.history.close_fix(fix_id, Outcome.WENT_AWAY, "It went away on its own, so I didn't change anything.")
                return RunResult(Outcome.WENT_AWAY, "It went away on its own.", fix_id)
            fix_id = self.history.open_fix(fresh, fixed_by=fixed_by, approved_via=via, approved_at=approved_at,
                                           card_id=card.id, option_title=card.option.title, grok=grok)
            return self._run_steps(fix_id, card, fresh, verify)
        finally:
            self._busy.release()

    def _run_steps(self, fix_id, card, problem, verify) -> RunResult:
        results: list[StepResult] = []
        steps = card.option.build_steps(problem)
        for idx, step in enumerate(steps):
            started = self._clock()
            try:
                result = step.run()
            except Exception as e:  # stop the rest, keep finished steps undoable
                ref = f"traceback:{fix_id}:{idx}"
                log.exception("Repair step failed [%s]", ref)
                result = StepResult(StepStatus.COULDNT, f"Stopped: {type(e).__name__} during {step.tool}.",
                                    output=str(e))
                self.history.add_step(fix_id, idx, step, result, started, self._clock())
                summary = " ".join(r.summary for r in results + [result])
                self.history.close_fix(fix_id, Outcome.COULDNT, summary, traceback_ref=ref)
                return RunResult(Outcome.COULDNT, summary, fix_id)
            self.history.add_step(fix_id, idx, step, result, started, self._clock())
            results.append(result)
            if result.status is StepStatus.COULDNT:
                summary = " ".join(r.summary for r in results)
                self.history.close_fix(fix_id, Outcome.COULDNT, summary)
                return RunResult(Outcome.COULDNT, summary, fix_id)
        summary = " ".join(r.summary for r in results)
        self.history.close_fix(fix_id, Outcome.DONE, summary)
        if any(r.needs_restart for r in results):
            verdict, detail = Verification.WAITING_RESTART, "I'll check again after the next restart."
        else:
            verdict, detail = verify(problem, results)
        self.history.add_verification(fix_id, verdict, detail)
        return RunResult(Outcome.DONE, summary + (" " + detail if detail else ""), fix_id, verdict)
