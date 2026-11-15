"""Tray icon, its menu, and the Ctrl+Alt+Space shortcut."""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from typing import Callable

log = logging.getLogger("systemautobot.tray")

COLORS = {"fine": (22, 121, 74), "needs": (214, 140, 0), "paused": (110, 118, 129)}


def icon_image(state: str):
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((4, 4, 60, 60), radius=14, fill=(15, 108, 189))
    d.rectangle((18, 22, 46, 26), fill="white")    # a simple "S" mark made of bars
    d.rectangle((18, 22, 22, 33), fill="white")
    d.rectangle((18, 30, 46, 34), fill="white")
    d.rectangle((42, 30, 46, 42), fill="white")
    d.rectangle((18, 38, 46, 42), fill="white")
    if state in COLORS:  # "app" = the plain app icon, no status dot
        d.ellipse((42, 42, 60, 60), fill=COLORS[state], outline="white", width=3)
    return img


class Tray:
    def __init__(self, *, open_main: Callable[[], None], check_now: Callable[[], None],
                 is_paused: Callable[[], bool], set_paused: Callable[[bool], None],
                 digest_now: Callable[[], None] | None, quit_app: Callable[[], None]):
        import pystray

        self._paused = is_paused
        self._set_paused = set_paused
        items = [
            pystray.MenuItem("Open SystemAutoBot", lambda: open_main(), default=True),
            pystray.MenuItem("Check this computer", lambda: check_now()),
            pystray.MenuItem("Pause fixes", lambda: self._toggle(), checked=lambda item: self._paused()),
        ]
        if digest_now:
            items.append(pystray.MenuItem("End of day now (demo)", lambda: digest_now()))
        items += [pystray.Menu.SEPARATOR, pystray.MenuItem("Quit", lambda: quit_app())]
        self.icon = pystray.Icon("SystemAutoBot", icon_image("fine"), "SystemAutoBot: Fine", pystray.Menu(*items))

    def _toggle(self):
        self._set_paused(not self._paused())
        self.set_state(False)

    def set_state(self, needs_you: bool) -> None:
        state = "paused" if self._paused() else ("needs" if needs_you else "fine")
        self.icon.icon = icon_image(state)
        self.icon.title = {"paused": "SystemAutoBot: Fixes paused", "needs": "SystemAutoBot: Needs you",
                           "fine": "SystemAutoBot: Fine"}[state]

    def start(self) -> None:
        self.icon.run_detached()

    def stop(self) -> None:
        self.icon.stop()


def start_hotkey(callback: Callable[[], None]) -> None:
    """Ctrl+Alt+Space opens the window. Runs its own message loop thread."""
    if sys.platform != "win32":
        return

    def loop():
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, VK_SPACE, WM_HOTKEY = 0x1, 0x2, 0x4000, 0x20, 0x0312
        if not user32.RegisterHotKey(None, 1, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_SPACE):
            log.warning("Ctrl+Alt+Space is taken by another app")
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == WM_HOTKEY:
                try:
                    callback()
                except Exception:
                    log.exception("hotkey")

    threading.Thread(target=loop, name="hotkey", daemon=True).start()
