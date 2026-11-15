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

from demo_flow import CleanupSession, DemoSession, readiness
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
        self.cleanup_session: CleanupSession | None = None
        self.cleanup_measurement: dict | None = None
        self.cleanup_record: dict | None = None
        self.cleanup_complete = False
        self.cleanup_can_retry = False
        self.polling = False
        self.busy = False
        self.closed = False
        root.title("SystemAutoBot - live demo")
        root.geometry("900x680")
        root.minsize(820, 620)
        root.configure(bg=BG)
        root.protocol("WM_DELETE_WINDOW", self.close)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(14, 7), font=("Segoe UI", 11))
        style.configure("TFrame", background=BG)

        header = tk.Frame(root, bg=BG)
        header.pack(fill="x", padx=20, pady=(12, 8))
        self.label(header, "SystemAutoBot: two real demos.", 21, bold=True).pack(anchor="w", pady=(4, 2))
        self.label(header, "SystemAutoBot notices PC problems, offers a repair, and records what changed.", 12).pack(anchor="w")
        self.connection = tk.StringVar(value="Connecting to the demo app...")
        self.label(header, self.connection, 10, ACCENT).pack(anchor="w", pady=(6, 0))

        tabs = ttk.Notebook(root)
        tabs.pack(fill="both", expand=True, padx=20, pady=(0, 8))
        live, cleanup, more = ttk.Frame(tabs), ttk.Frame(tabs), ttk.Frame(tabs)
        tabs.add(live, text="1  Frozen app")
        tabs.add(cleanup, text="2  Free up space")
        tabs.add(more, text="More capabilities")
        self.build_live(live)
        self.build_cleanup(cleanup)
        self.build_more(more)
        self.message = tk.StringVar(value="Both demos use disposable test data and need no AI key. Close the app window to keep monitoring in the tray.")
        self.label(root, self.message, 10, MUTED, wrap=850).pack(fill="x", padx=20, pady=(0, 10))
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
                         relief="flat", bd=0, padx=12, pady=8, cursor="hand2", takefocus=True)

    def build_live(self, parent):
        steps = tk.Frame(parent, bg=BG)
        steps.pack(fill="x", pady=10)
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
        left = tk.Frame(area, bg=SURFACE, padx=16, pady=14)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        right = tk.Frame(area, bg=SURFACE, padx=16, pady=14)
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self.label(left, "WHAT TO SHOW", 10, MUTED, bold=True).pack(anchor="w")
        self.title = tk.StringVar(value="Start with a real frozen test window.")
        self.label(left, self.title, 17, bold=True, wrap=365).pack(fill="x", pady=(8, 10))
        self.next = tk.StringVar(value="Press Start live recovery. Demo Notes opens, then freezes after 3 seconds. SystemAutoBot must detect it on its own.")
        self.label(left, self.next, 11, wrap=365).pack(fill="x", pady=(0, 10))
        actions = tk.Frame(left, bg=SURFACE)
        actions.pack(fill="x", pady=4)
        self.start_button = self.button(actions, "Start recovery", self.start, primary=True)
        self.start_button.pack(side="left", expand=True, fill="x", padx=(0, 4))
        self.open_button = self.button(actions, "Review repair", self.open_card)
        self.open_button.pack(side="left", expand=True, fill="x", padx=(4, 0))
        self.open_button.config(state="disabled")
        self.button(left, "Close demo windows / start over", self.tidy).pack(fill="x", pady=4)
        self.label(left, '"It spots the frozen app, asks permission, and records the repair."',
                   11, MUTED, wrap=365).pack(fill="x", pady=(10, 0))
        self.label(right, "LIVE EVIDENCE", 10, MUTED, bold=True).pack(anchor="w")
        self.label(right, "From this run's app card and fix history", 10, MUTED, wrap=365).pack(fill="x", pady=(6, 10))
        self.evidence = tk.Text(right, width=35, height=12, bg=SURFACE, fg=INK, relief="flat",
                                wrap="word", font=("Segoe UI", 11), padx=0, pady=4, state="disabled")
        self.evidence.pack(fill="both", expand=True)
        self.set_evidence(["No run yet.", "Detection, repair steps, and re-check results appear here when the app reports them.",
                           "After repair, type in the reopened notes window to demonstrate that it responds."])

    def build_cleanup(self, parent):
        steps = tk.Frame(parent, bg=BG)
        steps.pack(fill="x", pady=10)
        self.cleanup_steps = []
        for name in ("1  Create test files", "2  Review", "3  Approve", "4  Measure + record"):
            cell = self.label(steps, name, 11, MUTED, bold=True)
            cell.pack(side="left", expand=True, fill="x")
            self.cleanup_steps.append(cell)
        area = tk.Frame(parent, bg=BG)
        area.pack(fill="both", expand=True)
        area.columnconfigure(0, weight=1, uniform="col")
        area.columnconfigure(1, weight=1, uniform="col")
        area.rowconfigure(0, weight=1)
        left = tk.Frame(area, bg=SURFACE, padx=16, pady=14)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        right = tk.Frame(area, bg=SURFACE, padx=16, pady=14)
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self.label(left, "WHAT TO SHOW", 10, MUTED, bold=True).pack(anchor="w")
        self.cleanup_title = tk.StringVar(value="Create 24 MiB of disposable files.")
        self.label(left, self.cleanup_title, 17, bold=True, wrap=365).pack(fill="x", pady=(8, 10))
        self.cleanup_next = tk.StringVar(value="Start creates real test files in a dedicated demo folder and scans that folder. Then approve the cleanup and compare the measured sizes.")
        self.label(left, self.cleanup_next, 11, wrap=365).pack(fill="x", pady=(0, 10))
        self.cleanup_start_button = self.button(left, "Start cleanup demo", self.start_cleanup, primary=True)
        self.cleanup_start_button.pack(fill="x", pady=4)
        self.cleanup_open_button = self.button(left, "Open approval box", self.open_cleanup_card)
        self.cleanup_open_button.pack(fill="x", pady=4)
        self.cleanup_open_button.config(state="disabled")
        self.label(left, '"These are real disposable files. I approve their removal, then SystemAutoBot measures what remains and records the result."',
                   11, MUTED, wrap=365).pack(fill="x", pady=(10, 0))
        self.label(left, "This demo scans its test folder when started. It does not fill the drive or simulate a low-disk alert.",
                   10, MUTED, wrap=365).pack(fill="x", pady=(10, 0))
        self.label(right, "REAL FILE SIZES", 10, MUTED, bold=True).pack(anchor="w")
        self.cleanup_metrics = {}
        for key, title in (("before", "Before cleanup"), ("remaining", "Still in folder"), ("recovered", "Verified removed")):
            row = tk.Frame(right, bg=SURFACE)
            row.pack(fill="x", pady=(8, 0))
            self.label(row, title, 11, MUTED).pack(side="left")
            value = tk.StringVar(value="—")
            self.cleanup_metrics[key] = value
            self.label(row, value, 12, ACCENT, bold=True).pack(side="right")
        self.label(right, "LIVE EVIDENCE", 10, MUTED, bold=True).pack(anchor="w", pady=(16, 6))
        self.cleanup_evidence = tk.Text(right, width=35, height=8, bg=SURFACE, fg=INK, relief="flat",
                                        wrap="word", font=("Segoe UI", 10), state="disabled")
        self.cleanup_evidence.pack(fill="both", expand=True)
        self.set_cleanup_evidence(["No run yet. Only files generated for this demo are eligible for removal.",
                                   "Before and after measurements and the ordinary app repair record appear here."])

    def build_more(self, parent):
        canvas = tk.Canvas(parent, bg=BG, height=390, highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        content = tk.Frame(canvas, bg=BG)
        window = canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        parent = content
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

    def set_cleanup_evidence(self, lines):
        self.cleanup_evidence.config(state="normal")
        self.cleanup_evidence.delete("1.0", "end")
        self.cleanup_evidence.insert("1.0", "\n\n".join(line for line in lines if line))
        self.cleanup_evidence.config(state="disabled")

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

    def start_cleanup(self):
        if self.busy or (self.cleanup_session and not self.cleanup_complete and not self.cleanup_can_retry):
            return
        problem = readiness(self.state, frozen=False) if self.state else "Waiting for the demo app connection."
        if problem:
            self.message.set(problem)
            return

        def begin():
            measured = lab_call("/api/lab/cleanup/start", {})
            self.events.put(("cleanup_started", measured))
            return "Demo folder checked and measured. Open the ordinary approval box to review the cleanup."

        self.action(begin)
        self.render()

    def open_cleanup_card(self):
        if self.cleanup_session:
            card_id = self.cleanup_session.card_id
            self.action(lambda: (lab_call("/api/lab/show_card", {"card_id": card_id}),
                                 "Review the exact demo folder, then choose Allow or Not now in SystemAutoBot.")[1])

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
        cleanup_active = self.cleanup_session

        def worker():
            try:
                state = lab_call("/api/lab/state")
                record = None
                if active and active.card_id:
                    rec = next((r for r in state.get("history", []) if r.get("card_id") == active.card_id), None)
                    if rec:
                        record = lab_call("/api/lab/history/" + rec["id"])
                cleanup_record = None
                measured = None
                if cleanup_active:
                    measured = lab_call("/api/lab/cleanup/" + cleanup_active.run_id)
                    rec = next((r for r in state.get("history", []) if r.get("card_id") == cleanup_active.card_id), None)
                    if rec:
                        cleanup_record = lab_call("/api/lab/history/" + rec["id"])
                self.events.put(("state", (state, record, active, measured, cleanup_record, cleanup_active)))
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
        cleanup_problem = readiness(self.state, frozen=False) if self.state else "Waiting for the demo app."
        cleanup_stage = 0
        if self.cleanup_session:
            view = self.cleanup_session.observe(self.state, self.cleanup_measurement, self.cleanup_record)
            self.cleanup_complete = view["complete"]
            self.cleanup_can_retry = view["can_retry"]
            cleanup_stage = view["stage"]
            self.cleanup_title.set(view["title"])
            self.cleanup_next.set(view["next"])
            self.set_cleanup_evidence(view["evidence"])
            for name, value in self.cleanup_metrics.items():
                value.set(view[name])
            self.cleanup_open_button.config(state="normal" if view["can_open"] and not self.busy and not cleanup_problem else "disabled")
        self.cleanup_start_button.config(
            text="Run cleanup demo again" if self.cleanup_complete else
                 "Review cleanup again" if self.cleanup_can_retry else "Start cleanup demo",
            state="normal" if self.state and not cleanup_problem and not self.busy
            and (not self.cleanup_session or self.cleanup_complete or self.cleanup_can_retry) else "disabled")
        for i, cell in enumerate(self.cleanup_steps, 1):
            cell.config(fg=ACCENT if i <= cleanup_stage else MUTED)

    def drain(self):
        if self.closed:
            return
        while not self.events.empty():
            kind, value = self.events.get_nowait()
            if kind == "state":
                self.state, record, active, measured, cleanup_record, cleanup_active = value
                if active is self.session:
                    self.record = record
                if cleanup_active is self.cleanup_session:
                    self.cleanup_measurement = measured
                    self.cleanup_record = cleanup_record
                issue = readiness(self.state, frozen=False)
                self.connection.set("Demo app connected. " + (issue or "Ready for both demos. Repairs need your approval."))
                self.render()
            elif kind == "connection_error":
                self.state = {}
                self.connection.set(value)
                self.start_button.config(state="disabled")
                self.open_button.config(state="disabled")
                self.cleanup_start_button.config(state="disabled")
                self.cleanup_open_button.config(state="disabled")
                if self.cleanup_session:
                    self.cleanup_measurement = None
                    self.cleanup_record = None
                    self.cleanup_complete = False
                    self.cleanup_can_retry = False
                    self.cleanup_title.set("Connection lost. Waiting for a fresh measurement.")
                    self.cleanup_metrics["remaining"].set("—")
                    self.cleanup_metrics["recovered"].set("Not verified")
            elif kind == "cleanup_started":
                self.cleanup_session = CleanupSession(value["run_id"], value["card_id"])
                self.cleanup_measurement = value
                self.cleanup_record = None
                self.cleanup_complete = False
                self.cleanup_can_retry = False
                self.render()
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
