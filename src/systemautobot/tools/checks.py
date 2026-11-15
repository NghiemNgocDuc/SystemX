"""Read-only checks. None of these change the PC. Results are small and trimmed,
because the same functions are offered to Grok as tools."""

from __future__ import annotations

import ctypes
import email.utils
import shutil
import socket
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

MAX_ROWS = 20

# The address Windows itself uses to decide whether it is online.
PROBE_HOST = "www.msftconnecttest.com"
PROBE_URL = f"http://{PROBE_HOST}/connecttest.txt"
PROBE_BODY = b"Microsoft Connect Test"
CLOCK_SKEW_LIMIT = 300  # seconds; past this, secure connections start failing


def disk_space(drive: str = "C:\\") -> dict[str, Any]:
    usage = shutil.disk_usage(drive)
    return {
        "drive": drive.rstrip("\\"),
        "free": usage.free,
        "total": usage.total,
        "percent_free": round(100 * usage.free / usage.total, 1) if usage.total else 0.0,
    }


def hung_windows() -> list[dict[str, Any]]:
    """Visible top-level windows Windows marks as Not Responding."""
    if sys.platform != "win32":
        return []
    import psutil
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    foreground = user32.GetForegroundWindow()
    found: list[dict[str, Any]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _each(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or not user32.IsHungAppWindow(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        try:
            proc = psutil.Process(pid.value)
            name, exe = proc.name(), proc.exe()
        except (psutil.Error, OSError):
            name, exe = "unknown", ""
        found.append({
            "hwnd": int(hwnd), "pid": pid.value, "name": name, "exe": exe,
            "title": buf.value[:120], "foreground": hwnd == foreground,
        })
        return True

    user32.EnumWindows(_each, 0)
    return found[:MAX_ROWS]


def network_probe(timeout: float = 5.0) -> dict[str, Any]:
    """Connected? Lookup works? Web works? Is the clock close enough for secure connections?"""
    result: dict[str, Any] = {
        "adapter_up": _adapter_up(), "dns_ok": False, "http_ok": False,
        "clock_skew_seconds": None, "error": "",
    }
    try:
        socket.getaddrinfo(PROBE_HOST, 80)
        result["dns_ok"] = True
    except OSError as e:
        result["error"] = f"lookup failed: {e}"
        return result
    try:
        req = urllib.request.Request(PROBE_URL, headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(64)
            result["http_ok"] = body.startswith(PROBE_BODY)
            date = resp.headers.get("Date")
            if date:
                server = email.utils.parsedate_to_datetime(date)
                result["clock_skew_seconds"] = round((datetime.now(timezone.utc) - server).total_seconds())
    except (urllib.error.URLError, OSError, ValueError) as e:
        result["error"] = f"web check failed: {e}"
    return result


def _adapter_up() -> bool:
    import psutil

    for name, st in psutil.net_if_stats().items():
        if st.isup and "loopback" not in name.lower():
            return True
    return False


def running_apps() -> list[dict[str, Any]]:
    """Programs using the most memory, by name. No command lines, no window contents."""
    import psutil

    rows = []
    for p in psutil.process_iter(["pid", "name", "memory_info", "username"]):
        try:
            if not p.info["username"] or not p.info["memory_info"]:
                continue  # other accounts and protected processes
            rows.append({"pid": p.info["pid"], "name": p.info["name"],
                         "memory_mb": round(p.info["memory_info"].rss / 1024 ** 2)})
        except (psutil.Error, OSError):
            continue
    rows.sort(key=lambda r: r["memory_mb"], reverse=True)
    return rows[:MAX_ROWS]


def recent_errors(hours: int = 24, channels: tuple[str, ...] = ("Application", "System")) -> list[dict[str, Any]]:
    """Recent error events, newest first, trimmed. The message text is untrusted data."""
    if sys.platform != "win32":
        return []
    import win32evtlog

    from ..watcher import _format_message, parse_events_xml

    ms = max(1, min(hours, 72)) * 3600 * 1000
    query = f"*[System[(Level=1 or Level=2) and TimeCreated[timediff(@SystemTime) <= {ms}]]]"
    rows: list[dict[str, Any]] = []
    for channel in channels:
        try:
            h = win32evtlog.EvtQuery(channel, win32evtlog.EvtQueryChannelPath | win32evtlog.EvtQueryReverseDirection, query)
        except Exception:
            continue
        while len(rows) < MAX_ROWS * len(channels):
            try:
                batch = win32evtlog.EvtNext(h, 10)
            except Exception:
                break
            if not batch:
                break
            for ev in batch:
                xml = win32evtlog.EvtRender(ev, win32evtlog.EvtRenderEventXml)
                for s in parse_events_xml(xml, message=_format_message(ev, xml)):
                    rows.append({
                        "time": s.seen_at.isoformat(timespec="seconds"),
                        "source": s.data["provider"], "event_id": s.data["event_id"],
                        "app": s.data["fields"].get("AppName", ""), "message": s.text[:300],
                    })
    rows.sort(key=lambda r: r["time"], reverse=True)
    return rows[:MAX_ROWS]


JOB_PROBLEM_BITS = 0x2 | 0x20 | 0x40 | 0x200 | 0x400  # error, offline, paper out, blocked, needs attention


def print_jobs() -> list[dict[str, Any]]:
    """Jobs waiting in every printer queue, with their age. No document contents."""
    if sys.platform != "win32":
        return []
    import win32print

    rows: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    for p in win32print.EnumPrinters(flags, None, 2):
        name = p["pPrinterName"]
        try:
            h = win32print.OpenPrinter(name)
        except Exception:
            continue
        try:
            jobs = win32print.EnumJobs(h, 0, 50, 1)
        except Exception:
            jobs = []
        finally:
            win32print.ClosePrinter(h)
        for j in jobs:
            sub = j.get("Submitted")
            age = None
            if sub is not None:
                sub = sub.replace(tzinfo=timezone.utc) if sub.tzinfo is None else sub
                age = int((now - sub).total_seconds())
            rows.append({"printer": name, "job_id": j["JobId"], "document": (j.get("pDocument") or "")[:80],
                         "status": j.get("Status", 0), "error": bool(j.get("Status", 0) & JOB_PROBLEM_BITS),
                         "age_seconds": age})
    return rows[:MAX_ROWS]


def _ps_json(script: str, timeout: int = 30) -> Any:
    """Run a read-only PowerShell query that ends in ConvertTo-Json. None if it fails."""
    import json
    import subprocess

    try:
        out = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                             capture_output=True, text=True, timeout=timeout,
                             creationflags=0x08000000 if sys.platform == "win32" else 0)
        return json.loads(out.stdout) if out.returncode == 0 and out.stdout.strip() else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


SECURITY_QUERY = r"""
$r = [ordered]@{}
try {
  $m = Get-MpComputerStatus -ErrorAction Stop
  $r.defender = [ordered]@{ mode = "$($m.AMRunningMode)"; realtime = [bool]$m.RealTimeProtectionEnabled;
    antivirus = [bool]$m.AntivirusEnabled; signature_age_days = [int]$m.AntivirusSignatureAge;
    tamper_protected = [bool]$m.IsTamperProtected }
} catch { $r.defender = $null }
try { $r.firewall = @(Get-NetFirewallProfile -ErrorAction Stop | ForEach-Object { [ordered]@{ name = "$($_.Name)"; enabled = [bool]$_.Enabled } }) } catch { $r.firewall = @() }
try { $r.other_firewalls = @(Get-CimInstance -Namespace root/SecurityCenter2 -ClassName FirewallProduct -ErrorAction Stop | ForEach-Object { "$($_.displayName)" }) } catch { $r.other_firewalls = @() }
$r | ConvertTo-Json -Compress -Depth 4
"""


def security_status() -> dict[str, Any] | None:
    """Windows Security and firewall state. Readable without admin (checked 2026-11-14)."""
    if sys.platform != "win32":
        return None
    return _ps_json(SECURITY_QUERY)


def disk_health() -> list[dict[str, Any]]:
    """Each physical disk's own health verdict (Healthy, Warning, Unhealthy). No admin needed."""
    if sys.platform != "win32":
        return []
    data = _ps_json("@(Get-PhysicalDisk -ErrorAction Stop | Select-Object FriendlyName, MediaType, "
                    "HealthStatus, OperationalStatus) | ConvertTo-Json -Compress")
    rows = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
    return [{"name": str(r.get("FriendlyName") or "Disk")[:60], "media": str(r.get("MediaType") or ""),
             "health": str(r.get("HealthStatus") or ""), "status": str(r.get("OperationalStatus") or "")}
            for r in rows][:MAX_ROWS]


# Dialog children that mean "this is a file or settings dialog, not an error message".
_NOT_ERROR_CHILDREN = {"Edit", "ComboBox", "ComboBoxEx32", "DirectUIHWND", "SysTreeView32", "SHELLDLL_DefView",
                       "SysListView32", "msctls_trackbar32"}
_ERROR_WORDS = None


def error_dialogs() -> list[dict[str, Any]]:
    """Error message boxes on screen: standard dialogs whose text reads like an error.
    The text is untrusted: it goes to the AI wrapped as data and never into a title."""
    global _ERROR_WORDS
    if sys.platform != "win32":
        return []
    import os
    import re

    import psutil
    from ctypes import wintypes

    if _ERROR_WORDS is None:
        _ERROR_WORDS = re.compile(r"error|fail|cannot|can't|could not|couldn't|unable|not found|missing|denied|"
                                  r"exception|corrupt|stopped working|not responding|0x[0-9a-f]{6,}", re.I)
    user32 = ctypes.windll.user32
    me = os.getpid()
    found: list[dict[str, Any]] = []

    def text_of(hwnd) -> str:
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value

    def class_of(hwnd) -> str:
        buf = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, buf, 64)
        return buf.value

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or class_of(hwnd) != "#32770":
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == me:
            return True
        texts: list[str] = []
        kinds: set[str] = set()

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def child(ch, _):
            cls = class_of(ch)
            kinds.add(cls)
            if cls == "Static":
                t = text_of(ch).strip()
                if t:
                    texts.append(t)
            return True

        user32.EnumChildWindows(hwnd, child, 0)
        body = " ".join(texts)[:500]
        if kinds & _NOT_ERROR_CHILDREN or not _ERROR_WORDS.search(body + " " + text_of(hwnd)):
            return True
        try:
            name = psutil.Process(pid.value).name()
        except (psutil.Error, OSError):
            name = ""
        found.append({"hwnd": int(hwnd), "pid": pid.value, "name": name, "title": text_of(hwnd)[:120], "text": body})
        return True

    user32.EnumWindows(each, 0)
    return found[:5]


def is_admin() -> bool:
    if sys.platform != "win32":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


# SHQueryUserNotificationState values
QUNS_NOT_PRESENT = 1
QUNS_BUSY = 2
QUNS_RUNNING_D3D_FULL_SCREEN = 3
QUNS_PRESENTATION_MODE = 4
QUNS_ACCEPTS_NOTIFICATIONS = 5
QUNS_QUIET_TIME = 6
QUNS_APP = 7


def popups_should_wait() -> bool:
    """True during fullscreen apps, games, presentations, or when the screen is locked."""
    if sys.platform != "win32":
        return False
    state = ctypes.c_int()
    try:
        ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state))
    except OSError:
        return False
    return state.value in (QUNS_NOT_PRESENT, QUNS_BUSY, QUNS_RUNNING_D3D_FULL_SCREEN, QUNS_PRESENTATION_MODE)


def format_bytes(n: int) -> str:
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.1f} GB"
    return f"{n / 1024 ** 2:.0f} MB"
