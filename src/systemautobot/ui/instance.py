"""One tray process per user, and never as admin."""

from __future__ import annotations

import ctypes
import subprocess
import sys

_mutex = None


def acquire_single_instance(name: str = "Local\\SystemAutoBot.Tray") -> bool:
    global _mutex
    if sys.platform != "win32":
        return True
    _mutex = ctypes.windll.kernel32.CreateMutexW(None, False, name)
    return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def relaunch_unelevated_or_refuse(is_admin: bool) -> bool:
    """Return True if this process may continue. The main app never runs as admin.
    A packaged app relaunches itself through Explorer, which starts it unelevated."""
    if not is_admin:
        return True
    if getattr(sys, "frozen", False):
        subprocess.Popen(["explorer.exe", sys.executable])
    else:
        message_box("SystemAutoBot doesn't run as administrator. Start it normally; "
                    "Windows will ask for permission only for the repairs that need it.")
    return False


def message_box(text: str, title: str = "SystemAutoBot") -> None:
    if sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)
    else:
        print(text)
