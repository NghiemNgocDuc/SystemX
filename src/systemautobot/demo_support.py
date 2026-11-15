"""Recognize and reopen our one fixed test program, only in demo mode.

No command line from a window, event, or AI is ever passed to Popen.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

from .models import FixStep, StepResult, StepStatus, TouchedSetting

NOTES_SCRIPT = Path(__file__).with_name("demo_notes.py").resolve()


def _interpreters() -> set[Path]:
    paths = {Path(sys.executable).resolve(), Path(getattr(sys, "_base_executable", sys.executable)).resolve()}
    if sys.platform == "win32":
        for path in list(paths):
            paths.update(path.with_name(name) for name in ("python.exe", "pythonw.exe"))
    return paths


def notes_command(*, freeze: bool = False) -> list[str]:
    # Bypass the Windows venv launcher: its Popen PID belongs to a wrapper, not the
    # window the watcher detects. This stdlib-only test needs no venv packages.
    exe = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
    windowed = exe.with_name("pythonw.exe")
    if sys.platform == "win32" and windowed.is_file():
        exe = windowed
    return [str(exe), str(NOTES_SCRIPT)] + (["--freeze"] if freeze else [])


def is_demo_notes(pid: int) -> bool:
    import psutil

    if getattr(sys, "frozen", False):
        return False  # this source demo needs a Python interpreter
    try:
        proc = psutil.Process(pid)
        argv = proc.cmdline()
        interpreters = _interpreters()
        return (Path(proc.exe()).resolve() in interpreters
                and len(argv) in (2, 3)
                and Path(argv[0]).resolve() in interpreters
                and Path(argv[1]).resolve() == NOTES_SCRIPT
                and (len(argv) == 2 or argv[2] == "--freeze"))
    except (psutil.Error, OSError, ValueError):
        return False


def reopen_demo_notes_step() -> FixStep:
    def run() -> StepResult:
        flags = (0x00000008 | 0x00000200) if sys.platform == "win32" else 0
        try:
            p = subprocess.Popen(notes_command(), creationflags=flags, close_fds=True)
        except OSError as e:
            return StepResult(StepStatus.COULDNT, "Couldn't reopen Demo Notes.", output=str(e))
        return StepResult(StepStatus.DONE, "Opened Demo Notes again. Try typing in the new window.",
                          settings=[TouchedSetting("process", "Demo Notes", "closed", f"running (pid {p.pid})")])

    return FixStep(tool="open_program", args={"name": "Demo Notes"}, run=run)
