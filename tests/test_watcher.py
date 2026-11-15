from __future__ import annotations

from pathlib import Path

from systemautobot.watcher import parse_events_xml

FIXTURES = Path(__file__).parent / "fixtures" / "events"


def test_parses_wevtutil_output_without_root():
    sigs = parse_events_xml((FIXTURES / "app_error_1000.xml").read_text(encoding="utf-8"))
    assert len(sigs) == 1
    s = sigs[0]
    assert s.source == "event" and s.name == "Application/Application Error/1000"
    assert s.data["fields"]["AppName"] == "crashy.exe"
    assert s.data["level"] == 2


def test_event_text_is_trimmed():
    sigs = parse_events_xml((FIXTURES / "prompt_injection.xml").read_text(encoding="utf-8"))
    assert len(sigs[0].text) <= 500
