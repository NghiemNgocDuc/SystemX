"""Tray icon, its menu, Windows notifications, and the Ctrl+Alt+Space shortcut."""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
import time
from typing import Callable

log = logging.getLogger("systemautobot.tray")

COLORS = {"fine": (46, 139, 87), "needs": (201, 138, 27), "paused": (120, 120, 130)}
NIN_BALLOONUSERCLICK = 0x0405  # WM_USER + 5: the person clicked the notification
TITLE_MAX, BODY_MAX = 60, 240  # Windows cuts notification text beyond about 63 and 255 characters


def icon_image(state: str):
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((4, 4, 60, 60), radius=14, fill=(47, 93, 158))
    d.rectangle((18, 22, 46, 26), fill="white")    # a simple "S" mark made of bars
    d.rectangle((18, 22, 22, 33), fill="white")
    d.rectangle((18, 30, 46, 34), fill="white")
    d.rectangle((42, 30, 46, 42), fill="white")
    d.rectangle((18, 38, 46, 42), fill="white")
    if state in COLORS:  # "app" = the plain app icon, no status dot
        d.ellipse((42, 42, 60, 60), fill=COLORS[state], outline="white", width=3)
    return img


def _short(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


class Tray:
    def __init__(self, *, open_main: Callable[[], None], check_now: Callable[[], None],
                 is_paused: Callable[[], bool], set_paused: Callable[[bool], None],
                 digest_now: Callable[[], None] | None, quit_app: Callable[[], None],
                 show_waiting: Callable[[], None] | None = None):
        import pystray

        self._paused = is_paused
        self._set_paused = set_paused
        self._on_notification_click = show_waiting or open_main
        items = [pystray.MenuItem("Open SystemAutoBot", lambda: open_main(), default=True)]
        if show_waiting:
            items.append(pystray.MenuItem("Show what needs me", lambda: show_waiting()))
        items += [
            pystray.MenuItem("Check this computer", lambda: check_now()),
            pystray.MenuItem("Pause fixes", lambda: self._toggle(), checked=lambda item: self._paused()),
        ]
        if digest_now:
            items.append(pystray.MenuItem("End of day now (demo)", lambda: digest_now()))
        items += [pystray.Menu.SEPARATOR, pystray.MenuItem("Quit", lambda: quit_app())]
        self.icon = pystray.Icon("SystemAutoBot", icon_image("fine"), "SystemAutoBot: Fine", pystray.Menu(*items))
        self._hook_notification_click()

    def _hook_notification_click(self) -> None:
        """pystray shows notifications but doesn't report a click on one. Windows sends that click to
        the tray icon's own message handler, so wrap the handler and pass everything else through."""
        if sys.platform != "win32":
            return
        try:
            from pystray._util import win32

            handlers = self.icon._message_handlers
            original = handlers[win32.WM_NOTIFY]

            def on_notify(wparam, lparam):
                if lparam & 0xFFFF == NIN_BALLOONUSERCLICK:
                    threading.Thread(target=self._clicked, name="notification-click", daemon=True).start()
                    return 0
                return original(wparam, lparam)

            handlers[win32.WM_NOTIFY] = on_notify
        except Exception:  # a pystray update changed its internals: the tray menu still opens what's waiting
            log.exception("could not listen for notification clicks")

    def _clicked(self) -> None:
        try:
            self._on_notification_click()
        except Exception:
            log.exception("notification click")

    def notify(self, title: str, message: str) -> bool:
        """Show a Windows notification from the tray icon. False if it couldn't be shown."""
        try:
            for _ in range(30):  # at startup the icon needs a moment before it can show anything
                if getattr(self.icon, "visible", False):
                    break
                time.sleep(0.1)
            else:
                return False  # no tray icon: the caller opens the box directly instead
            self.icon.notify(_short(message, BODY_MAX), _short(title, TITLE_MAX))
            return True
        except Exception:
            log.exception("could not show a notification")
            return False

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
