"""The always-on watcher: cheap local checks on a timer, plus the Windows event log.
It only produces Signals. It never judges or fixes anything, and it costs no API calls."""

from __future__ import annotations

import logging
import re
import threading
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from .models import Signal, utcnow
from .settings import Settings
from .tools import checks as real_checks

log = logging.getLogger("systemautobot.watcher")
Emit = Callable[[Signal], None]

NS = {"e": "http://schemas.microsoft.com/win/2004/08/events/event"}
EVENT_QUERY = "*[System[(Level=1 or Level=2)]]"  # critical and error
DEFAULT_CHANNELS = ("Application", "System")


class Watcher:
    def __init__(self, emit: Emit, settings: Settings, checks: Any = real_checks,
                 drives: Iterable[str] = ("C:\\",), clock: Callable[[], datetime] = utcnow):
        self.emit = emit
        self.settings = settings
        self.checks = checks
        self.drives = list(drives)
        self.clock = clock
        self._hung_since: dict[int, datetime] = {}
        self._hung_reported: dict[int, dict] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._subscriber: EventSubscriber | None = None

    # ---- polling ----------------------------------------------------------

    def poll_once(self) -> None:
        for drive in self.drives:
            self._safe("disk_space", lambda: self.emit(Signal("check", "disk_space", self.checks.disk_space(drive))))
        self._safe("network", lambda: self.emit(Signal("check", "network", self.checks.network_probe())))
        self._safe("hung_windows", self._poll_hung)

    def _poll_hung(self) -> None:
        now = self.clock()
        current = {w["pid"]: w for w in self.checks.hung_windows()}
        for pid, w in current.items():
            since = self._hung_since.setdefault(pid, now)
            if pid not in self._hung_reported and now - since >= self.settings.hung_after:
                self._hung_reported[pid] = w
                self.emit(Signal("check", "hung_window",
                                 dict(w, hung=True, hung_seconds=int((now - since).total_seconds()))))
        for pid in list(self._hung_since):
            if pid not in current:
                del self._hung_since[pid]
                w = self._hung_reported.pop(pid, None)
                if w is not None:  # it recovered: tell triage so a waiting popup can close
                    self.emit(Signal("check", "hung_window", dict(w, hung=False)))

    def _safe(self, name: str, fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception:
            log.exception("check %s failed", name)

    # ---- lifecycle --------------------------------------------------------

    def start(self, subscribe_events: bool = True) -> None:
        if subscribe_events:
            try:
                self._subscriber = EventSubscriber(self.emit)
                self._subscriber.start()
            except Exception:
                log.exception("event log subscription failed; polling only")
        self._thread = threading.Thread(target=self._loop, name="watcher", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self.settings.poll_interval.total_seconds())

    def stop(self) -> None:
        self._stop.set()
        if self._subscriber:
            self._subscriber.stop()


# ---- Windows event log -----------------------------------------------------------

class EventSubscriber:
    """Push subscription to new error events (EvtSubscribe). No polling, no admin."""

    def __init__(self, emit: Emit, channels: Iterable[str] = DEFAULT_CHANNELS, query: str = EVENT_QUERY):
        self.emit = emit
        self.channels = list(channels)
        self.query = query
        self._handles: list[Any] = []

    def start(self) -> None:
        import win32evtlog

        for channel in self.channels:
            try:
                h = win32evtlog.EvtSubscribe(channel, win32evtlog.EvtSubscribeToFutureEvents,
                                             Query=self.query, Callback=self._on_event, Context=channel)
                self._handles.append(h)
            except Exception:
                log.exception("cannot subscribe to %s", channel)

    def _on_event(self, action, context, event) -> int:
        import win32evtlog

        if action != win32evtlog.EvtSubscribeActionDeliver:
            return 0
        try:
            xml = win32evtlog.EvtRender(event, win32evtlog.EvtRenderEventXml)
            message = _format_message(event, xml)
            for sig in parse_events_xml(xml, message=message):
                self.emit(sig)
        except Exception:
            log.exception("could not read event")
        return 0

    def stop(self) -> None:
        self._handles.clear()  # handles close when released


def _format_message(event, xml: str) -> str:
    import win32evtlog

    m = re.search(r"Provider Name=['\"]([^'\"]+)", xml)
    if not m:
        return ""
    try:
        meta = win32evtlog.EvtOpenPublisherMetadata(m.group(1))
        return win32evtlog.EvtFormatMessage(meta, event, win32evtlog.EvtFormatMessageEvent) or ""
    except Exception:
        return ""


def parse_events_xml(text: str, message: str = "") -> list[Signal]:
    """Parse one <Event> or many (wevtutil output has no root element)."""
    text = text.strip().lstrip("\ufeff")
    if not text.startswith("<Events"):
        text = f"<Events>{text}</Events>"
    root = ET.fromstring(text)
    signals = []
    for ev in root.iter(f"{{{NS['e']}}}Event"):
        sys_el = ev.find("e:System", NS)
        if sys_el is None:
            continue
        provider = sys_el.find("e:Provider", NS)
        provider_name = provider.get("Name", "") if provider is not None else ""
        event_id = int((sys_el.findtext("e:EventID", "0", NS) or "0").strip())
        level = int((sys_el.findtext("e:Level", "0", NS) or "0").strip())
        channel = sys_el.findtext("e:Channel", "", NS) or ""
        created = sys_el.find("e:TimeCreated", NS)
        fields: dict[str, str] = {}
        for i, d in enumerate(ev.findall("e:EventData/e:Data", NS)):
            fields[d.get("Name") or str(i)] = (d.text or "")[:300]
        text_msg = message or ev.findtext("e:RenderingInfo/e:Message", "", NS) or ""
        seen = utcnow()
        if created is not None and created.get("SystemTime"):
            try:
                seen = datetime.fromisoformat(created.get("SystemTime").replace("Z", "+00:00")[:32])
            except ValueError:
                pass
        signals.append(Signal(
            source="event",
            name=f"{channel}/{provider_name}/{event_id}",
            data={"channel": channel, "provider": provider_name, "event_id": event_id,
                  "level": level, "fields": fields},
            text=text_msg[:500],
            seen_at=seen,
        ))
    return signals


def replay(path: str | Path, emit: Emit) -> int:
    """Feed recorded events (one file, or every .xml in a folder) into the pipeline."""
    p = Path(path)
    files = sorted(p.glob("*.xml")) if p.is_dir() else [p]
    n = 0
    for f in files:
        for sig in parse_events_xml(f.read_text(encoding="utf-8-sig")):
            emit(sig)
            n += 1
    return n
