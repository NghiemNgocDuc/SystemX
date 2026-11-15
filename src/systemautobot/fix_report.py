"""What a fix just did, in plain words, for the card right after the person clicks Fix.

Built from the fix-history record, so the card shows exactly what Activity will show later:
the steps that ran, what changed (files, programs, print jobs, Windows settings), and the re-check.
"""

from __future__ import annotations

from pathlib import PureWindowsPath
from typing import Any

SHOW_FILES = 5  # example file names on the card; the full list is in Activity

VERDICT = {
    "fixed": "I checked again: it's fixed.",
    "not_fixed": "I checked again: it's still happening.",
    "waiting_restart": "It finishes after you restart the PC.",
    "cant_check": "There's no automatic check for this one. Tell me if it comes back.",
}


def size(n: int | None) -> str:
    n = int(n or 0)
    for unit, scale in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= scale:
            return f"{n / scale:.1f} {unit}"
    return f"{n} bytes"


def _plural(n: int, word: str) -> str:
    return f"{n:,} {word}{'' if n == 1 else 's'}"


def _setting_line(s: dict[str, Any]) -> str:
    kind, name, old, new = s.get("kind") or "", s.get("name") or "", s.get("old"), s.get("new")
    if kind == "process":
        return f"{'Closed' if new == 'closed' else 'Started'} {name}"
    if kind == "print_job":
        return f"Cancelled the stuck print job {name}"
    label = f"{kind.replace('_', ' ')} {name}".strip()
    return f"Changed {label}: {old if old not in (None, '') else 'none'} → {new if new not in (None, '') else 'none'}"


def build(rec: dict[str, Any] | None) -> dict[str, Any] | None:
    if not rec:
        return None
    steps = [{"summary": s.get("summary") or s.get("tool") or "Step", "status": s.get("status") or "",
              "admin": bool(s.get("needs_admin"))} for s in rec.get("steps") or []]

    changed: list[str] = []
    freed = 0
    for f in rec.get("folder_totals") or []:
        if not int(f.get("count") or 0):
            continue  # a folder that had nothing old enough to delete
        freed += int(f.get("bytes") or 0)
        changed.append(f"Deleted {_plural(int(f.get('count') or 0), 'file')} from {f.get('folder')} "
                       f"({size(f.get('bytes'))})")
    files = [f for f in rec.get("files") or [] if f.get("path")]
    examples = [PureWindowsPath(f["path"]).name for f in files[:SHOW_FILES]]
    total_files = sum(int(f.get("total") or 0) for f in rec.get("file_lists") or []) or len(files)
    if files and not rec.get("folder_totals"):
        changed.append(f"Deleted {_plural(len(files), 'file')}")
        freed += sum(int(f.get("size") or 0) for f in files)
    changed += [_setting_line(s) for s in rec.get("settings") or []]

    checks = rec.get("verifications") or []
    verdict = checks[-1].get("result") if checks else None
    return {
        "fix_id": rec.get("id"),
        "title": rec.get("title") or "",
        "option": rec.get("option_title") or "",
        "summary": rec.get("summary") or "",
        "outcome": rec.get("outcome") or "",
        "steps": steps,
        "changed": changed,
        "files": {"examples": examples, "more": max(0, total_files - len(examples))},
        "freed": size(freed) if freed else "",
        "verification": verdict,
        "verdict": VERDICT.get(verdict or "", ""),
        "restart": verdict == "waiting_restart",
    }
