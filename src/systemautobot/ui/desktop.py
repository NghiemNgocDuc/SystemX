"""Windows on screen (pywebview + Edge WebView2) and the Presenter the pipeline talks to."""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from typing import Callable

log = logging.getLogger("systemautobot.ui")

POPUP_W, POPUP_H = 460, 560
DIGEST_W, DIGEST_H = 560, 680


def work_area() -> tuple[int, int, int, int]:
    """Screen area not covered by the taskbar: left, top, right, bottom, in the 100%-scale units
    pywebview positions windows in. On a 125% display the raw pixels are divided by 1.25,
    otherwise popups land off the right edge."""
    if sys.platform != "win32":
        return 0, 0, 1280, 800
    from ctypes import wintypes

    rect = wintypes.RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)  # SPI_GETWORKAREA
    try:
        scale = ctypes.windll.user32.GetDpiForSystem() / 96 or 1
    except (AttributeError, OSError):
        scale = 1
    return (int(rect.left / scale), int(rect.top / scale), int(rect.right / scale), int(rect.bottom / scale))


class Desktop:
    """Owns every window. Also the pipeline's Presenter."""

    def __init__(self, base_url: str, token: str, on_state: Callable[[bool], None] = lambda needs: None):
        self.base_url = base_url
        self.token = token
        self.on_state = on_state
        self.main = None
        self.popups: dict[str, object] = {}
        self.digest_window = None
        self._lock = threading.RLock()

    def url(self, page: str = "", **params: str) -> str:
        frag = "&".join([f"t={self.token}"] + [f"{k}={v}" for k, v in params.items()])
        return f"{self.base_url}/{page}#{frag}"

    # ---- main window ---------------------------------------------------------------

    def create_main(self, visible: bool):
        import webview

        self.main = webview.create_window("SystemAutoBot", self.url(), width=920, height=760, min_size=(400, 500),
                                          hidden=not visible, background_color="#15181C")
        self.main.events.closing += self._hide_instead_of_close
        return self.main

    def _hide_instead_of_close(self):
        # Closing the window leaves the helper running in the tray.
        if getattr(self, "_quitting", False):
            return True
        threading.Thread(target=self.main.hide, daemon=True).start()
        return False

    def show_main(self, view: str = "home") -> None:
        if not self.main:
            return
        self.main.show()
        self.main.restore()
        try:
            self.main.evaluate_js(f"window.sabShow && window.sabShow({view!r})")
        except Exception:
            pass

    # ---- popups (major problems) ----------------------------------------------------

    def _popup(self, key: str, card_ids: list[str], title: str) -> None:
        import webview

        with self._lock:
            if key in self.popups:
                return
            left, top, right, bottom = work_area()
            n = len(self.popups)
            x = right - POPUP_W - 16
            y = max(top, bottom - POPUP_H - 16 - n * 40)
            self.popups[key] = webview.create_window(
                title, self.url("popup", cards=",".join(card_ids), key=key), width=POPUP_W, height=POPUP_H,
                x=x, y=y, on_top=True, resizable=False, background_color="#15181C")
            self.popups[key].events.closed += lambda: self.popups.pop(key, None)
        self.on_state(True)

    def close_popup(self, key: str) -> None:
        with self._lock:
            w = self.popups.pop(key, None)
        if w:
            w.destroy()

    def close_digest(self) -> None:
        if self.digest_window:
            w, self.digest_window = self.digest_window, None
            w.destroy()

    # ---- Presenter --------------------------------------------------------------------

    def show_popup(self, card) -> None:
        self._popup(card.id, [card.id], "SystemAutoBot needs you")

    def show_grouped(self, cards) -> None:
        self._popup("group-" + cards[0].id, [c.id for c in cards], f"{len(cards)} more things need you")

    def show_digest(self, items, went_away) -> None:
        import webview

        if self.digest_window:
            return
        left, top, right, bottom = work_area()
        self.digest_window = webview.create_window(
            "End of day", self.url("digest"), width=DIGEST_W, height=DIGEST_H,
            x=right - DIGEST_W - 16, y=max(top, bottom - DIGEST_H - 16), on_top=True, background_color="#15181C")
        self.on_state(True)

    def closed(self, card, reason: str) -> None:
        self.close_popup(card.id)

    def refresh(self) -> None:
        pass  # windows poll /api/state themselves


class HeadlessDesktop(Desktop):
    """--browser mode for development: no webview, pages open in the default browser."""

    def create_main(self, visible: bool):
        import webbrowser

        if visible:
            webbrowser.open(self.url())

    def show_main(self, view: str = "home") -> None:
        import webbrowser

        webbrowser.open(self.url(view=view))

    def _popup(self, key, card_ids, title):
        import webbrowser

        webbrowser.open(self.url("popup", cards=",".join(card_ids), key=key))
        self.on_state(True)

    def show_digest(self, items, went_away) -> None:
        import webbrowser

        webbrowser.open(self.url("digest"))

    def close_popup(self, key: str) -> None:
        pass

    def close_digest(self) -> None:
        pass
