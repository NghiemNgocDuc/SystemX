"""Print queue and disk-failing playbooks, the lab scoreboard, and the lab scripts' syntax."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from systemautobot.digest import Digest
from systemautobot.models import Route, Signal
from systemautobot.pipeline import Pipeline
from systemautobot.playbooks import Toolbox, build_all
from systemautobot.tools.fixes import AdminLauncher, Runner
from systemautobot.triage import Triage
from systemautobot.watcher import parse_events_xml

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "lab"


@pytest.fixture
def pipe(history, settings, fake_checks, sandbox, tmp_path):
    guard, temp, _ = sandbox
    tools = Toolbox(guard=guard, launcher=AdminLauncher(results_dir=tmp_path / "runs"), settings=settings,
                    checks=fake_checks, temp_roots=[temp])
    return Pipeline(build_all(tools), Triage(settings), Digest(settings), Runner(history, paused=False), history)


def test_stuck_print_job_is_minor_with_cancel_first(pipe):
    jobs = [{"printer": "Office", "job_id": 7, "document": "report.pdf", "status": 2, "error": True, "age_seconds": 5}]
    assert pipe.handle(Signal("check", "print_queue", {"jobs": jobs})) is Route.DIGEST
    card = pipe.digest.items["print_queue:any"].card
    assert card.option.title == "Cancel the stuck print job" and not card.option.needs_admin
    assert pipe.handle(Signal("check", "print_queue", {"jobs": []})) is Route.IGNORED
    assert "print_queue:any" not in pipe.digest.items  # it went away


def test_young_healthy_print_job_is_fine(pipe):
    jobs = [{"printer": "Office", "job_id": 8, "document": "x", "status": 16, "error": False, "age_seconds": 20}]
    assert pipe.handle(Signal("check", "print_queue", {"jobs": jobs})) is Route.IGNORED


def test_disk_failing_replay_is_major_with_no_fix(pipe):
    xml = (LAB / "replay" / "S9_disk_failing.xml").read_text(encoding="utf-8")
    routes = [pipe.handle(s) for s in parse_events_xml(xml)]
    assert routes[0] is Route.POPUP
    card = pipe.open_cards["disk_failing:disk"]
    assert card.option is None and "Back up" in card.manual_steps[0].text


def test_failing_disk_suppresses_write_heavy_fixes(pipe, fake_checks):
    from conftest import GB

    xml = (LAB / "replay" / "S9_disk_failing.xml").read_text(encoding="utf-8")
    for s in parse_events_xml(xml):
        pipe.handle(s)
    fake_checks.set_free(int(1 * GB))
    pipe.handle(Signal("check", "disk_space", fake_checks.disk_space()))
    card = pipe.open_cards["disk_space:C:"]
    assert card.option is None or "Delete" not in card.option.title


def _load(name: str):
    import sys

    spec = importlib.util.spec_from_file_location(f"lab_{name}", LAB / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses look the module up while the class is built
    spec.loader.exec_module(mod)
    return mod


def test_scoreboard_reports_real_numbers(tmp_path):
    sb = _load("scoreboard")
    rows = [
        {"scenario": "S1", "title": "Drive", "detected": True, "detect_seconds": 12, "severity_ok": True,
         "has_fix": True, "fix_offer_ok": True, "verification": "fixed", "fix_seconds": 4, "history_missing": []},
        {"scenario": "S1", "title": "Drive", "detected": False},
        {"scenario": "S7", "title": "Slow", "skipped": "not built"},
    ]
    (tmp_path / "run-1.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    sb.main.__globals__["sys"].argv = ["scoreboard", str(tmp_path)]
    sb.main()
    out = (tmp_path / "scoreboard.html").read_text(encoding="utf-8")
    assert "50% (1/2)" in out and "S7" in out and "needs work" in out


def test_every_built_scenario_has_its_breaker_or_replay():
    rs = _load("run_scenarios")
    for s in rs.SCENARIOS.values():
        if s.breaker:
            assert (LAB / "break" / s.breaker).is_file(), s.id
        if s.replay:
            assert (LAB / "replay" / s.replay).is_file(), s.id


@pytest.mark.skipif(not shutil.which("powershell.exe"), reason="needs Windows PowerShell")
def test_powershell_scripts_parse():
    files = sorted(LAB.rglob("*.ps1")) + sorted((ROOT / "packaging").glob("*.ps1"))
    import os

    script = ("$bad = 0; foreach ($f in ($env:SAB_PS_FILES -split '\\|')) { $e = $null; "
              "[void][System.Management.Automation.Language.Parser]::ParseFile($f, [ref]$null, [ref]$e); "
              "if ($e.Count) { $bad++; Write-Output \"$f : $($e[0].Message)\" } }; exit $bad")
    env = dict(os.environ, SAB_PS_FILES="|".join(map(str, files)))
    r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script],
                       capture_output=True, text=True, timeout=120, env=env)
    assert r.returncode == 0, r.stdout + r.stderr


def test_breakers_refuse_outside_the_lab_vm():
    common = (LAB / "break" / "_common.ps1").read_text(encoding="utf-8")
    assert "THIS_IS_A_LAB_VM" in common
    for f in (LAB / "break").glob("S*.ps1"):
        assert "Assert-LabVM" in f.read_text(encoding="utf-8"), f.name
