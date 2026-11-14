"""Fix history: every fix, who did it, each step, and what on this PC it touched.

Append-only. SQLite triggers abort any UPDATE or DELETE, so a record can only grow:
later events (close, verify, undo) are new rows linked by fix_id. Retention cleanup,
when it is added, must be its own audited path.
"""

from __future__ import annotations

import gzip
import json
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from .models import (
    ApprovedVia,
    FixedBy,
    FixStep,
    Outcome,
    Problem,
    StepResult,
    TouchedFile,
    TouchedSetting,
    Verification,
    new_id,
    utcnow,
)
from .paths import AppPaths
from .safety.redact import redact, trim

# Above this many files in one step, the full list goes to a gzip sidecar.
INLINE_FILE_LIMIT = 200
SIDECAR_FILE_CAP = 100_000

_TABLES = {
    "fixes": """
        id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, playbook_id TEXT NOT NULL,
        title TEXT NOT NULL, severity TEXT NOT NULL, source TEXT NOT NULL,
        detected_at TEXT NOT NULL, approved_at TEXT, approved_via TEXT, fixed_by TEXT NOT NULL,
        card_id TEXT, option_title TEXT, started_at TEXT NOT NULL,
        grok_model TEXT, grok_rounds INTEGER, grok_tokens INTEGER, grok_reason TEXT
    """,
    "steps": """
        fix_id TEXT NOT NULL, idx INTEGER NOT NULL, tool TEXT NOT NULL, args TEXT NOT NULL,
        needs_admin INTEGER NOT NULL, started_at TEXT NOT NULL, finished_at TEXT NOT NULL,
        status TEXT NOT NULL, summary TEXT NOT NULL, command TEXT, exit_code INTEGER, output TEXT
    """,
    "touched_files": """
        fix_id TEXT NOT NULL, step_idx INTEGER NOT NULL, path TEXT NOT NULL,
        action TEXT NOT NULL, size INTEGER, modified REAL
    """,
    "folder_totals": """
        fix_id TEXT NOT NULL, step_idx INTEGER NOT NULL, folder TEXT NOT NULL,
        count INTEGER NOT NULL, bytes INTEGER NOT NULL
    """,
    "file_lists": """
        fix_id TEXT NOT NULL, step_idx INTEGER NOT NULL, sidecar TEXT NOT NULL,
        total INTEGER NOT NULL, stored INTEGER NOT NULL
    """,
    "touched_settings": """
        fix_id TEXT NOT NULL, step_idx INTEGER NOT NULL, kind TEXT NOT NULL,
        name TEXT NOT NULL, old TEXT, new TEXT
    """,
    "undo_data": "fix_id TEXT NOT NULL, step_idx INTEGER NOT NULL, data TEXT NOT NULL",
    "closures": """
        fix_id TEXT PRIMARY KEY, finished_at TEXT NOT NULL, outcome TEXT NOT NULL,
        summary TEXT NOT NULL, traceback_ref TEXT
    """,
    "verifications": "fix_id TEXT NOT NULL, at TEXT NOT NULL, result TEXT NOT NULL, detail TEXT",
    "undos": "undo_fix_id TEXT NOT NULL, original_fix_id TEXT NOT NULL, at TEXT NOT NULL",
    "manual": "fix_id TEXT NOT NULL, steps TEXT NOT NULL, opened TEXT NOT NULL, note TEXT",
    "refusals": """
        at TEXT NOT NULL, card_id TEXT, fingerprint TEXT, reason TEXT NOT NULL, detail TEXT
    """,
}


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


class History:
    def __init__(self, paths: AppPaths):
        self.paths = paths.ensure()
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(paths.history_db), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._migrate()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ---- schema -----------------------------------------------------------

    def _migrate(self) -> None:
        with self._lock:
            for name, cols in _TABLES.items():
                self._db.execute(f"CREATE TABLE IF NOT EXISTS {name} ({cols})")
                for op in ("UPDATE", "DELETE"):
                    self._db.execute(
                        f"""CREATE TRIGGER IF NOT EXISTS {name}_no_{op.lower()}
                        BEFORE {op} ON {name}
                        BEGIN SELECT RAISE(ABORT, 'history is append-only'); END"""
                    )
            self._db.execute("CREATE INDEX IF NOT EXISTS fixes_fp ON fixes(fingerprint)")
            self._db.execute("CREATE INDEX IF NOT EXISTS files_path ON touched_files(path)")

    def _exec(self, sql: str, params: Iterable[Any] = ()) -> None:
        with self._lock:
            self._db.execute(sql, tuple(params))

    def _query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._db.execute(sql, tuple(params)))

    # ---- writing ----------------------------------------------------------

    def open_fix(
        self,
        problem: Problem,
        *,
        fixed_by: FixedBy,
        approved_via: ApprovedVia | None,
        approved_at: datetime | None = None,
        card_id: str | None = None,
        option_title: str | None = None,
        grok: dict[str, Any] | None = None,
    ) -> str:
        fix_id = new_id()
        grok = grok or {}
        self._exec(
            "INSERT INTO fixes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                fix_id, problem.fingerprint, problem.playbook_id, redact(problem.title),
                problem.severity.value, problem.source, _iso(problem.detected_at),
                _iso(approved_at), approved_via.value if approved_via else None, fixed_by.value,
                card_id, option_title, _iso(utcnow()),
                grok.get("model"), grok.get("rounds"), grok.get("tokens"),
                trim(grok.get("reason"), 300) if grok.get("reason") else None,
            ),
        )
        return fix_id

    def add_step(
        self, fix_id: str, idx: int, step: FixStep, result: StepResult,
        started: datetime, finished: datetime,
    ) -> None:
        self._exec(
            "INSERT INTO steps VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                fix_id, idx, step.tool, redact(json.dumps(step.args, default=str)),
                int(step.needs_admin), _iso(started), _iso(finished), result.status.value,
                trim(result.summary, 500), redact(result.command) if result.command else None,
                result.exit_code, trim(result.output, 500),
            ),
        )
        self._add_files(fix_id, idx, result.files)
        for folder, (count, size) in result.folder_totals.items():
            self._exec("INSERT INTO folder_totals VALUES (?,?,?,?,?)", (fix_id, idx, folder, count, size))
        self._add_settings(fix_id, idx, result.settings)
        if result.undo_data is not None:
            self._exec("INSERT INTO undo_data VALUES (?,?,?)", (fix_id, idx, json.dumps(result.undo_data)))

    def _add_files(self, fix_id: str, idx: int, files: list[TouchedFile]) -> None:
        if not files:
            return
        if len(files) <= INLINE_FILE_LIMIT:
            with self._lock:
                self._db.executemany(
                    "INSERT INTO touched_files VALUES (?,?,?,?,?,?)",
                    [(fix_id, idx, f.path, f.action, f.size, f.modified) for f in files],
                )
            return
        sidecar = self.paths.history_dir / f"{fix_id}.step{idx}.files.jsonl.gz"
        stored = files[:SIDECAR_FILE_CAP]
        with gzip.open(sidecar, "wt", encoding="utf-8") as fh:
            for f in stored:
                fh.write(json.dumps({"path": f.path, "action": f.action, "size": f.size, "modified": f.modified}) + "\n")
        self._exec(
            "INSERT INTO file_lists VALUES (?,?,?,?,?)",
            (fix_id, idx, str(sidecar), len(files), len(stored)),
        )

    def _add_settings(self, fix_id: str, idx: int, settings: list[TouchedSetting]) -> None:
        for s in settings:
            self._exec(
                "INSERT INTO touched_settings VALUES (?,?,?,?,?,?)",
                (fix_id, idx, s.kind, s.name, redact(s.old) if s.old else s.old, redact(s.new) if s.new else s.new),
            )

    def close_fix(self, fix_id: str, outcome: Outcome, summary: str, traceback_ref: str | None = None) -> None:
        self._exec(
            "INSERT INTO closures VALUES (?,?,?,?,?)",
            (fix_id, _iso(utcnow()), outcome.value, trim(summary, 500), traceback_ref),
        )

    def add_verification(self, fix_id: str, result: Verification, detail: str = "") -> None:
        self._exec("INSERT INTO verifications VALUES (?,?,?,?)", (fix_id, _iso(utcnow()), result.value, trim(detail, 500)))

    def link_undo(self, undo_fix_id: str, original_fix_id: str) -> None:
        self._exec("INSERT INTO undos VALUES (?,?,?)", (undo_fix_id, original_fix_id, _iso(utcnow())))

    def add_manual(self, fix_id: str, steps: list[str], opened: list[str], note: str | None = None) -> None:
        self._exec(
            "INSERT INTO manual VALUES (?,?,?,?)",
            (fix_id, json.dumps(steps), json.dumps(opened), trim(note, 500) if note else None),
        )

    def add_refusal(self, reason: str, *, card_id: str | None = None, fingerprint: str | None = None, detail: str = "") -> None:
        self._exec(
            "INSERT INTO refusals VALUES (?,?,?,?,?)",
            (_iso(utcnow()), card_id, fingerprint, reason, trim(detail, 500)),
        )

    # ---- reading ----------------------------------------------------------

    def unclosed(self) -> list[str]:
        """Fixes that started but never closed: the app died mid-repair."""
        rows = self._query(
            "SELECT f.id FROM fixes f LEFT JOIN closures c ON c.fix_id = f.id WHERE c.fix_id IS NULL"
        )
        return [r["id"] for r in rows]

    def count_fixed(self, fingerprint: str, within: timedelta = timedelta(days=14)) -> int:
        since = _iso(utcnow() - within)
        rows = self._query(
            """SELECT COUNT(*) AS n FROM fixes f JOIN closures c ON c.fix_id = f.id
               WHERE f.fingerprint = ? AND c.outcome = 'done' AND c.finished_at >= ?""",
            (fingerprint, since),
        )
        return int(rows[0]["n"])

    def attempts_today(self, fingerprint: str) -> int:
        since = _iso(utcnow() - timedelta(days=1))
        rows = self._query(
            "SELECT COUNT(*) AS n FROM fixes WHERE fingerprint = ? AND started_at >= ? AND fixed_by != 'person'",
            (fingerprint, since),
        )
        return int(rows[0]["n"])

    def refusals(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._query("SELECT * FROM refusals ORDER BY at")]

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._query(
            """SELECT f.*, c.finished_at, c.outcome, c.summary
               FROM fixes f LEFT JOIN closures c ON c.fix_id = f.id
               ORDER BY f.started_at DESC LIMIT ?""",
            (limit,),
        )
        return [dict(r) for r in rows]

    def get(self, fix_id: str) -> dict[str, Any] | None:
        rows = self._query(
            "SELECT f.*, c.finished_at, c.outcome, c.summary, c.traceback_ref "
            "FROM fixes f LEFT JOIN closures c ON c.fix_id = f.id WHERE f.id = ?",
            (fix_id,),
        )
        if not rows:
            return None
        rec = dict(rows[0])
        rec["steps"] = [dict(r) for r in self._query("SELECT * FROM steps WHERE fix_id = ? ORDER BY idx", (fix_id,))]
        rec["files"] = [dict(r) for r in self._query("SELECT * FROM touched_files WHERE fix_id = ?", (fix_id,))]
        rec["folder_totals"] = [dict(r) for r in self._query("SELECT * FROM folder_totals WHERE fix_id = ?", (fix_id,))]
        rec["file_lists"] = [dict(r) for r in self._query("SELECT * FROM file_lists WHERE fix_id = ?", (fix_id,))]
        rec["settings"] = [dict(r) for r in self._query("SELECT * FROM touched_settings WHERE fix_id = ?", (fix_id,))]
        rec["verifications"] = [dict(r) for r in self._query("SELECT * FROM verifications WHERE fix_id = ? ORDER BY at", (fix_id,))]
        rec["manual"] = [dict(r) for r in self._query("SELECT * FROM manual WHERE fix_id = ?", (fix_id,))]
        rec["undo_data"] = [dict(r) for r in self._query("SELECT * FROM undo_data WHERE fix_id = ?", (fix_id,))]
        rec["undone_by"] = [r["undo_fix_id"] for r in self._query("SELECT * FROM undos WHERE original_fix_id = ?", (fix_id,))]
        return rec

    def touched(self, path_fragment: str) -> list[dict[str, Any]]:
        """"What touched this file or folder?" Searches inline rows and folder totals.
        Sidecar lists are searched when the history page needs them, not here."""
        like = f"%{path_fragment}%"
        rows = self._query(
            """SELECT DISTINCT f.id, f.title, f.started_at FROM fixes f
               LEFT JOIN touched_files t ON t.fix_id = f.id
               LEFT JOIN folder_totals ft ON ft.fix_id = f.id
               WHERE t.path LIKE ? OR ft.folder LIKE ? ORDER BY f.started_at DESC""",
            (like, like),
        )
        return [dict(r) for r in rows]

    @staticmethod
    def read_sidecar(path: str | Path) -> list[dict[str, Any]]:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return [json.loads(line) for line in fh]
