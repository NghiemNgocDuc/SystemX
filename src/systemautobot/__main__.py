"""Command line for running the pipeline before the UI exists.

  python -m systemautobot check                 run every check once and print the results
  python -m systemautobot watch [--demo]        watch and print what would pop up (fixes paused)
  python -m systemautobot watch --allow-fixes   also ask "Fix now?" in this console
  python -m systemautobot replay <file|folder>  feed recorded events through the pipeline
  python -m systemautobot history [--id ID]     show the fix history
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import queue
import sys
from datetime import time as dtime

from .history import History
from .models import ApprovedVia, Card
from .paths import AppPaths
from .safety.redact import redact
from .settings import Settings
from .tools import checks


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


def setup_logging(paths: AppPaths) -> None:
    handler = logging.handlers.RotatingFileHandler(paths.app_log, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(_RedactFilter())
    root = logging.getLogger("systemautobot")
    root.setLevel(logging.INFO)
    root.addHandler(handler)


class ConsolePresenter:
    """Stands in for the tray popups. Cards go on a queue the main thread answers."""

    def __init__(self):
        self.q: queue.Queue = queue.Queue()

    def show_popup(self, card: Card) -> None:
        self.q.put(("popup", card))

    def show_grouped(self, cards: list[Card]) -> None:
        print(f"\n[Needs you] {len(cards)} more things need you.")
        for c in cards:
            self.q.put(("popup", c))

    def show_digest(self, items, went_away) -> None:
        print(f"\n=== End of day: {len(items)} things to look at ===")
        for i in went_away:
            print(f"  (went away on its own) {i.problem.title}")
        for i in items:
            self.q.put(("digest", i.card))

    def closed(self, card: Card, reason: str) -> None:
        print(f"\n[Closed] {card.problem.title} {reason}")


def _describe(card: Card) -> None:
    p = card.problem
    print(f"\n[{p.severity.value.upper()}] {p.title}")
    if card.option:
        print(f"  Fix: {card.option.title}. {card.option.explain}")
        for w in card.option.warnings:
            print(f"  Warning: {w}")
        if not card.option.undo_possible:
            print(f"  {card.option.no_undo_text}")
    else:
        print("  I can't fix this one myself.")
    for i, s in enumerate(card.manual_steps, 1):
        print(f"  {i}. {s.text}" + (f"  [{s.open_target}]" if s.open_target else ""))


def cmd_check(_args) -> int:
    out = {
        "disk_space": checks.disk_space(),
        "network": checks.network_probe(),
        "hung_windows": checks.hung_windows(),
        "is_admin": checks.is_admin(),
        "popups_should_wait": checks.popups_should_wait(),
    }
    print(json.dumps(out, indent=2, default=str))
    return 0


def cmd_watch(args) -> int:
    from .pipeline import build_pipeline
    from .watcher import Watcher

    paths = AppPaths.default().ensure()
    setup_logging(paths)
    settings = Settings.demo_mode() if args.demo else Settings()
    settings.fixes_paused = not args.allow_fixes
    if args.end_of_day:
        h, m = map(int, args.end_of_day.split(":"))
        settings.end_of_day = dtime(h, m)
    presenter = ConsolePresenter()
    history = History(paths)
    pipe = build_pipeline(settings, history, presenter)
    interrupted = pipe.recover_interrupted()
    if interrupted:
        print(f"{len(interrupted)} repair(s) were interrupted last time. See: history")

    watcher = Watcher(pipe.handle, settings)
    watcher.start(subscribe_events=not args.no_events)
    print(f"Watching every {int(settings.poll_interval.total_seconds())} s. "
          f"Fixes are {'ON' if args.allow_fixes else 'paused'}. Ctrl+C to stop.")
    try:
        while True:
            pipe.tick()
            try:
                kind, card = presenter.q.get(timeout=1)
            except queue.Empty:
                continue
            _describe(card)
            if not args.allow_fixes or card.option is None:
                continue
            answer = input("  Fix now? [y = fix / n = not now / m = I'll do it myself] ").strip().lower()
            via = ApprovedVia.FIX_NOW if kind == "popup" else ApprovedVia.FIX_IT_FOR_ME
            if answer == "y":
                res = pipe.approve(card.id, pipe.runner.token, via)
                print(f"  {res.outcome.value}: {res.message}")
            elif answer == "m":
                input("  Follow the steps above, then press Enter to check again. ")
                print(f"  {pipe.manual_done(card.id).value}")
            else:
                pipe.not_now(card.id)
                print("  Okay. I'll ask again at the end of the day.")
    except KeyboardInterrupt:
        watcher.stop()
        print("\nStopped.")
    return 0


def cmd_replay(args) -> int:
    from .pipeline import build_pipeline
    from .watcher import replay

    paths = AppPaths.default().ensure()
    history = History(paths)
    settings = Settings()
    pipe = build_pipeline(settings, history)
    routes: dict[str, int] = {}

    def emit(sig):
        r = pipe.handle(sig)
        routes[r.value] = routes.get(r.value, 0) + 1
        print(f"{r.value:9} {sig.name}")

    n = replay(args.path, emit)
    print(f"\n{n} events: {routes}")
    return 0


def cmd_history(args) -> int:
    history = History(AppPaths.default().ensure())
    if args.id:
        print(json.dumps(history.get(args.id), indent=2, default=str))
        return 0
    for r in history.recent(args.limit):
        print(f"{r['started_at'][:19]}  {r['outcome'] or 'open':12} {r['fixed_by']:8} {r['title']}")
        if r["summary"]:
            print(f"    {r['summary']}  (id {r['id']})")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["app"]:  # the desktop app: python -m systemautobot app [--demo] [--browser] ...
        from .app import run

        return run(argv[1:])
    ap = argparse.ArgumentParser(prog="systemautobot")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    w = sub.add_parser("watch")
    w.add_argument("--demo", action="store_true", help="check every 10 s")
    w.add_argument("--allow-fixes", action="store_true", help="ask before fixing in this console")
    w.add_argument("--no-events", action="store_true", help="skip the event log subscription")
    w.add_argument("--end-of-day", help="HH:MM, e.g. 18:00")
    w.set_defaults(fn=cmd_watch)
    r = sub.add_parser("replay")
    r.add_argument("path")
    r.set_defaults(fn=cmd_replay)
    h = sub.add_parser("history")
    h.add_argument("--id")
    h.add_argument("--limit", type=int, default=20)
    h.set_defaults(fn=cmd_history)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
