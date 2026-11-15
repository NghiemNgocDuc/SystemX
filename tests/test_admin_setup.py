"""No-prompt admin repairs: off by default, set up once by the person, limited to a short fixed list."""

from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

import test_ai
from sab_helper.helper import ALLOWLIST
from systemautobot import admin_setup
from systemautobot.autopilot import Autopilot
from systemautobot.models import FixOption, Problem, StepStatus
from systemautobot.settings import Settings
from systemautobot.tools.fixes import UNATTENDED, AdminLauncher, admin_step

tools = test_ai.tools
service_world = test_ai.service_world


def test_off_by_default():
    assert Settings().autopilot_admin is False and Settings().autopilot is False


def test_the_short_list_is_quick_repairs_only():
    assert set(UNATTENDED) == {"time_sync", "restart_print_spooler", "defender_signature_update",
                               "firewall_enable", "check_disk"}
    for entry in UNATTENDED:  # nothing with a restore point, a restart, or special logic
        assert set(ALLOWLIST[entry]) <= {"summary", "commands", "settings"}, entry
    for heavy in ("sfc_scan", "dism_restore", "winsock_reset", "reset_windows_update", "dns_automatic"):
        assert heavy not in UNATTENDED


def test_the_elevated_script_holds_only_those_repairs_and_full_paths():
    script = admin_setup.repair_script()
    for entry in UNATTENDED:
        assert f"'{entry}' = @{{" in script
    for other in set(ALLOWLIST) - set(UNATTENDED):
        assert f"'{other}'" not in script, other
    exes = [line.split("Exe = ")[1].split(";")[0] for line in script.splitlines() if "Exe = " in line]
    assert exes and all("$env:SystemRoot" in e or "Program Files" in e for e in exes)  # never a bare program name
    assert "param([Parameter(Mandatory = $true)][string]$Entry)" in script  # the only input: which repair
    assert "Invoke-Expression" not in script and "DownloadString" not in script


def test_setup_installs_for_this_account_only_and_fits_one_command_line():
    s = admin_setup.setup_script("LAPTOP\\sam")
    assert "$user = 'LAPTOP\\sam'" in s and "-RunLevel Highest" in s and "exit 5" in s
    assert "S-1-5-32-545:(OI)(CI)RX" in s  # ordinary users can read the folders, never write them
    assert len(admin_setup.encoded(s)) < admin_setup.MAX_COMMAND
    for bad in ("sam", "LAPTOP\\sam'; calc", "a\\b\\c"):
        with pytest.raises(ValueError):
            admin_setup.setup_script(bad)
    assert "Unregister-ScheduledTask" in admin_setup.remove_script()


@pytest.mark.skipif(sys.platform != "win32", reason="needs Windows PowerShell")
def test_the_generated_scripts_are_valid_powershell(tmp_path):
    for name, text in (("repair", admin_setup.repair_script()), ("setup", admin_setup.setup_script("PC\\me")),
                       ("remove", admin_setup.remove_script())):
        f = tmp_path / f"{name}.ps1"
        f.write_text(text, encoding="utf-8")
        check = ("$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile($env:SAB_F, [ref]$null, [ref]$e); "
                 "if ($e.Count) { $e | ForEach-Object { $_.Message }; exit 1 }")
        import os
        r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", check], capture_output=True, text=True,
                           env={**os.environ, "SAB_F": str(f)}, timeout=60)
        assert r.returncode == 0, f"{name}: {r.stdout}{r.stderr}"


def test_installed_means_exactly_this_versions_script(tmp_path, monkeypatch):
    target = tmp_path / "repair.ps1"
    monkeypatch.setattr(admin_setup, "script_path", lambda: target)
    assert admin_setup.installed() is False
    target.write_bytes(admin_setup.repair_script().replace("\n", "\r\n").encode("utf-8"))
    assert admin_setup.installed() is True  # line endings don't matter
    target.write_text(admin_setup.repair_script() + "Remove-Item C:\\ -Recurse", encoding="utf-8")
    assert admin_setup.installed() is False  # anything else in the file does


# ---- the launcher's no-prompt path -----------------------------------------------------------

def _launcher(tmp_path, write=True):
    launcher = AdminLauncher(results_dir=tmp_path, timeout=3)
    started = []

    def start(entry):
        started.append(entry)
        if write:
            (tmp_path / f"task-{entry}.json").write_text(json.dumps(
                {"status": "done", "summary": "Set the clock from the internet time server.",
                 "steps": [{"command": "w32tm.exe /resync /force", "exit_code": 0, "output": "ok"}],
                 "settings": [{"kind": "service", "name": "w32time", "old": None, "new": "running"}]}), encoding="utf-8")
        return write
    launcher.start_task = start
    return launcher, started


def test_no_prompt_mode_uses_the_installed_task_and_reads_its_result(tmp_path):
    launcher, started = _launcher(tmp_path)
    with launcher.no_prompt():
        r = admin_step("time_sync", launcher).run()
    assert started == ["time_sync"] and r.status is StepStatus.DONE and "clock" in r.summary
    assert r.settings[0].name == "w32time" and "w32tm" in r.command


def test_no_prompt_mode_never_falls_back_to_a_windows_prompt(tmp_path, monkeypatch):
    launcher, started = _launcher(tmp_path, write=False)  # the setup isn't installed
    monkeypatch.setattr(launcher, "helper_command", lambda *a: pytest.fail("it tried the prompted path"))
    with launcher.no_prompt():
        r = launcher.run("time_sync")
        heavy = launcher.run("sfc_scan")
    assert r.status is StepStatus.COULDNT and "aren't set up" in r.summary
    assert heavy.status is StepStatus.COULDNT and started == ["time_sync"]  # not on the list: not even tried


# ---- Autopilot's guard ---------------------------------------------------------------------------

def _pilot(service_world, settings, tmp_path, ready=True):
    svc, pipe, *_ = service_world
    launcher, started = _launcher(tmp_path)
    human = SimpleNamespace(show_popup=lambda c: None, show_grouped=lambda c: None, show_digest=lambda a, b: None,
                            closed=lambda c, r: None)
    return Autopilot(pipe, svc, settings, human, background=False, launcher=launcher,
                     admin_ready=lambda: ready), launcher, started


def test_admin_repairs_need_the_option_the_setup_and_the_short_list(service_world, settings, tmp_path):
    pilot, launcher, _ = _pilot(service_world, settings, tmp_path)
    p = Problem("network", "network:clock", "Your clock is wrong.")
    clock = FixOption("Set the clock", "", lambda p: [admin_step("time_sync", launcher)], needs_admin=True, auto="safe")
    sfc = FixOption("Repair Windows system files", "", lambda p: [admin_step("sfc_scan", launcher)], needs_admin=True,
                    auto="safe")
    settings.autopilot = True
    assert "Windows has to ask" in pilot.may_run(clock, p)          # option off (the default)
    settings.autopilot_admin = True
    assert pilot.may_run(clock, p) is None                           # option on, set up, on the list
    assert "isn't one of the few" in pilot.may_run(sfc, p)          # never the heavy ones
    pilot.admin_ready = lambda: False
    assert "aren't set up" in pilot.may_run(clock, p)                # switched on but not installed (or changed)


def test_autopilot_runs_an_admin_repair_through_the_task_with_no_prompt(service_world, settings, tmp_path, history):
    from systemautobot.models import Card, Severity

    pilot, launcher, started = _pilot(service_world, settings, tmp_path)
    svc, pipe, *_ = service_world
    settings.autopilot = settings.autopilot_admin = True
    seen = []
    real_run_task = launcher.run_task
    launcher.run_task = lambda entry, **kw: seen.append(getattr(launcher._mode, "on", False)) or real_run_task(entry, **kw)
    card = pipe.runner.register(Card(
        problem=Problem("unknown", "unknown:clock", "The clock is wrong.", severity=Severity.MAJOR),
        option=FixOption("Set the clock", "", lambda p: [admin_step("time_sync", launcher)], needs_admin=True, auto="safe"),
        manual_steps=[]))
    pilot.show_popup(card)
    assert started == ["time_sync"] and seen == [True]
    full = history.get(history.recent()[0]["id"])
    assert full["approved_via"] == "auto" and full["steps"][0]["tool"] == "admin:time_sync"
    assert getattr(launcher._mode, "on", False) is False  # the mode ends with that one run


# ---- the switch in Settings ------------------------------------------------------------------------

class FakeAdmin:
    def __init__(self, will_install=True):
        self.is_installed, self.will_install, self.calls = False, will_install, []

    def installed(self): return self.is_installed

    def install(self):
        self.calls.append("install")
        self.is_installed = self.will_install
        return (True, "Set up.") if self.will_install else (False, "You said no at the Windows prompt, so nothing changed.")

    def remove(self):
        self.calls.append("remove")
        self.is_installed = False
        return True, "Removed."

    def status(self, enabled):
        return {"enabled": enabled, "installed": self.is_installed, "account_is_admin": True, "repairs": []}


def _client(service_world, settings, tmp_path, admin):
    from fastapi.testclient import TestClient

    from systemautobot.settings import SettingsStore
    from systemautobot.web.server import AppContext, create_app

    svc, pipe, *_ = service_world
    store = SettingsStore(tmp_path / "settings.json", settings)
    ctx = AppContext(pipeline=pipe, history=pipe.history, service=svc, store=store, origin="http://testserver", admin=admin)
    return TestClient(create_app(ctx)), {"X-SAB-Token": pipe.runner.token, "Origin": "http://testserver"}, store


def test_switching_it_on_runs_the_setup_and_off_removes_it(service_world, settings, tmp_path):
    admin = FakeAdmin()
    client, good, store = _client(service_world, settings, tmp_path, admin)
    r = client.post("/api/autopilot_admin", json={"on": True}, headers=good).json()
    assert r["ok"] and r["enabled"] and r["installed"] and settings.autopilot_admin is True
    assert json.loads(store.path.read_text())["autopilot_admin"] is True
    r = client.post("/api/autopilot_admin", json={"on": False}, headers=good).json()
    assert r["ok"] and not r["enabled"] and not r["installed"] and admin.calls == ["install", "remove"]


def test_saying_no_at_the_windows_prompt_leaves_it_off(service_world, settings, tmp_path):
    client, good, _ = _client(service_world, settings, tmp_path, FakeAdmin(will_install=False))
    r = client.post("/api/autopilot_admin", json={"on": True}, headers=good).json()
    assert not r["ok"] and not r["enabled"] and settings.autopilot_admin is False and "said no" in r["message"]


def test_the_ordinary_settings_call_cannot_switch_it_on(service_world, settings, tmp_path):
    client, good, _ = _client(service_world, settings, tmp_path, FakeAdmin())
    client.post("/api/settings", json={"autopilot_admin": True}, headers=good)
    assert settings.autopilot_admin is False  # only the setup can
