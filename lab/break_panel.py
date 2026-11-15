"""Break it panel for the live demo: one button per scenario, so a judge can choose.
Run INSIDE the lab VM, as admin:  python lab\\break_panel.py
"""

import subprocess
import tkinter as tk
from pathlib import Path

LAB = Path(__file__).resolve().parent
LIVE = [
    ("S1", "Fill the drive", "S1_full_drive.ps1"),
    ("S2", "Freeze a program", "S2_frozen_program.ps1"),
    ("S4", "Break the internet (DNS)", "S4_bad_dns.ps1"),
    ("S5", "Set the clock wrong", "S5_wrong_clock.ps1"),
    ("S6", "Jam the printer", "S6_stuck_print.ps1"),
    ("S11", "Inject a malicious error", "S11_prompt_injection.ps1"),
]


def run(script: str, undo: bool = False) -> None:
    args = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(LAB / "break" / script)]
    subprocess.Popen(args + (["-Undo"] if undo else []))
    status.set(("Undoing: " if undo else "Breaking: ") + script)


root = tk.Tk()
root.title("Break it (lab VM only)")
root.geometry("440x520")
tk.Label(root, text="Pick a problem. SystemAutoBot should notice it.", font=("Segoe UI", 12)).pack(pady=10)
for sid, label, script in LIVE:
    row = tk.Frame(root)
    row.pack(fill="x", padx=12, pady=4)
    tk.Button(row, text=f"{sid}  {label}", height=2, command=lambda s=script: run(s)).pack(
        side="left", fill="x", expand=True)
    tk.Button(row, text="Undo", height=2, command=lambda s=script: run(s, True)).pack(side="left", padx=6)
status = tk.StringVar(value="Ready.")
tk.Label(root, textvariable=status, fg="#555").pack(pady=10)
root.mainloop()
