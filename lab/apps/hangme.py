"""hangme: a window with unsaved text and a Freeze button that blocks its own UI thread,
so Windows marks it Not Responding. Lab scenario S2.

    python hangme.py            press Freeze yourself
    python hangme.py --freeze   freeze 3 seconds after opening (for the scenario runner)
"""
import sys
import time
import tkinter as tk

FREEZE_SECONDS = 600

root = tk.Tk()
root.title("hangme - unsaved notes")
root.geometry("420x260")
text = tk.Text(root, height=8)
text.insert("1.0", "Unsaved notes: this text is lost if the program is closed.")
text.pack(fill="both", expand=True, padx=8, pady=8)


def freeze():
    root.title("hangme - frozen")
    root.update()
    time.sleep(FREEZE_SECONDS)  # blocks the UI thread: the window stops answering


tk.Button(root, text="Freeze", command=freeze, height=2).pack(fill="x", padx=8, pady=8)
if "--freeze" in sys.argv:
    root.after(3000, freeze)
root.mainloop()
