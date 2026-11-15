"""Critical problems are announced with a Windows notification; a click opens the small Allow box."""

from __future__ import annotations

import sys
import threading
from types import SimpleNamespace

import pytest

from systemautobot.ui.desktop import Desktop


def card(card_id="c1", title="python is not responding.", fix="Close and reopen python"):
    return SimpleNamespace(id=card_id, problem=SimpleNamespace(title=title),
                           option=SimpleNamespace(title=fix) if fix else None)


@pytest.fixture
def desk():
    d = Desktop("http://127.0.0.1:1", "token")
    d.opened, d.notes = [], []
    d._popup = lambda key, ids, title: d.opened.append((key, ids))  # no real window in tests
    d.notifier = lambda title, body: d.notes.append((title, body)) or True
    return d


def test_a_critical_problem_becomes_a_notification_not_a_window(desk):
    desk.show_popup(card())
    assert desk.notes == [("python is not responding.", "Fix ready: Close and reopen python. Click to allow it or not.")]
    assert desk.opened == []  # nothing opens until the person clicks
    desk.show_popup(card())   # the same problem again: one notification is enough
    assert len(desk.notes) == 1


def test_clicking_the_notification_opens_the_small_box(desk):
    desk.show_popup(card())
    assert desk.open_pending() is True and desk.opened == [("c1", ["c1"])]
    assert desk.open_pending() is False  # nothing left: the app opens its main window instead


def test_a_problem_with_no_fix_says_so(desk):
    desk.show_popup(card(title="Your disk may be failing. Back up your files now.", fix=None))
    assert desk.notes[0][1] == "There's no automatic fix. Click to see what to do."


def test_a_problem_fixed_meanwhile_does_not_open_an_empty_box(desk):
    desk.show_popup(card())
    desk.is_open = lambda card_id: False  # fixed from Home, or it went away
    assert desk.open_pending() is False and desk.opened == []


def test_a_problem_that_went_away_forgets_its_notification(desk):
    c = card()
    desk.show_popup(c)
    desk.closed(c, "went_away")
    assert desk.open_pending() is False and desk.opened == []


def test_without_notifications_the_box_opens_directly(desk):
    desk.notifier = lambda title, body: False  # the tray isn't up, or Windows refused
    desk.show_popup(card())
    assert desk.opened == [("c1", ["c1"])]


def test_several_at_once_are_one_notification(desk):
    desk.show_grouped([card("a"), card("b"), card("c")])
    assert desk.notes == [("3 more things need you", "Click to see them and choose what to fix.")]
    desk.open_pending()
    assert desk.opened == [("group-a", ["a", "b", "c"])]


@pytest.mark.skipif(sys.platform != "win32", reason="the tray icon is Windows-only")
def test_the_tray_hears_a_click_on_its_notification():
    pytest.importorskip("pystray")
    from pystray._util import win32

    from systemautobot.ui.tray import NIN_BALLOONUSERCLICK, Tray, _short

    clicked, opened = threading.Event(), []
    tray = Tray(open_main=lambda: opened.append("main"), check_now=lambda: None, is_paused=lambda: False,
                set_paused=lambda v: None, digest_now=None, quit_app=lambda: None, show_waiting=clicked.set)
    handler = tray.icon._message_handlers[win32.WM_NOTIFY]
    handler(0, win32.WM_LBUTTONUP)  # anything else passes through: a left click still opens the app
    assert opened == ["main"] and not clicked.is_set()
    handler(0, NIN_BALLOONUSERCLICK)
    assert clicked.wait(2) and opened == ["main"]
    assert tray.notify("title", "body") is False  # the icon isn't on screen in a test: the caller falls back
    assert _short("a" * 300, 60).endswith("…") and len(_short("a" * 300, 60)) == 60
