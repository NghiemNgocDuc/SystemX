from pathlib import Path

import pytest

from systemautobot.demo_cleanup import DemoCleanup, FILE_BYTES, FILE_COUNT
from systemautobot.digest import Digest
from systemautobot.models import ApprovedVia, Outcome, Signal, Verification
from systemautobot.pipeline import Pipeline
from systemautobot.playbooks import Toolbox
from systemautobot.safety.guard import GuardRefusal
from systemautobot.tools.fixes import Runner
from systemautobot.triage import Triage


@pytest.fixture
def cleanup(settings, sandbox, tmp_path, history):
    settings.demo = True
    guard, _, _ = sandbox
    demo = DemoCleanup(Toolbox(guard, None, settings), tmp_path / "demo-cleanup")
    pipe = Pipeline([demo], Triage(settings), Digest(settings), Runner(history, paused=False), history)
    return demo, pipe


def offer(demo, pipe):
    run = demo.prepare()
    problem = demo.match(Signal("demo", demo.id, {"run_id": run.id}))
    pipe.triage.assess(problem)
    card = pipe.propose(problem)
    run.card_id = card.id
    return run, card


def test_real_cleanup_needs_approval_then_measures_and_records_exact_files(cleanup, history, tmp_path):
    demo, pipe = cleanup
    outside = tmp_path / "personal.txt"
    outside.write_text("keep me")
    run, card = offer(demo, pipe)
    before = demo.snapshot(run.id)
    assert before["before_bytes"] == before["remaining_bytes"] == FILE_BYTES * FILE_COUNT
    assert demo.prepare() is run  # repeated start does not fill the disk
    assert pipe.approve(card.id, "wrong", ApprovedVia.FIX_NOW).outcome is Outcome.REFUSED
    assert demo.snapshot(run.id)["remaining_bytes"] == before["before_bytes"]
    result = pipe.approve(card.id, pipe.runner.token, ApprovedVia.FIX_NOW)
    assert result.outcome is Outcome.DONE and result.verification is Verification.FIXED
    after = demo.snapshot(run.id)
    assert after["remaining_bytes"] == 0 and after["removed_bytes"] == before["before_bytes"]
    record = history.get(result.fix_id)
    assert record["approved_via"] == "fix_now" and record["card_id"] == card.id
    assert record["steps"][0]["tool"] == "delete_demo_files"
    assert sum(f["size"] for f in record["files"]) == before["before_bytes"]
    assert outside.read_text() == "keep me"
    assert demo.prepare().id != run.id


@pytest.mark.parametrize("change", ["added", "modified", "nested", "linked"])
def test_changed_folder_refuses_without_deleting_files(cleanup, change, tmp_path):
    demo, pipe = cleanup
    run, card = offer(demo, pipe)
    first = next(iter(run.files))
    if change == "added":
        (run.folder / "personal.txt").write_text("keep me")
    elif change == "modified":
        first.write_text("new personal content")
    elif change == "nested":
        (run.folder / "nested").mkdir()
    else:
        other = tmp_path / "other.txt"
        other.write_text("keep me")
        first.unlink()
        try:
            first.hardlink_to(other)
        except OSError:
            pytest.skip("hard links unavailable")
    result = pipe.approve(card.id, pipe.runner.token, ApprovedVia.FIX_NOW)
    assert result.outcome is Outcome.COULDNT
    assert all(path.exists() for path in run.files)


def test_removed_file_cannot_be_credited_to_demo_cleanup(cleanup):
    demo, pipe = cleanup
    run, card = offer(demo, pipe)
    first = next(iter(run.files))
    first.unlink()
    result = pipe.approve(card.id, pipe.runner.token, ApprovedVia.FIX_NOW)
    assert result.outcome is Outcome.COULDNT
    assert demo.snapshot(run.id)["files_remaining"] == FILE_COUNT - 1


def test_file_added_during_cleanup_is_never_in_delete_manifest(cleanup, monkeypatch):
    from systemautobot.safety.guard import Guard

    demo, pipe = cleanup
    run, card = offer(demo, pipe)
    original = Guard.delete_known_files
    added = run.folder / "personal.txt"

    def intercept(guard, files):
        added.write_text("keep me")
        return original(guard, files)

    monkeypatch.setattr(Guard, "delete_known_files", intercept)
    result = pipe.approve(card.id, pipe.runner.token, ApprovedVia.FIX_NOW)
    assert result.outcome is Outcome.DONE
    assert added.read_text() == "keep me"


def test_demo_is_unavailable_in_normal_mode(cleanup):
    demo, _ = cleanup
    demo.tools.settings.demo = False
    with pytest.raises(GuardRefusal):
        demo.prepare()
    assert demo.match(Signal("demo", demo.id, {"run_id": "anything"})) is None


def test_unknown_run_and_path_are_not_accepted(cleanup):
    demo, _ = cleanup
    assert demo.match(Signal("demo", demo.id, {"run_id": "../Documents"})) is None
    with pytest.raises(GuardRefusal):
        demo._safe_folder(demo.root.parent)
