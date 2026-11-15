"""Approval and recording rules every fix must pass, whoever proposed it."""

from __future__ import annotations

from datetime import timedelta

import pytest

from systemautobot.models import (
    ApprovedVia, Card, FixOption, FixStep, Outcome, Problem, Severity, StepResult, StepStatus, Verification,
)
from systemautobot.tools.fixes import Runner


class Spy:
    def __init__(self, fail_at=None, raise_at=None):
        self.ran = []
        self.fail_at, self.raise_at = fail_at, raise_at

    def steps(self, problem):
        def mk(i):
            def run():
                self.ran.append(i)
                if i == self.raise_at:
                    raise RuntimeError("boom")
                if i == self.fail_at:
                    return StepResult(StepStatus.COULDNT, f"step {i} couldn't")
                return StepResult(StepStatus.DONE, f"step {i} done.")
            return FixStep(f"tool{i}", {}, run)
        return [mk(0), mk(1), mk(2)]


def _card(runner, spy, **kw):
    p = Problem("disk_space", "disk_space:C:", "full", {"free": 1}, severity=Severity.MAJOR)
    return runner.register(Card(p, FixOption("Fix", "does it", spy.steps), [], **kw))


def _go(runner, card, token=None):
    return runner.execute(card.id, token or runner.token, ApprovedVia.FIX_NOW,
                          refresh=lambda p: p, verify=lambda p, r: (Verification.FIXED, "fixed"))


@pytest.fixture
def runner(history, clock):
    return Runner(history, paused=False, clock=clock)


def test_runs_records_and_verifies(runner, history):
    spy = Spy()
    res = _go(runner, _card(runner, spy))
    assert res.outcome is Outcome.DONE and res.verification is Verification.FIXED
    assert spy.ran == [0, 1, 2]
    rec = history.get(res.fix_id)
    assert [s["tool"] for s in rec["steps"]] == ["tool0", "tool1", "tool2"]
    assert rec["summary"] == "step 0 done. step 1 done. step 2 done."


@pytest.mark.parametrize("token", ["", "forged", None])
def test_refuses_without_the_launch_token(runner, history, token):
    spy = Spy()
    card = _card(runner, spy)
    res = runner.execute(card.id, token, ApprovedVia.FIX_NOW, refresh=lambda p: p,
                         verify=lambda p, r: (Verification.FIXED, ""))
    assert res.outcome is Outcome.REFUSED and spy.ran == []
    assert history.refusals()[-1]["card_id"] == card.id


def test_refuses_unknown_card(runner, history):
    res = runner.execute("nope", runner.token, ApprovedVia.FIX_NOW, refresh=lambda p: p,
                         verify=lambda p, r: (Verification.FIXED, ""))
    assert res.outcome is Outcome.REFUSED


def test_refuses_reused_card(runner):
    spy = Spy()
    card = _card(runner, spy)
    _go(runner, card)
    assert _go(runner, card).outcome is Outcome.REFUSED
    assert spy.ran == [0, 1, 2]


def test_refuses_expired_card(runner, clock):
    spy = Spy()
    card = _card(runner, spy)
    card.created_at = clock() - timedelta(minutes=16)
    assert _go(runner, card).outcome is Outcome.REFUSED and spy.ran == []


def test_refuses_when_paused(history, clock):
    r = Runner(history, paused=True, clock=clock)
    spy = Spy()
    assert _go(r, _card(r, spy)).outcome is Outcome.REFUSED and spy.ran == []


def test_refuses_card_with_no_repair(runner):
    p = Problem("disk_space", "disk_space:C:", "full", severity=Severity.MAJOR)
    card = runner.register(Card(p, None, []))
    assert _go(runner, card).outcome is Outcome.REFUSED


def test_two_tries_a_day_then_manual(runner):
    for _ in range(2):
        assert _go(runner, _card(runner, Spy())).outcome is Outcome.DONE
    assert _go(runner, _card(runner, Spy())).outcome is Outcome.REFUSED


def test_went_away_changes_nothing(runner, history):
    spy = Spy()
    card = _card(runner, spy)
    res = runner.execute(card.id, runner.token, ApprovedVia.FIX_NOW, refresh=lambda p: None,
                         verify=lambda p, r: (Verification.FIXED, ""))
    assert res.outcome is Outcome.WENT_AWAY and spy.ran == []
    assert history.get(res.fix_id)["fixed_by"] == "nobody"


def test_couldnt_step_stops_the_rest(runner, history):
    spy = Spy(fail_at=1)
    res = _go(runner, _card(runner, spy))
    assert res.outcome is Outcome.COULDNT and spy.ran == [0, 1]
    assert [s["status"] for s in history.get(res.fix_id)["steps"]] == ["done", "couldnt"]


def test_exception_stops_and_keeps_finished_steps(runner, history):
    spy = Spy(raise_at=1)
    res = _go(runner, _card(runner, spy))
    rec = history.get(res.fix_id)
    assert res.outcome is Outcome.COULDNT and spy.ran == [0, 1]
    assert rec["traceback_ref"].startswith("traceback:")
    assert rec["steps"][0]["status"] == "done" and rec["steps"][1]["status"] == "couldnt"


def test_restart_needed_is_not_verified_yet(runner, history):
    p = Problem("network", "network:internet", "x", severity=Severity.MAJOR)
    step = FixStep("admin:winsock_reset", {}, lambda: StepResult(StepStatus.DONE, "Reset.", needs_restart=True))
    card = runner.register(Card(p, FixOption("Reset", "x", lambda p: [step]), []))
    res = _go(runner, card)
    assert res.verification is Verification.WAITING_RESTART
