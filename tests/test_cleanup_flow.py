"""The cleanup presentation requires both real measurements and a matching repair."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest


@pytest.fixture
def flow(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "lab"))
    return importlib.import_module("demo_flow")


def state(**changes):
    card = {"id": "cleanup-card", "kind": "demo_cleanup", "used": False, "result": None}
    return {"settings": {"demo": True}, "cards": [dict(card, **changes)]}


def measured(**changes):
    return dict({"run_id": "run", "card_id": "cleanup-card", "before_bytes": 25165824,
                 "remaining_bytes": 0, "removed_bytes": 25165824, "files_remaining": 0,
                 "folder": "demo folder"}, **changes)


def record(**changes):
    return dict({"id": "history", "card_id": "cleanup-card", "outcome": "done", "approved_via": "fix_now",
                 "steps": [{"tool": "delete_demo_files", "status": "done", "summary": "Removed demo files."}],
                 "files": [{"action": "deleted", "size": 25165824}],
                 "verifications": [{"result": "fixed"}]}, **changes)


def test_measurement_alone_never_claims_a_successful_repair(flow):
    session = flow.CleanupSession("run", "cleanup-card")
    view = session.observe(state(), measured())
    assert view["before"] == "24.0 MiB" and view["remaining"] == "0.0 MiB"
    assert not view["complete"] and view["recovered"] == "Not verified"
    assert view["can_open"]


def test_complete_requires_measured_removal_and_matching_verified_history(flow):
    session = flow.CleanupSession("run", "cleanup-card")
    view = session.observe(state(used=True, result={"outcome": "done"}), measured(), record())
    assert view["complete"] and view["stage"] == 4
    assert view["recovered"] == "24.0 MiB" and not view["can_open"]


@pytest.mark.parametrize("changes", [
    {"card_id": "different-card"}, {"outcome": "couldnt"}, {"verifications": []},
    {"verifications": [{"result": "not_fixed"}]}, {"verifications": [{"result": "cant_check"}]},
    {"steps": []}, {"steps": [{"tool": "delete_demo_files", "status": "couldnt"}]},
    {"steps": [{"tool": "delete_files", "status": "done"}]},
    {"files": []}, {"files": [{"action": "deleted", "size": 100}]},
    {"files": [{"action": "created", "size": 25165824}]},
])
def test_unverified_or_other_repairs_never_get_a_recovered_total(flow, changes):
    view = flow.CleanupSession("run", "cleanup-card").observe(
        state(used=True), measured(), record(**changes))
    assert not view["complete"] and view["recovered"] == "Not verified"


@pytest.mark.parametrize("changes", [
    {"run_id": "old-run"}, {"card_id": "old-card"}, {"remaining_bytes": 100},
    {"removed_bytes": 0}, {"before_bytes": 0},
])
def test_missing_or_conflicting_measurement_blocks_success(flow, changes):
    view = flow.CleanupSession("run", "cleanup-card").observe(state(used=True), measured(**changes), record())
    assert not view["complete"] and view["recovered"] == "Not verified"


def test_missing_connection_cannot_preserve_a_success_claim(flow):
    view = flow.CleanupSession("run", "cleanup-card").observe({}, None, record())
    assert not view["complete"] and not view["can_open"]
    assert view["remaining"] == "—"


def test_cleanup_is_independent_of_frozen_app_monitoring(flow):
    config = {"settings": {"demo": True, "off": ["frozen_app"], "never": ["frozen_app:demo"]}}
    assert flow.readiness(config, frozen=False) is None
    config["settings"]["autopilot"] = True
    assert "Autopilot" in flow.readiness(config, frozen=False)


@pytest.mark.parametrize("outcome", ["couldnt", "refused", "interrupted"])
def test_failed_cleanup_can_request_fresh_review_without_claiming_success(flow, outcome):
    view = flow.CleanupSession("run", "cleanup-card").observe(
        state(used=True, result={"outcome": outcome}), measured(remaining_bytes=25165824, removed_bytes=0),
        record(outcome=outcome, files=[], verifications=[]))
    assert view["can_retry"] and not view["complete"]
    assert "adds no files" in view["next"]


def test_pending_repair_cannot_enable_retry_even_with_old_refusal(flow):
    view = flow.CleanupSession("run", "cleanup-card").observe(
        state(used=True, result={"outcome": "refused"}), measured(),
        record(outcome=None, files=[], verifications=[]))
    assert not view["can_retry"] and not view["can_open"]


def test_running_cleanup_without_result_cannot_enable_retry(flow):
    view = flow.CleanupSession("run", "cleanup-card").observe(state(used=True), measured())
    assert not view["can_retry"] and not view["can_open"]
