"""sab-helper: runs ONE allowlisted admin repair, writes a result file, and exits.

Usage: sab-helper <entry_id> <run_id>

- Exactly two arguments. Anything else exits with code 2 before touching the PC.
- entry_id must be on ALLOWLIST below. Every command is written here, never received.
- run_id is 32 lowercase hex characters (it names the result file).
- Results go to %ProgramData%\\SystemAutoBot\\runs\\<run_id>.json, which only admins can write,
  so the tray can trust what it reads even if a different admin account approved the prompt.

Self-contained on purpose: no imports from the main app, so it runs with `python -I`.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import sys
import winreg
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

RUN_ID = re.compile(r"^[0-9a-f]{32}$")
GUID = re.compile(r"^\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}$")
CREATE_NO_WINDOW = 0x08000000
EXIT_BAD_ARGS, EXIT_NOT_ADMIN, EXIT_FAILED = 2, 3, 4

Exec = Callable[[list[str], int], tuple[int, str]]


def real_exec(argv: list[str], timeout: int) -> tuple[int, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                           creationflags=CREATE_NO_WINDOW, errors="replace")
        return p.returncode, (p.stdout + p.stderr)[-2000:]
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, str(e)


def ps(script: str) -> list[str]:
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script]


# ---- the allowlist ------------------------------------------------------------------
# commands: list of (argv, ok_exit_codes, timeout_seconds)

ALLOWLIST: dict[str, dict] = {
    "sfc_scan": {
        "summary": "Checked and repaired Windows system files.",
        "restore_point": True,
        "commands": [(["sfc.exe", "/scannow"], {0}, 3600)],
    },
    "dism_restore": {
        "summary": "Repaired the Windows component store.",
        "restore_point": True,
        "commands": [(["DISM.exe", "/Online", "/Cleanup-Image", "/RestoreHealth"], {0}, 3600)],
    },
    "winsock_reset": {
        "summary": "Reset the network stack. It takes effect after a restart.",
        "restore_point": True,
        "needs_restart": True,
        "commands": [(["netsh.exe", "winsock", "reset"], {0}, 120)],
    },
    "time_sync": {
        "summary": "Set the clock from the internet time server.",
        "settings": [("service", "w32time", None, "running")],
        "commands": [
            (["sc.exe", "config", "w32time", "start=", "auto"], {0}, 60),
            (["net.exe", "start", "w32time"], {0, 2}, 60),  # 2: already running
            (["w32tm.exe", "/resync", "/force"], {0}, 120),
        ],
    },
    "restart_print_spooler": {
        "summary": "Restarted the print service.",
        "settings": [("service", "Spooler", "stuck", "restarted")],
        "commands": [
            (["net.exe", "stop", "spooler", "/y"], {0, 2}, 120),
            (["net.exe", "start", "spooler"], {0}, 120),
        ],
    },
    "reset_windows_update": {
        "summary": "Reset the Windows Update components.",
        "restore_point": True,
        "special": "reset_windows_update",
    },
    "dns_automatic": {
        "summary": "Switched the DNS servers back to automatic.",
        "special": "dns_automatic",
    },
    # Not yet built (need a validated argument): dns_restore (undo), startup_disable_all_users.
}


# ---- special repairs ------------------------------------------------------------------

def _manual_dns_interfaces() -> list[dict]:
    """Interfaces whose IPv4 DNS servers were typed in by hand."""
    found = []
    base = r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces"
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as root:
        for i in range(winreg.QueryInfoKey(root)[0]):
            guid = winreg.EnumKey(root, i)
            if not GUID.match(guid):
                continue
            with winreg.OpenKey(root, guid) as k:
                try:
                    servers, _ = winreg.QueryValueEx(k, "NameServer")
                except OSError:
                    continue
            if servers.strip():
                found.append({"guid": guid, "servers": servers})
    return found


def special_dns_automatic(run: Exec, result: dict) -> bool:
    interfaces = _manual_dns_interfaces()
    if not interfaces:
        result["summary"] = "No DNS servers were set by hand, so there was nothing to switch."
        return True
    ok = True
    for itf in interfaces:
        # guid was read from the registry and matched against GUID above.
        argv = ps(f"Get-NetAdapter | Where-Object InterfaceGuid -eq '{itf['guid']}' | "
                  f"Set-DnsClientServerAddress -ResetServerAddresses")
        code, out = run(argv, 60)
        result["steps"].append({"command": " ".join(argv), "exit_code": code, "output": out[-500:]})
        if code == 0:
            result["settings"].append({"kind": "dns", "name": itf["guid"], "old": itf["servers"], "new": "automatic"})
        ok = ok and code == 0
    result["undo"] = {"entry": "dns_restore", "interfaces": interfaces}
    run(["ipconfig.exe", "/flushdns"], 30)
    return ok


def special_reset_windows_update(run: Exec, result: dict) -> bool:
    services = ["wuauserv", "bits", "cryptsvc"]
    for svc in services:
        code, out = run(["net.exe", "stop", svc, "/y"], 120)
        result["steps"].append({"command": f"net stop {svc}", "exit_code": code, "output": out[-300:]})
    windir = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    stamp = result["run_id"][:8]
    ok = True
    for folder in (windir / "SoftwareDistribution", windir / "System32" / "catroot2"):
        target = folder.with_name(f"{folder.name}.sab-{stamp}")
        try:
            if folder.exists():
                folder.rename(target)
                result["settings"].append({"kind": "folder", "name": str(folder), "old": str(folder), "new": str(target)})
        except OSError as e:
            ok = False
            result["steps"].append({"command": f"rename {folder}", "exit_code": -1, "output": str(e)})
    for svc in reversed(services):
        code, out = run(["net.exe", "start", svc], 120)
        result["steps"].append({"command": f"net start {svc}", "exit_code": code, "output": out[-300:]})
        ok = ok and code in (0, 2)
    return ok


SPECIAL = {"dns_automatic": special_dns_automatic, "reset_windows_update": special_reset_windows_update}


# ---- restore point -------------------------------------------------------------------

def make_restore_point(run: Exec, started: datetime) -> dict:
    code, out = run(ps('Checkpoint-Computer -Description "SystemAutoBot" -RestorePointType MODIFY_SETTINGS'), 600)
    q_code, q_out = run(ps("(Get-ComputerRestorePoint | Sort-Object SequenceNumber -Descending | "
                           "Select-Object -First 1).CreationTime"), 120)
    latest = None
    m = re.search(r"(\d{14})", q_out or "")  # WMI time: yyyymmddHHMMSS
    if m:
        latest = datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
    if latest is None:
        return {"state": "unavailable", "detail": "System Protection is off, so no restore point is possible."}
    if latest >= started.replace(tzinfo=None) - timedelta(minutes=1):
        return {"state": "created", "time": latest.isoformat()}
    if datetime.now() - latest < timedelta(hours=24):
        return {"state": "reused", "time": latest.isoformat(),
                "detail": f"Used the restore point from {latest:%I:%M %p} today."}
    return {"state": "unavailable", "detail": (out or "")[-300:]}


# ---- main ----------------------------------------------------------------------------

def results_dir() -> Path:
    return Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "SystemAutoBot" / "runs"


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def validate(argv: list[str]) -> tuple[str, str] | None:
    if len(argv) != 2:
        return None
    entry, run_id = argv
    if entry not in ALLOWLIST or not RUN_ID.match(run_id):
        return None
    return entry, run_id


def execute(entry: str, run_id: str, run: Exec = real_exec) -> dict:
    spec = ALLOWLIST[entry]
    started = datetime.now(timezone.utc).astimezone()
    result = {"entry": entry, "run_id": run_id, "status": "couldnt", "summary": spec["summary"],
              "steps": [], "settings": [], "needs_restart": bool(spec.get("needs_restart")), "undo": None}
    if spec.get("restore_point"):
        rp = make_restore_point(run, started)
        result["restore_point"] = rp
        result["settings"].append({"kind": "restore_point", "name": rp["state"], "old": None, "new": rp.get("time")})
    if "special" in spec:
        ok = SPECIAL[spec["special"]](run, result)
    else:
        ok = True
        for argv, ok_codes, timeout in spec["commands"]:
            code, out = run(argv, timeout)
            result["steps"].append({"command": " ".join(argv), "exit_code": code, "output": out[-500:]})
            if code not in ok_codes:
                ok = False
                break
    for kind, name, old, new in spec.get("settings", []):
        if ok:
            result["settings"].append({"kind": kind, "name": name, "old": old, "new": new})
    result["status"] = "done" if ok else "couldnt"
    if not ok:
        result["summary"] = "Couldn't finish: " + spec["summary"][0].lower() + spec["summary"][1:]
    return result


def write_result(result: dict, out_dir: Path | None = None) -> Path:
    out_dir = out_dir or results_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{result['run_id']}.json"
    path.write_text(json.dumps(result, indent=1), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parsed = validate(sys.argv[1:] if argv is None else argv)
    if parsed is None:
        return EXIT_BAD_ARGS
    if not is_admin():
        return EXIT_NOT_ADMIN
    entry, run_id = parsed
    result = execute(entry, run_id)
    write_result(result)
    return 0 if result["status"] == "done" else EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
