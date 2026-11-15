"""A real, bounded cleanup exercise, registered only by the authenticated demo lab.

The lab creates its own files. Paths and sizes never come from an HTTP request.
Cleanup goes through the ordinary approval runner, file guard and fix history.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import threading
import uuid

from .models import FixOption, FixStep, Problem, Signal, StepResult, StepStatus, Verification
from .playbooks.base import Playbook, Toolbox
from .safety.guard import Guard, GuardRefusal, _is_reparse

FILE_BYTES = 4 * 1024 * 1024
FILE_COUNT = 6


@dataclass
class CleanupRun:
    id: str
    folder: Path
    files: dict[Path, tuple] = field(default_factory=dict)
    before_bytes: int = 0
    card_id: str | None = None


def _identity(path: Path) -> tuple:
    st = path.stat()
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns


class DemoCleanup(Playbook):
    id = "demo_cleanup"

    def __init__(self, tools: Toolbox, root: Path):
        super().__init__(tools)
        self.root = root.absolute()
        self.runs: dict[str, CleanupRun] = {}
        self.lock = threading.RLock()

    def _safe_folder(self, folder: Path) -> None:
        if folder != self.root and folder.parent != self.root:
            raise GuardRefusal("The cleanup folder is outside this demo.")
        # Check every component before creating, inspecting or deleting files.
        if any(_is_reparse(p) for p in (folder, *folder.parents)):
            raise GuardRefusal("The demo folder contains a link or junction; nothing was removed.")
        if folder.resolve() != folder:
            raise GuardRefusal("The demo folder no longer points to its original location.")

    def prepare(self) -> CleanupRun:
        with self.lock:
            if not self.tools.settings.demo:
                raise GuardRefusal("Cleanup demos require demo mode.")
            for run in self.runs.values():
                if self.snapshot(run.id)["remaining_bytes"]:
                    return run
            self._safe_folder(self.root)
            self.root.mkdir(parents=True, exist_ok=True)
            run = CleanupRun(uuid.uuid4().hex, self.root / uuid.uuid4().hex)
            self._safe_folder(run.folder)
            run.folder.mkdir(exist_ok=False)
            self.runs[run.id] = run
            # Write real bytes, not sparse-file lengths or a low-disk simulation.
            for i in range(FILE_COUNT):
                path = run.folder / f"disposable-cache-{i + 1}.bin"
                with path.open("xb") as handle:
                    for _ in range(FILE_BYTES // (1024 * 1024)):
                        handle.write(os.urandom(1024 * 1024))
                run.files[path] = _identity(path)
                run.before_bytes += path.stat().st_size
            return run

    def snapshot(self, run_id: str) -> dict:
        with self.lock:
            run = self.runs[run_id]
            self._safe_folder(run.folder)
            remaining, count = 0, 0
            for path in run.files:
                if _is_reparse(path):
                    raise GuardRefusal("A demo file was replaced by a link; nothing will be removed.")
                if path.exists():
                    if not path.is_file():
                        raise GuardRefusal("A demo file was replaced; cleanup is stopped.")
                    remaining += path.stat().st_size
                    count += 1
            return {"run_id": run.id, "card_id": run.card_id, "folder": str(run.folder),
                    "before_bytes": run.before_bytes, "remaining_bytes": remaining,
                    "removed_bytes": max(0, run.before_bytes - remaining),
                    "files_remaining": count, "files_created": len(run.files)}

    def match(self, signal: Signal) -> Problem | None:
        if not self.tools.settings.demo or signal.source != "demo" or signal.name != self.id:
            return None
        if signal.data.get("run_id") not in self.runs:
            return None
        data = self.snapshot(signal.data["run_id"])
        return Problem(self.id, f"{self.id}:{data['run_id']}",
                       f"Demo files are using {data['remaining_bytes'] / 1024 ** 2:.0f} MiB.",
                       evidence=data, signal=signal, source="demo")

    def refresh(self, problem: Problem) -> Problem | None:
        return self.match(Signal("demo", self.id, {"run_id": problem.evidence["run_id"]}))

    def options(self, problem: Problem) -> list[FixOption]:
        # Capture the registered run, never a folder supplied in problem evidence.
        run = self.runs[problem.evidence["run_id"]]
        return [FixOption(
            title="Remove disposable demo files",
            explain=f"I'll remove only the {len(run.files)} test files this demo created, then measure what's left.",
            warnings=["These disposable test files will be permanently deleted."],
            no_undo_text="You can create a new set by starting the demo again.",
            build_steps=lambda p: [self._step(run)],
        )]

    def _step(self, run: CleanupRun) -> FixStep:
        def clean() -> StepResult:
            with self.lock:
                self._safe_folder(run.folder)
                if not run.folder.is_dir():
                    raise GuardRefusal("The demo folder is missing; cleanup is stopped.")
                guard = Guard([run.folder], [])
                # Added files, modified files, links and nested folders stop the whole
                # operation. Only this run's unchanged, disposable files may be removed.
                present = set(run.folder.iterdir())
                if present != set(run.files):
                    raise GuardRefusal("The demo folder changed; no files were removed.")
                for path in present:
                    guard.check_delete(path)
                    if path not in run.files or not path.is_file() or path.stat().st_nlink != 1:
                        raise GuardRefusal("The demo folder changed; no files were removed.")
                    if _identity(path) != run.files[path]:
                        raise GuardRefusal("A demo file changed; no files were removed.")
                report = guard.delete_known_files(run.files)
                amount = report.bytes_freed / 1024 ** 2
                return StepResult(StepStatus.DONE,
                                  f"Removed {amount:.1f} MiB from {report.count} disposable demo files.",
                                  files=report.files,
                                  folder_totals={str(run.folder): (report.count, report.bytes_freed)})

        return FixStep("delete_demo_files", {"folder": str(run.folder), "run_id": run.id}, clean)

    def verify(self, problem, fresh, results):
        if fresh is None:
            return Verification.CANT_CHECK, "The demo folder could not be checked."
        remaining = fresh.evidence["remaining_bytes"]
        if remaining:
            return Verification.NOT_FIXED, f"{remaining / 1024 ** 2:.1f} MiB of demo files remain."
        removed = sum(f.size for result in results for f in result.files if f.action == "deleted")
        return Verification.FIXED, f"Re-check: no demo files remain. This repair removed {removed / 1024 ** 2:.1f} MiB."
