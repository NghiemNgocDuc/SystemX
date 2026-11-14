"""The admin helper must refuse anything that isn't exactly <allowlisted entry> <run id>."""

from __future__ import annotations

import pytest

from sab_helper import helper

RUN = "0123456789abcdef0123456789abcdef"


@pytest.mark.parametrize("argv", [
    [],
    ["sfc_scan"],
    ["sfc_scan", RUN, "extra"],
    ["format_c", RUN],
    ["powershell -c whoami", RUN],
    ["sfc_scan", "../../evil"],
    ["sfc_scan", RUN.upper()],
    ["sfc_scan", RUN + "0"],
])
def test_rejects_bad_arguments(argv):
    assert helper.validate(argv) is None
    assert helper.main(argv) == helper.EXIT_BAD_ARGS


def test_accepts_allowlisted_entry():
    assert helper.validate(["sfc_scan", RUN]) == ("sfc_scan", RUN)


def test_commands_come_from_the_allowlist_only():
    calls = []

    def fake(argv, timeout):
        calls.append(argv)
        return 0, "ok"

    result = helper.execute("time_sync", RUN, run=fake)
    assert result["status"] == "done"
    assert calls == [argv for argv, _, _ in helper.ALLOWLIST["time_sync"]["commands"]]


def test_failure_stops_and_is_reported():
    def fake(argv, timeout):
        return (1, "nope") if argv[0] == "net.exe" else (0, "")

    result = helper.execute("time_sync", RUN, run=fake)
    assert result["status"] == "couldnt"
    assert len(result["steps"]) == 2  # sc config ran, net start failed, w32tm never ran


def test_restore_point_states_are_reported():
    def fake(argv, timeout):
        if "Checkpoint-Computer" in argv[-1]:
            return 1, "System Protection is off"
        return 0, ""  # no restore points listed

    result = helper.execute("sfc_scan", RUN, run=fake)
    assert result["restore_point"]["state"] == "unavailable"


def test_not_admin_exits_before_running(monkeypatch):
    monkeypatch.setattr(helper, "is_admin", lambda: False)
    monkeypatch.setattr(helper, "execute", lambda *a, **k: pytest.fail("ran without admin"))
    assert helper.main(["sfc_scan", RUN]) == helper.EXIT_NOT_ADMIN


def test_launcher_reads_helper_result(tmp_path):
    from systemautobot.models import StepStatus
    from systemautobot.tools.fixes import AdminLauncher

    result = helper.execute("winsock_reset", RUN, run=lambda a, t: (0, "ok"))
    helper.write_result(result, tmp_path)
    step = AdminLauncher(results_dir=tmp_path).read_result(RUN, 0)
    assert step.status is StepStatus.DONE and step.needs_restart
    assert "netsh.exe winsock reset" in step.command
