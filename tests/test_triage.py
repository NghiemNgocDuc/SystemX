from __future__ import annotations

import pytest

from conftest import GB
from systemautobot.models import Problem, Route, Severity
from systemautobot.playbooks import ALL
from systemautobot.triage import RULES, Triage


def disk(free_gb, total_gb=500):
    free = int(free_gb * GB)
    return Problem("disk_space", "disk_space:C:", "low",
                   {"drive": "C:", "free": free, "total": total_gb * GB,
                    "percent_free": round(100 * free / (total_gb * GB), 1)})


def test_every_playbook_has_a_severity_rule():
    for cls in ALL:
        assert cls.id in RULES


@pytest.mark.parametrize("free_gb,total_gb,expected", [
    (1.5, 500, Severity.MAJOR),   # under 2 GB
    (15, 500, Severity.MAJOR),    # 3% and below the 20 GB warning
    (5, 60, Severity.MINOR),      # 8.3%: below the warning, not under 5%
    (32, 484, None),              # 6.6% of a big drive, above the warning: healthy
    (24, 500, None),              # 4.8% but above the warning: not a problem
])
def test_disk_rule(settings, free_gb, total_gb, expected):
    assert Triage(settings).assess(disk(free_gb, total_gb)) is expected


def test_major_pops_minor_waits(settings, clock):
    t = Triage(settings, clock=clock)
    p = disk(1)
    t.assess(p)
    assert t.route(p) is Route.POPUP
    m = disk(10, 100)  # 10%: minor
    m.fingerprint = "disk_space:D:"
    t.assess(m)
    assert t.route(m) is Route.DIGEST


def test_duplicates_are_merged(settings, clock):
    t = Triage(settings, clock=clock)
    a, b = disk(1), disk(0.9)
    t.assess(a), t.assess(b)
    assert t.route(a) is Route.POPUP
    assert t.route(b) is Route.DUPLICATE
    assert t.waiting["disk_space:C:"].evidence["free"] == b.evidence["free"]


def test_minor_that_becomes_major_routes_again(settings, clock):
    t = Triage(settings, clock=clock)
    minor = disk(15, 200)  # 7.5%
    t.assess(minor)
    assert t.route(minor) is Route.DIGEST
    major = disk(1, 200)
    t.assess(major)
    assert t.route(major) is Route.POPUP


def test_popup_limit_groups_the_rest(settings, clock):
    t = Triage(settings, clock=clock)
    routes = []
    for i in range(5):
        p = disk(1)
        p.fingerprint = f"disk_space:{i}"
        t.assess(p)
        routes.append(t.route(p))
    assert routes == [Route.POPUP] * 3 + [Route.GROUPED] * 2
    clock.advance(hours=1, minutes=1)
    p = disk(1)
    p.fingerprint = "disk_space:later"
    t.assess(p)
    assert t.route(p) is Route.POPUP


def test_not_now_quiets_for_4_hours_unless_worse(settings, clock):
    t = Triage(settings, clock=clock)
    p = disk(1.8)
    t.assess(p)
    t.route(p)
    t.not_now(p)
    same = disk(1.8)
    t.assess(same)
    assert t.route(same) is Route.SNOOZED
    worse = disk(1.0)  # 44% less free
    t.assess(worse)
    assert t.route(worse) is Route.POPUP


def test_not_now_expires(settings, clock):
    t = Triage(settings, clock=clock)
    p = disk(1.8)
    t.assess(p), t.route(p), t.not_now(p)
    clock.advance(hours=4, minutes=1)
    again = disk(1.8)
    t.assess(again)
    assert t.route(again) is Route.POPUP


def test_never_for_this(settings, clock):
    t = Triage(settings, clock=clock)
    t.never_for("disk_space:C:")
    p = disk(1)
    t.assess(p)
    assert t.route(p) is Route.SNOOZED


def test_grok_can_raise_but_never_lower(settings):
    t = Triage(settings)
    p = Problem("unknown", "unknown:x", "x")
    t.assess(p)
    assert p.severity is Severity.MINOR
    t.raise_severity(p, Severity.MAJOR)
    assert p.severity is Severity.MAJOR
    t.raise_severity(p, Severity.MINOR)
    assert p.severity is Severity.MAJOR


def test_recurring_problem_is_flagged(settings, clock):
    t = Triage(settings, count_fixed=lambda fp, w: 3, clock=clock)
    p = disk(1)
    t.assess(p)
    t.route(p)
    assert p.recurring
