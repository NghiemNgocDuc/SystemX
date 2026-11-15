"""The local server: token and Origin on every call, lab endpoints only in lab mode."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import GB
from fake_ai import ScriptedTransport, call, say
from systemautobot.digest import Digest
from systemautobot.grok.service import AssistantService
from systemautobot.pipeline import Pipeline
from systemautobot.playbooks import Toolbox, build_all
from systemautobot.settings import SettingsStore
from systemautobot.tools.fixes import AdminLauncher, Runner
from systemautobot.triage import Triage
from systemautobot.watcher import Watcher
from systemautobot.web.server import AppContext, create_app

ORIGIN = "http://127.0.0.1:9999"


@pytest.fixture
def env(history, settings, fake_checks, sandbox, tmp_path, monkeypatch):
    guard, temp, _ = sandbox
    tools = Toolbox(guard=guard, launcher=AdminLauncher(results_dir=tmp_path / "runs"), settings=settings,
                    checks=fake_checks, temp_roots=[temp])
    pipe = Pipeline(build_all(tools), Triage(settings), Digest(settings), Runner(history, paused=False), history)
    scripts: list = []
    svc = AssistantService(pipe, tools, settings, key_loader=lambda p: "xai-" + "k" * 30,
                           transport_factory=lambda p, k, b: ScriptedTransport(scripts))
    store = SettingsStore(tmp_path / "settings.json", settings)
    opened: list[str] = []
    saved_keys: dict = {}
    monkeypatch.setattr("systemautobot.grok.keystore.set_key", lambda p, k: saved_keys.__setitem__(p, k))
    monkeypatch.setattr("systemautobot.grok.keystore.get_key", lambda p: saved_keys.get(p))
    ctx = AppContext(pipe, history, svc, store, origin=ORIGIN, watcher=Watcher(pipe.handle, settings, checks=fake_checks),
                     open_target=opened.append)
    client = TestClient(create_app(ctx))
    good = {"X-SAB-Token": pipe.runner.token, "Origin": ORIGIN}
    return client, good, ctx, pipe, fake_checks, scripts, opened, saved_keys


def _major(pipe, checks):
    checks.set_free(int(1 * GB))
    Watcher(pipe.handle, pipe.runner and pipe.triage.settings, checks=checks).poll_once()
    return next(iter(pipe.open_cards.values()))


def test_pages_load_without_token(env):
    client, *_ = env
    assert client.get("/").status_code == 200
    assert "SystemAutoBot" in client.get("/").text
    assert client.get("/static/common.js").status_code == 200


def test_demo_panel_can_open_normal_approval_without_running_a_repair(env):
    from unittest.mock import Mock

    client, good, ctx, pipe, checks, *_ = env
    card = _major(pipe, checks)
    ctx.ui = Mock()
    assert client.post("/api/lab/show_card", json={"card_id": card.id}).status_code == 404
    ctx.lab_token = "demo-token"
    assert client.post("/api/lab/show_card", json={"card_id": card.id}).status_code == 403
    headers = {"X-SAB-Lab-Token": ctx.lab_token}
    response = client.post("/api/lab/show_card", json={"card_id": card.id}, headers=headers)
    assert response.status_code == 200
    ctx.ui.show_card.assert_called_once_with(card)
    assert not card.used and ctx.history.recent() == []
    card.used = True
    assert client.post("/api/lab/show_card", json={"card_id": card.id}, headers=headers).status_code == 404


def test_lab_state_includes_readiness_without_changing_settings(env):
    client, good, ctx, *_ = env
    ctx.lab_token = "demo-token"
    ctx.store.settings.demo = True
    response = client.get("/api/lab/state", headers={"X-SAB-Lab-Token": ctx.lab_token})
    assert response.status_code == 200
    assert response.json()["settings"]["demo"] is True
    assert "watcher_health" in response.json()


@pytest.mark.parametrize("headers", [{}, {"X-SAB-Token": "wrong", "Origin": ORIGIN}])
def test_api_needs_the_token(env, headers):
    client, *_ = env
    assert client.get("/api/state", headers=headers).status_code == 403


def test_post_needs_matching_origin(env):
    client, good, ctx, pipe, checks, *_ = env
    card = _major(pipe, checks)
    for origin in (None, "http://evil.example", "http://127.0.0.1:1"):
        h = {"X-SAB-Token": good["X-SAB-Token"]}
        if origin:
            h["Origin"] = origin
        assert client.post(f"/api/cards/{card.id}/approve", json={"via": "fix_now"}, headers=h).status_code == 403
    assert not card.used


def test_approve_runs_the_fix_and_records_it(env):
    client, good, ctx, pipe, checks, *_ = env
    card = _major(pipe, checks)
    state = client.get("/api/state", headers=good).json()
    assert state["status"] == "Needs you" and state["cards"][0]["id"] == card.id
    r = client.post(f"/api/cards/{card.id}/approve", json={"via": "fix_now"}, headers=good).json()
    assert r["outcome"] == "done" and r["fix_id"]
    h = client.get(f"/api/history/{r['fix_id']}", headers=good).json()
    assert h["approved_via"] == "fix_now" and h["steps"]
    rep = r["report"]  # the card shows what ran, what changed, and the re-check right away
    assert rep["fix_id"] == r["fix_id"] and rep["steps"][0]["status"] == "done"
    assert rep["verification"] == h["verifications"][0]["result"] and rep["verdict"]
    assert client.get(f"/api/cards/{card.id}", headers=good).json()["result"]["report"]["fix_id"] == r["fix_id"]


def test_unknown_button_is_refused(env):
    client, good, ctx, pipe, checks, *_ = env
    card = _major(pipe, checks)
    assert client.post(f"/api/cards/{card.id}/approve", json={"via": "lab"}, headers=good).status_code == 400


def test_open_only_allows_listed_pages(env):
    client, good, ctx, pipe, checks, scripts, opened, _ = env
    assert client.post("/api/open", json={"target": "ms-settings:storagesense"}, headers=good).status_code == 200
    assert client.post("/api/open", json={"target": "cmd.exe"}, headers=good).status_code == 400
    assert opened == ["ms-settings:storagesense"]


def test_lab_endpoints_hidden_without_lab_mode(env):
    client, good, *_ = env
    assert client.get("/api/lab/state", headers={"X-SAB-Lab-Token": "x"}).status_code == 404


def test_lab_endpoints_need_lab_token(env):
    client, good, ctx, *_ = env
    ctx.lab_token = "labtoken"
    assert client.get("/api/lab/state", headers={"X-SAB-Lab-Token": "nope"}).status_code == 403
    assert client.get("/api/lab/state", headers={"X-SAB-Lab-Token": "labtoken"}).status_code == 200


def test_setup_tests_then_saves_provider_key_and_model(env):
    client, good, ctx, pipe, checks, scripts, opened, saved = env
    scripts += [[say("OK")]]
    r = client.post("/api/setup", json={"provider": "anthropic", "model": "claude-sonnet-5-5",
                                        "key": "sk-ant-" + "x" * 30}, headers=good).json()
    assert r["ok"] and saved["anthropic"].startswith("sk-ant-")
    s = client.get("/api/state", headers=good).json()["settings"]
    assert s["provider"] == "anthropic" and s["model"] == "claude-sonnet-5-5" and s["setup_done"]
    assert "sk-ant" not in (ctx.store.path.read_text())


def test_setup_failure_does_not_save(env):
    client, good, ctx, pipe, checks, scripts, opened, saved = env
    from fake_ai import unavailable

    scripts += [unavailable("bad_key")]
    r = client.post("/api/setup", json={"provider": "xai", "model": "grok-4.7", "key": "xai-bad"}, headers=good).json()
    assert not r["ok"] and "xai" not in saved


def test_custom_provider_needs_https(env):
    client, good, *_ = env
    r = client.post("/api/setup", json={"provider": "custom", "model": "m", "key": "k",
                                        "base_url": "http://evil.example/v1"}, headers=good)
    assert r.status_code == 400


def test_chat_returns_reply_and_card(env):
    client, good, ctx, pipe, checks, scripts, *_ = env
    scripts += [[call("propose_fix", {"entry": "empty_recycle_bin", "reason": "Bin is huge."})],
                [say("I can empty the Recycle Bin.")]]
    r = client.post("/api/chat", json={"text": "free up space"}, headers=good).json()
    assert r["reply"] == "I can empty the Recycle Bin." and r["card"]["fix"]["title"] == "Empty the Recycle Bin"


def test_settings_validate_and_persist(env):
    client, good, ctx, *_ = env
    assert client.post("/api/settings", json={"warn_free_gb": 33}, headers=good).status_code == 400
    s = client.post("/api/settings", json={"warn_free_gb": 40, "end_of_day": "17:30", "fixes_paused": True},
                    headers=good).json()
    assert s["warn_free_gb"] == 40 and s["end_of_day"] == "17:30" and ctx.pipeline.runner.paused
    assert '"17:30"' in ctx.store.path.read_text()


def test_history_export_csv(env):
    client, good, ctx, pipe, checks, *_ = env
    card = _major(pipe, checks)
    client.post(f"/api/cards/{card.id}/approve", json={"via": "fix_now"}, headers=good)
    r = client.get("/api/history/export?fmt=csv", headers=good)
    assert r.status_code == 200 and r.text.startswith("started_at,")


def test_choosing_which_problems_to_handle(env):
    client, good, ctx, *_ = env
    assert client.post("/api/settings", json={"off": ["format_drive"]}, headers=good).status_code == 400
    s = client.post("/api/settings", json={"off": ["print_queue", "unknown"]}, headers=good).json()
    assert s["off"] == ["print_queue", "unknown"]
    assert {k["id"] for k in s["problem_kinds"]} >= {"disk_space", "network", "unknown"}
    assert '"print_queue"' in ctx.store.path.read_text()


def test_glance_tiles_and_skip_setup(env):
    client, good, ctx, pipe, checks, *_ = env
    _major(pipe, checks)  # 1 GB free reading
    g = client.get("/api/glance", headers=good).json()
    tiles = {t["kind"]: t for t in g["tiles"]}
    assert tiles["disk_space"]["state"] == "bad" and tiles["disk_space"]["fill"] is not None
    assert tiles["network"]["state"] == "ok"  # the watcher poll recorded a healthy reading
    client.post("/api/settings", json={"off": ["frozen_app"]}, headers=good)
    g = client.get("/api/glance", headers=good).json()
    assert {t["kind"]: t for t in g["tiles"]}["frozen_app"]["state"] == "off"
    assert g["watching"] == 7  # 8 kinds, one switched off
    assert client.post("/api/setup/skip", json={}, headers=good).json()["ok"]
    assert client.get("/api/state", headers=good).json()["needs_setup"] is False


def test_check_again_reports_in_plain_words(env):
    client, good, ctx, pipe, checks, *_ = env
    card = _major(pipe, checks)
    r = client.post(f"/api/cards/{card.id}/check_again", json={}, headers=good).json()
    assert r["verification"] == "not_fixed" and r["message"].startswith("Still happening")
    checks.set_free(int(200 * GB))  # the person freed space themselves
    r = client.post(f"/api/cards/{card.id}/check_again", json={}, headers=good).json()
    assert r["verification"] == "fixed" and "fixed" in r["message"]
    assert client.get("/api/state", headers=good).json()["cards"] == []
    assert client.post("/api/cards/nope/check_again", json={}, headers=good).status_code == 404
