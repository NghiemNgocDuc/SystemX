"""The live demo must show this run's evidence and reopen only its fixed test program."""

from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import sys

import pytest

from systemautobot import demo_support
from systemautobot.models import Signal, StepStatus
from systemautobot.playbooks.frozen_app import FrozenApp


@pytest.fixture
def flow(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "lab"))
    return importlib.import_module("demo_flow")


def state(pid=100, **changes):
    card = {"id": "current", "kind": "frozen_app", "process_id": pid, "used": False,
            "fix": {"title": "Close and reopen Demo Notes"}, "result": None}
    return {"settings": {"demo": True}, "cards": [dict(card, **changes)]}


def record(**changes):
    return dict({"id": "record-1", "card_id": "current", "outcome": "done", "approved_via": "fix_now",
                 "steps": [{"tool": "close_program", "status": "done", "summary": "Closed Demo Notes."},
                           {"tool": "open_program", "status": "done", "summary": "Opened Demo Notes."}],
                 "verifications": [{"result": "fixed", "detail": "No frozen window remains."}]}, **changes)


def test_detection_is_never_inferred_from_elapsed_time_or_another_process(flow):
    session = flow.DemoSession(100, 0)
    view = session.observe(state(pid=200), 60)
    assert session.card_id is None and view["stage"] == 1
    assert "not received a detection" in view["next"]
    assert not view["can_open"]


def test_real_card_enables_ordinary_approval(flow):
    session = flow.DemoSession(100, 0)
    view = session.observe(state(), 17)
    assert view["stage"] == 2 and view["can_open"]
    assert session.card_id == "current" and session.detected_seconds == 17


def test_success_needs_matching_history_steps_and_a_fixed_verdict(flow):
    session = flow.DemoSession(100, 0)
    current = state(used=True, result={"outcome": "done", "fix_id": "record-1"})
    assert session.observe(current, 25)["stage"] == 3
    assert session.observe(current, 26, record(card_id="old-run"))["stage"] == 3
    view = session.observe(current, 27, record())
    assert view["stage"] == 4 and not view["can_open"]
    assert "type" in view["next"].lower()


@pytest.mark.parametrize("changes", [
    {"outcome": "couldnt"}, {"verifications": [{"result": "not_fixed"}]},
    {"verifications": [{"result": "cant_check"}]}, {"steps": []}, {"verifications": []},
    {"steps": [{"tool": "open_program", "status": "couldnt"}]},
])
def test_incomplete_or_failed_repairs_never_claim_recovery(flow, changes):
    session = flow.DemoSession(100, 0)
    view = session.observe(state(used=True, result={"outcome": "done"}), 25, record(**changes))
    assert view["stage"] == 3 and "needs review" in view["title"]


@pytest.mark.parametrize("settings, expected", [
    ({"demo": False}, "--demo --lab"), ({"fixes_paused": True}, "Pause fixes"),
    ({"autopilot": True}, "Autopilot"), ({"off": ["frozen_app"]}, "Frozen programs"),
    ({"never": ["frozen_app:python.exe"]}, "Never for this"),
])
def test_preflight_explains_presentation_blockers(flow, settings, expected):
    assert expected in flow.readiness({"settings": dict({"demo": True}, **settings)})


def test_demo_notes_recognition_requires_exact_interpreter_script_and_arguments(monkeypatch, tmp_path):
    import psutil

    proc = Mock()
    proc.exe.return_value = sys.executable
    monkeypatch.setattr(psutil, "Process", lambda pid: proc)
    for args in ([sys.executable, str(demo_support.NOTES_SCRIPT)],
                 [sys.executable, str(demo_support.NOTES_SCRIPT), "--freeze"]):
        proc.cmdline.return_value = args
        assert demo_support.is_demo_notes(100)
    for args in ([sys.executable, "-c", "evil()"],
                 [sys.executable, str(tmp_path / "demo_notes.py")],
                 [sys.executable, str(demo_support.NOTES_SCRIPT), "--other"],
                 [sys.executable, str(demo_support.NOTES_SCRIPT), "--freeze", "extra"]):
        proc.cmdline.return_value = args
        assert not demo_support.is_demo_notes(100)
    proc.cmdline.return_value = [sys.executable, str(demo_support.NOTES_SCRIPT)]
    proc.exe.return_value = str(tmp_path / "other-python.exe")
    assert not demo_support.is_demo_notes(100)


def test_reopen_uses_fixed_notes_script_without_the_freeze_flag(monkeypatch):
    popen = Mock(return_value=SimpleNamespace(pid=900))
    monkeypatch.setattr(demo_support.subprocess, "Popen", popen)
    result = demo_support.reopen_demo_notes_step().run()
    assert popen.call_args.args[0] == demo_support.notes_command()
    assert "--freeze" not in popen.call_args.args[0]
    assert result.status is StepStatus.DONE


def test_windows_venv_and_windowed_interpreters_are_recognized(monkeypatch, tmp_path):
    import psutil

    launcher = tmp_path / "venv" / "python.exe"
    base = tmp_path / "base" / "python.exe"
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "executable", str(launcher))
    monkeypatch.setattr(sys, "_base_executable", str(base))
    proc = Mock()
    proc.exe.return_value = str(base.with_name("pythonw.exe"))
    proc.cmdline.return_value = [str(base.with_name("pythonw.exe")), str(demo_support.NOTES_SCRIPT), "--freeze"]
    monkeypatch.setattr(psutil, "Process", lambda pid: proc)
    assert demo_support.is_demo_notes(100)
    assert Path(demo_support.notes_command(freeze=True)[0]).parent == base.parent


def test_demo_labels_and_restart_are_only_enabled_in_demo_mode(monkeypatch, settings, fake_checks):
    monkeypatch.setattr(demo_support, "is_demo_notes", lambda pid: True)
    tools = SimpleNamespace(settings=settings, checks=fake_checks, guard=Mock())
    playbook = FrozenApp(tools)
    signal = Signal("check", "hung_window", {"pid": 100, "name": "python.exe", "exe": sys.executable, "foreground": True})
    normal = playbook.match(signal)
    assert normal.title == "python is not responding."
    settings.demo = True
    demo = playbook.match(signal)
    assert demo.title == "Demo Notes is not responding."
    assert demo.fingerprint == "frozen_app:demo_notes:100"
    assert playbook.options(demo)[0].title == "Close and reopen Demo Notes"
    # A second hung Python process must never replace the test process on refresh.
    fake_checks.hung = [{"pid": 200, "name": "python.exe"}]
    assert not playbook.refresh(demo).evidence["hung"]
    # Cleanup can close the process before the watcher sends its recovery signal.
    monkeypatch.setattr(demo_support, "is_demo_notes", lambda pid: False)
    recovered = playbook.match(Signal("check", "hung_window", dict(signal.data, hung=False)))
    assert recovered.fingerprint == demo.fingerprint
    assert not recovered.evidence["hung"]
