"""The desktop app: tray + watcher + local server + windows.

  SystemAutoBot.exe                 normal start (tray; window on first run)
  SystemAutoBot.exe --demo          checks every 10 s, "End of day now" button, demo banner
  SystemAutoBot.exe --demo --lab    also the lab-only endpoints (VM lab only)
  SystemAutoBot.exe --observe       fixes forced to paused (observe-only build for teammates)
  SystemAutoBot.exe --replay FILE   recorded AI answers instead of a live provider (demo backup)
  python -m systemautobot app --browser   development: pages in the default browser
"""

from __future__ import annotations

import argparse
import logging
import os
import secrets
import socket
import threading
import time
import urllib.request
from pathlib import Path

from . import __version__

log = logging.getLogger("systemautobot.app")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for(url: str, seconds: float = 15) -> None:
    end = time.time() + seconds
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=1)
            return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("The local server didn't start.")


def parse(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="SystemAutoBot")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--lab", action="store_true")
    ap.add_argument("--observe", action="store_true")
    ap.add_argument("--browser", action="store_true")
    ap.add_argument("--show", action="store_true", help="open the window at start")
    ap.add_argument("--replay", type=Path)
    ap.add_argument("--debug", action="store_true")
    return ap.parse_args(argv)


def run(argv: list[str] | None = None) -> int:
    # A windowed app (pythonw, or the packaged .exe) has no console: stdout and stderr are None,
    # which crashes libraries that write or check them. Give them somewhere harmless to go.
    import sys

    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    args = parse(argv)
    from .ui.instance import acquire_single_instance, message_box, relaunch_unelevated_or_refuse
    from .tools.checks import is_admin

    if not relaunch_unelevated_or_refuse(is_admin()):
        return 1
    if not acquire_single_instance():
        message_box("SystemAutoBot is already running. Look for it in the tray, or press Ctrl+Alt+Space.")
        return 0

    from .__main__ import setup_logging
    from .grok.service import AssistantService
    from .history import History
    from .paths import AppPaths
    from .pipeline import build_pipeline
    from .settings import Settings, SettingsStore
    from .ui.desktop import Desktop, HeadlessDesktop
    from .ui.tray import Tray, start_hotkey
    from .watcher import Watcher
    from .web.server import AppContext, create_app

    paths = AppPaths.default().ensure()
    setup_logging(paths)
    demo = args.demo or args.lab
    settings = Settings.demo_mode() if demo else Settings()
    store = SettingsStore(paths.base / "settings.json", settings)
    store.load()
    if args.observe:
        settings.fixes_paused = True

    history = History(paths)
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    pipeline = build_pipeline(settings, history)
    holder: dict = {}
    desktop_cls = HeadlessDesktop if args.browser else Desktop
    desktop = desktop_cls(base, pipeline.runner.token, on_state=lambda needs: holder.get("tray") and holder["tray"].set_state(needs))
    pipeline.presenter = desktop
    service = AssistantService(pipeline, pipeline.tools, settings, replay_file=args.replay)
    from .autopilot import Autopilot

    # Critical problems reach Autopilot first. Off (the default), it hands every one to the person.
    pipeline.presenter = Autopilot(pipeline, service, settings, human=desktop, tell=desktop.tell,
                                   launcher=pipeline.tools.launcher)
    watcher = Watcher(pipeline.handle, settings)

    lab_token = None
    if args.lab:
        lab_token = secrets.token_urlsafe(24)
        (paths.base / "lab_token.txt").write_text(f"{lab_token}\n{base}\n", encoding="utf-8")

    ctx = AppContext(pipeline, history, service, store, lab_token=lab_token, origin=base, watcher=watcher,
                     ui=desktop, version=__version__)

    import uvicorn

    # log_config=None: the app's own logging (app.log) is used; uvicorn's console setup is skipped.
    server = uvicorn.Server(uvicorn.Config(create_app(ctx), host="127.0.0.1", port=port, log_level="warning",
                                           access_log=False, log_config=None))
    threading.Thread(target=server.run, name="server", daemon=True).start()
    _wait_for(base + "/api/health")

    interrupted = pipeline.recover_interrupted()
    if interrupted:
        log.warning("%d repair(s) were interrupted last time", len(interrupted))
    pipeline.state_path = paths.base / "waiting.json"
    restored = pipeline.restore_waiting()
    if restored:
        log.info("restored %d waiting problem(s) from last time", restored)
    watcher.start()

    stop = threading.Event()

    def ticker():
        last = None
        while not stop.is_set():
            try:
                pipeline.tick()
                needs = bool(pipeline.cards_view())
                if needs != last and holder.get("tray"):
                    holder["tray"].set_state(needs)
                    last = needs
            except Exception:
                log.exception("tick")
            stop.wait(1.0)

    def set_paused(value: bool) -> None:
        pipeline.runner.paused = value
        store.update(fixes_paused=value)

    def quit_app() -> None:
        stop.set()
        watcher.stop()
        server.should_exit = True
        desktop._quitting = True
        if holder.get("tray"):
            holder["tray"].stop()
        try:
            import webview

            for w in list(webview.windows):
                w.destroy()
        except Exception:
            pass
        threading.Timer(1.5, lambda: os._exit(0)).start()

    tray = Tray(open_main=lambda: desktop.show_main(), check_now=lambda: threading.Thread(target=watcher.poll_once, daemon=True).start(),
                is_paused=lambda: pipeline.runner.paused, set_paused=set_paused,
                digest_now=(lambda: pipeline.show_digest()) if demo else None, quit_app=quit_app,
                show_waiting=lambda: desktop.open_pending() or desktop.show_main(desktop.take_view()))
    holder["tray"] = tray
    tray.start()
    tray.set_state(False)

    def still_waiting(card_id: str) -> bool:
        card = pipeline.runner.cards.get(card_id)
        return card is not None and not card.used

    # Critical problems are announced as Windows notifications; a click opens the small Allow box.
    desktop.notifier = tray.notify
    desktop.is_open = still_waiting
    threading.Thread(target=ticker, name="ticker", daemon=True).start()
    start_hotkey(lambda: desktop.show_main())

    visible = args.show or demo or not settings.setup_done
    if args.browser:
        desktop.create_main(visible)
        print(f"SystemAutoBot is running at {base} (development mode). Ctrl+C to quit.")
        try:
            while not stop.is_set():
                time.sleep(0.5)
        except KeyboardInterrupt:
            quit_app()
        return 0

    import webview

    desktop.create_main(visible)
    webview.start(debug=args.debug)
    quit_app()
    return 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
