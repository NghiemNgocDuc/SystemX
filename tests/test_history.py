from __future__ import annotations

import sqlite3

import pytest

from systemautobot.history import INLINE_FILE_LIMIT, History
from systemautobot.models import (
    ApprovedVia, FixedBy, FixStep, Outcome, Problem, Severity, StepResult, StepStatus,
    TouchedFile, TouchedSetting, Verification,
)
from systemautobot.safety.redact import clear_secrets, register_secret


def _problem(fp="disk_space:C:"):
    return Problem("disk_space", fp, "Your C: drive is almost full.", {"free": 1}, severity=Severity.MAJOR)


def test_full_record_round_trip(history):
    fid = history.open_fix(_problem(), fixed_by=FixedBy.PLAYBOOK, approved_via=ApprovedVia.FIX_NOW)
    step = FixStep("delete_temp_files", {"folders": ["C:\\T"]}, run=lambda: None)
    res = StepResult(StepStatus.DONE, "Deleted 1.0 GB.", files=[TouchedFile("C:\\T\\a.tmp", "deleted", 10)],
                     folder_totals={"C:\\T": (1, 10)},
                     settings=[TouchedSetting("dns", "eth0", "10.0.0.1", "automatic")])
    when = _problem().detected_at
    history.add_step(fid, 0, step, res, when, when)
    history.close_fix(fid, Outcome.DONE, "Deleted 1.0 GB.")
    history.add_verification(fid, Verification.FIXED, "fixed")
    rec = history.get(fid)
    assert rec["outcome"] == "done" and rec["approved_via"] == "fix_now"
    assert rec["steps"][0]["tool"] == "delete_temp_files"
    assert rec["files"][0]["path"] == "C:\\T\\a.tmp"
    assert rec["settings"][0]["old"] == "10.0.0.1"
    assert rec["verifications"][0]["result"] == "fixed"
    assert history.touched("a.tmp")[0]["id"] == fid


@pytest.mark.parametrize("sql", [
    "UPDATE fixes SET title = 'nothing happened'",
    "DELETE FROM fixes",
    "UPDATE closures SET outcome = 'done'",
    "DELETE FROM steps",
])
def test_history_is_append_only(history, sql):
    fid = history.open_fix(_problem(), fixed_by=FixedBy.PLAYBOOK, approved_via=ApprovedVia.FIX_NOW)
    when = _problem().detected_at
    history.add_step(fid, 0, FixStep("x", {}, run=lambda: None), StepResult(StepStatus.DONE, "ok"), when, when)
    history.close_fix(fid, Outcome.COULDNT, "Couldn't.")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        history._db.execute(sql)


def test_bulk_files_go_to_sidecar(history):
    fid = history.open_fix(_problem(), fixed_by=FixedBy.PLAYBOOK, approved_via=ApprovedVia.FIX_NOW)
    files = [TouchedFile(f"C:\\T\\{i}.tmp", "deleted", 1) for i in range(INLINE_FILE_LIMIT + 5)]
    history.add_step(fid, 0, FixStep("delete_temp_files", {}, run=lambda: None),
                     StepResult(StepStatus.DONE, "ok", files=files), _problem().detected_at, _problem().detected_at)
    rec = history.get(fid)
    assert rec["files"] == []
    assert rec["file_lists"][0]["total"] == len(files)
    assert len(History.read_sidecar(rec["file_lists"][0]["sidecar"])) == len(files)


def test_unclosed_fixes_are_found(history):
    fid = history.open_fix(_problem(), fixed_by=FixedBy.PLAYBOOK, approved_via=ApprovedVia.FIX_NOW)
    assert history.unclosed() == [fid]
    history.close_fix(fid, Outcome.INTERRUPTED, "Interrupted.")
    assert history.unclosed() == []


def test_key_is_redacted_everywhere(history):
    key = "xai-SECRETSECRETSECRETSECRET1234"
    register_secret(key)
    try:
        fid = history.open_fix(_problem(), fixed_by=FixedBy.GROK, approved_via=ApprovedVia.FIX_THIS,
                               grok={"reason": f"used key {key}"})
        history.add_step(fid, 0, FixStep("x", {"k": key}, run=lambda: None),
                         StepResult(StepStatus.DONE, f"summary {key}", output=f"out {key}"),
                         _problem().detected_at, _problem().detected_at)
        history.close_fix(fid, Outcome.DONE, f"done {key}")
        dump = "\n".join(history._db.iterdump())
        assert key not in dump
    finally:
        clear_secrets()


def test_unregistered_xai_key_pattern_is_redacted():
    from systemautobot.safety.redact import redact

    assert "xai-" not in redact("token xai-abcdefghijklmnopqrstuvwxyz0123")
    assert "gsk_" not in redact("Authorization: Bearer gsk_" + "A1b2C3d4" * 6)
