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
