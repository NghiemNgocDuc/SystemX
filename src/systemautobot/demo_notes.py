"""Disposable notes window for the live demo. Never opens the person's files."""

from __future__ import annotations

import sys
import tkinter as tk
import time


def main() -> None:
    root = tk.Tk()
    root.title("Demo Notes - responding")
    root.geometry("480x340")
    root.configure(bg="#f4f4f6")
    tk.Label(root, text="Demo Notes", font=("Segoe UI", 20, "bold"), bg="#f4f4f6").pack(pady=(16, 4))
    status = tk.StringVar(value="Responding. Type below to try it.")
    tk.Label(root, textvariable=status, bg="#f4f4f6", font=("Segoe UI", 11)).pack()
    text = tk.Text(root, height=5, font=("Segoe UI", 12), wrap="word")
    text.insert("1.0", "Disposable demo text. Closing this window loses these notes.\nNo real documents are opened.")
    text.pack(fill="both", expand=True, padx=16, pady=12)

    def freeze():
        root.title("Demo Notes - frozen")
        status.set("Frozen. SystemAutoBot can now detect this window.")
        root.update_idletasks()
        time.sleep(600)  # deliberately blocks only this test window's UI thread

    tk.Button(root, text="Freeze this test window", command=freeze, font=("Segoe UI", 11), pady=8).pack(
        fill="x", padx=16, pady=(0, 16))
    if "--freeze" in sys.argv:
        root.after(3000, freeze)
    root.mainloop()


if __name__ == "__main__":
    main()
