"""Laptop demo panel: show SystemAutoBot to judges on your own Windows laptop, safely.

Every button here is harmless to the laptop:
  - it opens a small test program that freezes itself, or
  - it feeds RECORDED Windows events into the app (nothing is broken for real), or
  - it runs the test suite.
No admin rights, no network, clock, printer, or disk changes. The VM-only breakers in lab\\break stay locked.

    1. Start the app:   .venv\\Scripts\\python.exe -m systemautobot app --demo --lab
    2. Start the panel: .venv\\Scripts\\python.exe lab\\laptop_demo.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tkinter as tk
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "lab"
EVENTS = {
    "disk": LAB / "replay" / "S9_disk_failing.xml",
    "crash": ROOT / "tests" / "fixtures" / "events" / "app_error_1000.xml",
    "inject": ROOT / "tests" / "fixtures" / "events" / "prompt_injection.xml",
}
CREATE_NEW_CONSOLE = 0x00000010


def lab_call(path: str, body: dict | None = None):
    token_file = Path(os.environ["LOCALAPPDATA"]) / "SystemAutoBot" / "lab_token.txt"
    if not token_file.exists():
        raise RuntimeError("Start the app with --demo --lab first.")
    token, base = token_file.read_text(encoding="utf-8").split()[:2]
    req = urllib.request.Request(base + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-SAB-Lab-Token": token, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or b"null")


def replay(kind: str) -> str:
    routes = lab_call("/api/lab/replay", {"xml": EVENTS[kind].read_text(encoding="utf-8")})["routes"]
    if "popup" in routes:
        return "A popup should appear now (bottom right)."
    if "digest" in routes:
        return "Saved for the end-of-day list. Press 'Show end-of-day list'."
    if "duplicate" in routes:
        return "It's already waiting in the app."
    return f"The app routed it as: {', '.join(routes)}"


def freeze() -> str:
    subprocess.Popen([sys.executable, str(LAB / "apps" / "hangme.py"), "--freeze"])
    return "hangme opens, then freezes after 3 s. The popup comes about 30 to 40 s later."


def error_box() -> str:
    """A real Windows error message box from a harmless PowerShell window. Close it any time."""
    msg = ("Cannot open the project file. Error 0x80070002: The system cannot find the file specified.")
    script = ("Add-Type -AssemblyName System.Windows.Forms; "
              f"[void][System.Windows.Forms.MessageBox]::Show('{msg}', 'Demo Editor', 0, 16)")
    subprocess.Popen(["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script])
    return "An error box appears. Within about 6 s the app shows it on Home. Press Fix for me to watch the AI work."


def digest() -> str:
    d = lab_call("/api/lab/digest_now", {})
    return f"End-of-day list opened with {len(d['items'])} item(s)."


def redteam() -> str:
    subprocess.Popen([sys.executable, "-m", "pytest", str(ROOT / "tests" / "test_redteam.py"), "-v"],
                     cwd=ROOT, creationflags=CREATE_NEW_CONSOLE)
    return "Red-team suite running in a new window: every attack should be refused."


def tidy() -> str:
    out = subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                          "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
                          "Where-Object CommandLine -like '*hangme*' | "
                          "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"], capture_output=True)
    return "Closed any hangme windows." if out.returncode == 0 else "Nothing to close."


BUTTONS = [
    ("1  Freeze a program", "Major popup with an unsaved-work warning", freeze),
    ("2  Disk is failing (recorded events)", "Major popup, deliberately no fix button", lambda: replay("disk")),
    ("E  Show an error message box", "A real error box: the app notices it and offers to explain", error_box),
    ("3  An app crashed (recorded event)", "Small problem, waits for the end of day", lambda: replay("crash")),
    ("4  Malicious error message", "Text that tries to command the AI", lambda: replay("inject")),
    ("5  Show end-of-day list now", "Fix it for me / I'll do it myself / Not now", digest),
    ("6  Run the red-team suite", "19 attacks, all refused", redteam),
    ("Tidy up", "Close the frozen test program", tidy),
]

root = tk.Tk()
root.title("SystemAutoBot laptop demo")
root.geometry("460x660")
root.configure(bg="#ffd43b")
tk.Label(root, text="Laptop demo (safe)", font=("Segoe UI", 16, "bold"), bg="#ffd43b").pack(pady=(14, 2))
tk.Label(root, text="Nothing here changes your PC. Recorded events stand in\nfor problems that can't be caused safely.",
         font=("Segoe UI", 9), bg="#ffd43b").pack(pady=(0, 10))
status = tk.StringVar(value="Ready. Start the app with --demo --lab first.")


def run(fn):
    try:
        status.set(fn())
    except Exception as e:  # show the reason instead of a traceback
        status.set(f"Couldn't: {e}")


for label, hint, fn in BUTTONS:
    f = tk.Frame(root, bg="#ffd43b")
    f.pack(fill="x", padx=14, pady=4)
    tk.Button(f, text=label, anchor="w", font=("Segoe UI", 11, "bold"), bg="#ffffff", relief="solid", bd=2,
              command=lambda fn=fn: run(fn)).pack(fill="x")
    tk.Label(f, text=hint, font=("Segoe UI", 9), bg="#ffd43b", anchor="w").pack(fill="x")
tk.Label(root, textvariable=status, wraplength=420, justify="left", font=("Segoe UI", 10),
         bg="#ffffff", relief="solid", bd=2, padx=8, pady=8).pack(fill="x", padx=14, pady=12)
root.mainloop()
