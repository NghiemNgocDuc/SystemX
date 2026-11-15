"""Guided laptop demo, with real detection and repair evidence.

Start SystemAutoBot with --demo --lab first, then run this file with the same Python.
The panel never approves a repair. Allow / Not now remain in the normal app UI.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk
import urllib.error
import urllib.request

from demo_flow import DemoSession, readiness
from systemautobot.demo_support import notes_command

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "lab"
EVENTS = {
    "disk": LAB / "replay" / "S9_disk_failing.xml",
    "crash": ROOT / "tests" / "fixtures" / "events" / "app_error_1000.xml",
    "inject": ROOT / "tests" / "fixtures" / "events" / "prompt_injection.xml",
}
BG, SURFACE, INK, MUTED, ACCENT = "#f4f4f6", "#ffffff", "#182332", "#4b586a", "#175cd3"


def lab_call(path: str, body: dict | None = None):
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    token_file = local / "SystemAutoBot" / "lab_token.txt"
    try:
        token, base = token_file.read_text(encoding="utf-8").split()
    except (OSError, ValueError):
        raise RuntimeError("Start the app with --demo --lab first.") from None
    req = urllib.request.Request(
        base + path, method="POST" if body is not None else "GET",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-SAB-Lab-Token": token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            return json.loads(response.read() or b"null")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"The demo app rejected the request ({e.code}). Restart it with --demo --lab.") from None
    except OSError:
        raise RuntimeError("Can't reach the demo app. Start it with --demo --lab; the panel will reconnect.") from None


def replay(kind: str) -> str:
    routes = lab_call("/api/lab/replay", {"xml": EVENTS[kind].read_text(encoding="utf-8")})["routes"]
    if any(r in routes for r in ("popup", "grouped")):
        return "Recorded event delivered. Open SystemAutoBot Home to see the problem and proposed next step."
    if "digest" in routes:
        return "Recorded event saved for the end-of-day list. Press Open end-of-day list."
    if any(r in routes for r in ("duplicate", "snoozed")):
        return "This problem is already waiting or was deferred. Check Home and the end-of-day list."
    return "Recorded event routes: " + ", ".join(routes)


def error_box() -> str:
    script = ("Add-Type -AssemblyName System.Windows.Forms; "
              "[void][System.Windows.Forms.MessageBox]::Show("
              "'Cannot open the project file. Error 0x80070002: The system cannot find the file specified.', "
              "'Demo Editor', 0, 16)")
    subprocess.Popen(["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script])
    return "A real error box opened. After about 6 s, look on Home and press Fix for me. AI diagnosis needs a connected provider."


def redteam() -> str:
    # Keep the actual exit code and pytest summary visible for the audience.
    command = ('& $env:SAB_DEMO_PY -m pytest $env:SAB_DEMO_TEST -v; '
               '$result = $LASTEXITCODE; Write-Host "Suite exit code: $result"; '
               'Read-Host "Press Enter to close"; exit $result')
    subprocess.Popen(["powershell.exe", "-NoProfile", "-Command", command], cwd=ROOT,
                     env=dict(os.environ, SAB_DEMO_PY=sys.executable,
                              SAB_DEMO_TEST=str(ROOT / "tests" / "test_redteam.py")),
                     creationflags=subprocess.CREATE_NEW_CONSOLE)
    return "Safety tests opened in a console. Show the actual pytest summary and exit code; no success is assumed."


class DemoPanel:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.events: queue.Queue = queue.Queue()
        self.session: DemoSession | None = None
        self.children: list[subprocess.Popen] = []
        self.state: dict = {}
        self.record: dict | None = None
        self.polling = False
        self.busy = False
        self.closed = False
        root.title("SystemAutoBot - live demo")
        root.geometry("1000x790")
        root.minsize(820, 680)
        root.configure(bg=BG)
        root.protocol("WM_DELETE_WINDOW", self.close)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(18, 10), font=("Segoe UI", 11))
        style.configure("TFrame", background=BG)

        header = tk.Frame(root, bg=BG)
        header.pack(fill="x", padx=24, pady=(20, 12))
        self.label(header, "SYSTEMAUTOBOT / LIVE DEMO", 10, MUTED).pack(anchor="w")
        self.label(header, "A frozen app. One approval. A visible recovery.", 23, bold=True).pack(anchor="w", pady=(6, 4))
        self.label(header, "SystemAutoBot notices PC problems, offers a repair, and records what changed.", 12).pack(anchor="w")
        self.connection = tk.StringVar(value="Connecting to the demo app...")
        self.label(header, self.connection, 11, ACCENT).pack(anchor="w", pady=(12, 0))

        tabs = ttk.Notebook(root)
        tabs.pack(fill="both", expand=True, padx=24, pady=(0, 16))
        live, more = ttk.Frame(tabs), ttk.Frame(tabs)
        tabs.add(live, text="1  Live recovery")
        tabs.add(more, text="2  More capabilities")
        self.build_live(live)
        self.build_more(more)
        self.message = tk.StringVar(value="Only disposable demo notes are used. The main recovery needs no internet or AI key.")
        self.label(root, self.message, 11, MUTED, wrap=930).pack(fill="x", padx=24, pady=(0, 16))
        root.after(100, self.drain)
        self.poll()

    @staticmethod
    def label(parent, text, size=12, color=INK, bold=False, wrap=0):
        options = {"textvariable": text} if isinstance(text, tk.Variable) else {"text": text}
        return tk.Label(parent, **options, bg=parent.cget("background") if isinstance(parent, tk.Frame) else BG,
                        fg=color, font=("Segoe UI", size, "bold" if bold else "normal"),
                        anchor="w", justify="left", wraplength=wrap)

    @staticmethod
    def button(parent, text, command, primary=False):
        return tk.Button(parent, text=text, command=command, font=("Segoe UI", 11, "bold"),
                         bg=ACCENT if primary else SURFACE, fg=SURFACE if primary else INK,
                         activebackground="#124ba9" if primary else "#e6ebf2",
                         activeforeground=SURFACE if primary else INK,
                         relief="flat", bd=0, padx=16, pady=12, cursor="hand2", takefocus=True)

    def build_live(self, parent):
        steps = tk.Frame(parent, bg=BG)
        steps.pack(fill="x", pady=16)
        self.steps = []
        for name in ("1  Freeze", "2  Detect", "3  Approve", "4  Check + record"):
            cell = self.label(steps, name, 12, MUTED, bold=True)
            cell.pack(side="left", expand=True, fill="x")
            self.steps.append(cell)
        area = tk.Frame(parent, bg=BG)
        area.pack(fill="both", expand=True)
        area.columnconfigure(0, weight=1, uniform="col")
        area.columnconfigure(1, weight=1, uniform="col")
        area.rowconfigure(0, weight=1)
        left = tk.Frame(area, bg=SURFACE, padx=20, pady=20)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        right = tk.Frame(area, bg=SURFACE, padx=20, pady=20)
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self.label(left, "WHAT TO SHOW", 10, MUTED, bold=True).pack(anchor="w")
        self.title = tk.StringVar(value="Start with a real frozen test window.")
        self.label(left, self.title, 20, bold=True, wrap=390).pack(fill="x", pady=(10, 14))
        self.next = tk.StringVar(value="Press Start live recovery. Demo Notes opens, then freezes after 3 seconds. SystemAutoBot must detect it on its own.")
        self.label(left, self.next, 12, wrap=390).pack(fill="x", pady=(0, 20))
        self.start_button = self.button(left, "Start live recovery", self.start, primary=True)
        self.start_button.pack(fill="x", pady=4)
        self.open_button = self.button(left, "Open approval box", self.open_card)
        self.open_button.pack(fill="x", pady=4)
        self.open_button.config(state="disabled")
        self.button(left, "Close demo windows / start over", self.tidy).pack(fill="x", pady=4)
        self.label(left, "SAY THIS", 10, MUTED, bold=True).pack(anchor="w", pady=(24, 8))
        self.label(left, '"It found the frozen program without a prompt. I choose whether to allow the repair. Then it checks again and keeps a record."',
                   12, wrap=390).pack(fill="x")
        self.label(right, "LIVE EVIDENCE", 10, MUTED, bold=True).pack(anchor="w")
        self.label(right, "From this run's app card and fix history", 11, MUTED, wrap=390).pack(fill="x", pady=(6, 16))
        self.evidence = tk.Text(right, width=35, height=12, bg=SURFACE, fg=INK, relief="flat",
                                wrap="word", font=("Segoe UI", 12), padx=0, pady=4, state="disabled")
        self.evidence.pack(fill="both", expand=True)
        self.set_evidence(["No run yet.", "Detection, repair steps, and re-check results appear here when the app reports them.",
                           "After repair, type in the reopened notes window to demonstrate that it responds."])

    def build_more(self, parent):
        self.label(parent, "Show these after the live recovery", 18, bold=True).pack(anchor="w", pady=(16, 6))
        self.label(parent, "Each example demonstrates a different capability. Recorded events are labeled; their repair buttons can still run real actions.",
                   11, MUTED, wrap=900).pack(anchor="w", pady=(0, 8))
        examples = [
            ("Prioritize urgent problems", "RECORDED", "Replay a failing-drive event. Show the backup warning on Home. Software cannot repair worn-out hardware.",
             "Replay disk warning", lambda: replay("disk")),
            ("Keep small problems quiet", "RECORDED", "Replay an app crash, then open the end-of-day list. Minor issues wait together instead of interrupting.",
             "Replay app crash", lambda: replay("crash")),
            ("Explain unfamiliar errors", "LIVE / AI CONNECTION REQUIRED", "Open a real error box. On Home, press Fix for me to show the AI's checks and proposed plan.",
             "Show error box", error_box),
            ("Keep repairs inside the toolbox", "LOCAL TEST SUITE", "Run hostile-AI tests. Present the actual test results, then explain that a proposal cannot bypass approval or path guards.",
             "Run safety tests", redteam),
        ]
        for title, tag, detail, action, fn in examples:
            row = tk.Frame(parent, bg=SURFACE, padx=16, pady=12)
            row.pack(fill="x", pady=5)
            text = tk.Frame(row, bg=SURFACE)
            text.pack(side="left", fill="x", expand=True)
            self.label(text, title + "  /  " + tag, 11, bold=True, wrap=620).pack(anchor="w")
            self.label(text, detail, 11, MUTED, wrap=620).pack(anchor="w", pady=(4, 0))
            self.button(row, action, lambda fn=fn: self.action(fn)).pack(side="right", padx=(12, 0))
        footer = tk.Frame(parent, bg=BG)
        footer.pack(fill="x", pady=8)
        self.button(footer, "Open end-of-day list", lambda: self.action(self.digest)).pack(side="left")
        self.button(footer, "Replay malicious error", lambda: self.action(lambda: replay("inject"))).pack(side="left", padx=8)

    @staticmethod
    def digest():
        result = lab_call("/api/lab/digest_now", {})
        return f"Opened the end-of-day list with {len(result['items'])} item(s)."

    def set_evidence(self, lines):
        self.evidence.config(state="normal")
        self.evidence.delete("1.0", "end")
        self.evidence.insert("1.0", "\n\n".join(line for line in lines if line))
        self.evidence.config(state="disabled")

    def action(self, fn):
        if self.busy:
            return
        self.busy = True
        self.message.set("Working...")

        def worker():
            try:
                self.events.put(("message", fn()))
            except Exception as e:
                self.events.put(("message", str(e)))
            finally:
                self.events.put(("idle", None))

        threading.Thread(target=worker, daemon=True).start()

    def start(self):
        if self.session or self.busy:
            return
        problem = readiness(self.state) if self.state else "Waiting for the demo app connection."
        if problem:
            self.message.set(problem)
            return
        try:
            child = subprocess.Popen(notes_command(freeze=True))
        except OSError as e:
            self.message.set(f"Couldn't start Demo Notes: {e}")
            return
        self.children.append(child)
        self.session = DemoSession(child.pid, time.monotonic())
        self.record = None
        self.start_button.config(state="disabled")
        self.message.set("This test loses only its disposable notes. Repair approval stays in the normal app window.")
        self.render()

    def open_card(self):
        if self.session and self.session.card_id:
            card_id = self.session.card_id
            self.action(lambda: (lab_call("/api/lab/show_card", {"card_id": card_id}),
                                 "Approval box opened. Read the warning and choose Allow or Not now in SystemAutoBot.")[1])

    def tidy(self):
        # Only exact test-program processes. Never close arbitrary Python programs by substring.
        from systemautobot.demo_support import is_demo_notes
        import psutil

        def clean():
            for proc in psutil.process_iter(["pid"]):
                if is_demo_notes(proc.pid):
                    try:
                        proc.terminate()
                        proc.wait(timeout=3)
                    except psutil.Error:
                        pass
            return "Closed Demo Notes test windows. The real fix history remains in Activity."

        if not self.busy:
            self.session = None
            self.record = None
            self.action(clean)
            self.title.set("Start with a real frozen test window.")
            self.next.set("Start a new run when ready. Previous repair records stay in Activity.")
            self.set_evidence(["No active run."])
            self.open_button.config(state="disabled")
            self.render()

    def poll(self):
        if self.closed or self.polling:
            return
        self.polling = True
        active = self.session

        def worker():
            try:
                state = lab_call("/api/lab/state")
                record = None
                if active and active.card_id:
                    rec = next((r for r in state.get("history", []) if r.get("card_id") == active.card_id), None)
                    if rec:
                        record = lab_call("/api/lab/history/" + rec["id"])
                self.events.put(("state", (state, record, active)))
            except Exception as e:
                self.events.put(("connection_error", str(e)))
            finally:
                self.events.put(("polled", None))

        threading.Thread(target=worker, daemon=True).start()

    def render(self):
        problem = readiness(self.state) if self.state else "Waiting for the demo app."
        self.start_button.config(state="normal" if self.state and not problem and not self.session and not self.busy else "disabled")
        if self.session:
            view = self.session.observe(self.state, time.monotonic(), self.record)
            self.title.set(view["title"])
            self.next.set(view["next"])
            self.set_evidence(view["evidence"])
            self.open_button.config(state="normal" if view["can_open"] and not self.busy else "disabled")
            stage = view["stage"]
        else:
            stage = 0
        for i, cell in enumerate(self.steps, 1):
            cell.config(fg=ACCENT if i <= stage else MUTED)

    def drain(self):
        if self.closed:
            return
        while not self.events.empty():
            kind, value = self.events.get_nowait()
            if kind == "state":
                self.state, record, active = value
                if active is self.session:
                    self.record = record
                issue = readiness(self.state)
                self.connection.set("Demo app connected. " + (issue or "Ready for a live recovery."))
                self.render()
            elif kind == "connection_error":
                self.state = {}
                self.connection.set(value)
                self.start_button.config(state="disabled")
                self.open_button.config(state="disabled")
            elif kind == "message":
                self.message.set(value)
            elif kind == "idle":
                self.busy = False
                self.render()
            elif kind == "polled":
                self.polling = False
                self.root.after(1000, self.poll)
        self.root.after(100, self.drain)

    def close(self):
        self.closed = True
        self.root.destroy()


def main():
    root = tk.Tk()
    DemoPanel(root)
    root.mainloop()


if __name__ == "__main__":
    main()
