"""The local server behind every window. Binds to 127.0.0.1 only.

Every /api call needs the per-launch token (X-SAB-Token). The token reaches the app's own
windows in the URL fragment, which is never sent over HTTP. Every POST must also carry an
Origin matching this server. Lab endpoints exist only with --demo --lab and use a separate token.
"""

from __future__ import annotations

import csv
import hmac
import io
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import time as dtime
from pathlib import Path
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from ..grok import keystore, providers
from ..grok.tools import ALLOWED_PAGES
from ..models import ApprovedVia

log = logging.getLogger("systemautobot.web")
STATIC = Path(__file__).parent / "static"


@dataclass
class AppContext:
    pipeline: Any
    history: Any
    service: Any
    store: Any
    lab_token: str | None = None
    origin: str = ""                                   # set once the port is known
    watcher: Any = None
    ui: Any = None                                     # Desktop: popups, digest, main window
    open_target: Callable[[str], None] = field(default=lambda t: os.startfile(t))  # noqa: E731
    version: str = ""
    opened: dict[str, list[str]] = field(default_factory=dict)

    @property
    def token(self) -> str:
        return self.pipeline.runner.token


def create_app(ctx: AppContext) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    # ---- guards ------------------------------------------------------------------

    def _origin_ok(request: Request) -> bool:
        if request.method in ("GET", "HEAD"):
            return True
        return bool(ctx.origin) and request.headers.get("origin") == ctx.origin

    def ui_auth(request: Request) -> None:
        sent = request.headers.get("x-sab-token", "")
        if not hmac.compare_digest(sent, ctx.token):
            raise HTTPException(403, "This request didn't come from SystemAutoBot.")
        if not _origin_ok(request):
            raise HTTPException(403, "Wrong origin.")

    def lab_auth(request: Request) -> None:
        if not ctx.lab_token:
            raise HTTPException(404)
        if not hmac.compare_digest(request.headers.get("x-sab-lab-token", ""), ctx.lab_token):
            raise HTTPException(403, "Wrong lab token.")

    ui = [Depends(ui_auth)]
    from ..grok.think import ThinkManager

    think = ThinkManager(ctx.pipeline, ctx.service) if ctx.service is not None else None

    # ---- pages -------------------------------------------------------------------

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/popup")
    def popup():
        return FileResponse(STATIC / "popup.html")

    @app.get("/digest")
    def digest_page():
        return FileResponse(STATIC / "digest.html")

    @app.get("/api/health")
    def health():
        return {"ok": True}

    # ---- state -------------------------------------------------------------------

    def state() -> dict:
        p = ctx.pipeline
        cards = p.cards_view()
        return {
            "version": ctx.version,
            "ai": ctx.service.status(),
            "settings": ctx.store.public(),
            "needs_setup": not ctx.store.settings.setup_done,
            "paused": p.runner.paused,
            "cards": cards,
            "status": "Needs you" if cards else "Fine",
            "lab": bool(ctx.lab_token),
        }

    @app.get("/api/state", dependencies=ui)
    def get_state():
        return state()

    @app.get("/api/glance", dependencies=ui)
    def glance():
        """Plain-language status tiles: drive, internet, frozen programs, printing."""
        from ..tools import checks as real_checks

        p, s = ctx.pipeline, ctx.store.settings
        checks = getattr(getattr(p, "tools", None), "checks", real_checks)
        gb = 1024 ** 3
        tiles = []

        def tile(kind, label, state, value, detail, fill=None):
            if kind in s.off:
                state, value, detail, fill = "off", "Not watched", "Switched off in Settings", None
            tiles.append({"kind": kind, "label": label, "state": state, "value": value, "detail": detail, "fill": fill})

        disk = p.readings.get("disk_space", (None,))[0]
        try:
            disk = disk or checks.disk_space()
        except Exception:
            disk = None
        if disk:
            free = disk["free"]
            state = "bad" if free < s.major_free_gb * gb else "warn" if free < s.warn_free_gb * gb else "ok"
            pct = disk["percent_free"]
            tile("disk_space", f"Drive {disk['drive']}", state, f"{free / gb:.1f} GB free",
                 f"{pct:.0f}% of {disk['total'] / gb:.0f} GB free" if pct >= 1 else f"Under 1% of {disk['total'] / gb:.0f} GB free",
                 fill=pct)
            health = p.readings.get("disk_health", (None,))[0]
            bad = [d for d in (health or {}).get("disks", []) if d.get("health", "").lower() not in ("", "healthy")]
            if bad and tiles and tiles[-1]["kind"] == "disk_space" and tiles[-1]["state"] != "off":
                tiles[-1].update(state="bad", detail=f"Disk health: {bad[0]['health']}")
        net = p.readings.get("network", (None,))[0]
        if net is None:
            tile("network", "Internet", "wait", "Checking…", "First check runs in a moment")
        elif not net.get("adapter_up"):
            tile("network", "Internet", "warn", "Not connected", "Wi-Fi or cable is off")
        elif not (net.get("dns_ok") and net.get("http_ok")):
            tile("network", "Internet", "bad", "Not working", "Connected, but websites don't load")
        elif abs(net.get("clock_skew_seconds") or 0) > 300:
            tile("network", "Internet", "bad", "Clock is wrong", "Secure websites will fail")
        else:
            tile("network", "Internet", "ok", "Working", "Websites load, clock is right")
        try:
            hung = checks.hung_windows()
        except Exception:
            hung = []
        tile("frozen_app", "Programs", "warn" if hung else "ok",
             f"{len(hung)} not responding" if hung else "All responding",
             ", ".join(sorted({w["name"] for w in hung}))[:60] if hung else "Nothing frozen")
        jobs = []
        if hasattr(checks, "print_jobs"):
            try:
                jobs = checks.print_jobs()
            except Exception:
                jobs = []
        stuck = [j for j in jobs if j.get("error")]
        tile("print_queue", "Printing", "warn" if stuck else "ok",
             f"{len(stuck)} stuck" if stuck else ("Queue empty" if not jobs else f"{len(jobs)} printing"),
             stuck[0]["printer"] if stuck else "No stuck jobs")
        sec = {k.removeprefix("security_"): v[0] for k, v in p.readings.items() if k.startswith("security_")}
        if not sec:
            tile("security", "Security", "wait", "Checking…", "Windows Security and firewall")
        elif sec.get("realtime"):
            tile("security", "Security", "bad", "Protection off", "Real-time protection is off")
        elif sec.get("firewall"):
            tile("security", "Security", "bad", "Firewall off", ", ".join(sec["firewall"].get("profiles_off", [])))
        elif sec.get("signatures"):
            tile("security", "Security", "warn", "Definitions old", f"{sec['signatures'].get('age_days')} days old")
        else:
            tile("security", "Security", "ok", "Protected", "Protection and firewall are on")
        from ..settings import PROBLEM_IDS

        seen = [t for _, t in p.readings.values()]
        return {"tiles": tiles, "last_checked": max(seen).isoformat() if seen else None,
                "watching": len(PROBLEM_IDS - set(s.off))}

    @app.post("/api/cards/{card_id}/explain", dependencies=ui)
    def explain(card_id: str):
        """Ask the AI about this card now, instead of waiting for the end-of-day list."""
        card = ctx.pipeline.runner.cards.get(card_id)
        if card is None or card.used:
            raise HTTPException(404, "That card is gone.")
        ctx.service.diagnose(card, force=True)
        return card.to_dict()

    # ---- think mode: "Fix for me", every step shown, nothing runs without Allow ----------------

    def _session(sid: str):
        s = think.get(sid) if think else None
        if s is None:
            raise HTTPException(404, "That session is over. Press Fix for me again.")
        return s

    @app.post("/api/think/start", dependencies=ui)
    def think_start(body: dict):
        card = ctx.pipeline.runner.cards.get(str(body.get("card_id", "")))
        if card is None or card.used or think is None:
            raise HTTPException(404, "That card is gone. It may have been fixed already.")
        return think.start(card).view()

    @app.get("/api/think/{sid}", dependencies=ui)
    def think_view(sid: str, after: int = 0):
        return _session(sid).view(max(0, after))

    @app.post("/api/think/{sid}/send", dependencies=ui)
    def think_send(sid: str):
        s = _session(sid)
        think.send(s)
        return s.view()

    @app.post("/api/think/{sid}/stop", dependencies=ui)
    def think_stop(sid: str):
        s = _session(sid)
        think.stop(s)
        return s.view()

    @app.get("/api/ai_usage", dependencies=ui)
    def ai_usage():
        return ctx.service.usage()

    @app.get("/api/doctor", dependencies=ui)
    def doctor():
        """Self-check: which parts work on this PC, and which need attention."""
        import sys

        from ..tools import checks as real_checks

        rows = []

        def add(name, state, detail):
            rows.append({"name": name, "state": state, "detail": detail})

        w = ctx.watcher
        n = w.subscribed if w else 0
        add("Windows error events", "ok" if n else "warn",
            f"Listening to {n} event logs" if n else "Not listening; regular checks still run")
        labels = {"disk_space": "Free space check", "network": "Internet and clock check",
                  "hung_windows": "Frozen program check", "print_queue": "Print queue check",
                  "security": "Windows Security check", "disk_health": "Disk health check",
                  "error_dialogs": "Error message watcher"}
        for key, label in labels.items():
            state = (w.health.get(key) if w else None)
            if state is None:
                add(label, "warn", "Hasn't run yet")
            else:
                add(label, "ok" if state == "ok" else "bad", "Working" if state == "ok" else f"Failed ({state})")
        if getattr(sys, "frozen", False):
            helper = Path(sys.executable).with_name("sab-helper.exe")
        else:
            helper = Path(__file__).resolve().parents[2] / "sab_helper" / "helper.py"
        add("Admin helper", "ok" if helper.exists() else "bad",
            "Ready; Windows asks permission each time" if helper.exists() else "Missing: admin repairs won't work")
        admin = real_checks.is_admin()
        add("Runs without admin rights", "warn" if admin else "ok",
            "Running as administrator (it shouldn't)" if admin else "Yes, as it should")
        st = ctx.service.status()
        add("AI", "ok" if st["configured"] else "warn",
            f"{st['label']} · {st['model']}" if st["configured"] else "Not connected; built-in fixes still work")
        add("Fixes", "warn" if ctx.pipeline.runner.paused else "ok",
            "Paused: checking only" if ctx.pipeline.runner.paused else "On; each one still needs your click")
        try:
            ctx.history.recent(1)
            add("Fix history", "ok", "Saving on this PC")
        except Exception as e:
            add("Fix history", "bad", f"Can't open ({type(e).__name__})")
        return {"checks": rows}

    @app.get("/api/report", dependencies=ui)
    def report():
        """A one-page health report to keep or send to whoever helps with this PC."""
        import html as h
        import platform
        from datetime import datetime as dt

        g = glance()
        s = ctx.store.settings
        cards = ctx.pipeline.cards_view()
        fixes = ctx.history.recent(25)
        use = ctx.service.usage()
        e = h.escape
        colors = {"ok": "#e2fcef", "warn": "#fff3c4", "bad": "#ffe4e0", "off": "#f1f0f4", "wait": "#f1f0f4"}
        tiles = "".join(f"<div class='t' style='background:{colors.get(t['state'], '#fff')}'><b>{e(t['label'])}</b>"
                        f"<div class='v'>{e(t['value'])}</div><div class='d'>{e(t['detail'])}</div></div>"
                        for t in g["tiles"])
        needs = "".join(f"<li><b>{e(c['title'])}</b> <span class='m'>({'urgent' if c['severity'] == 'major' else 'small'})</span></li>"
                        for c in cards) or "<li>Nothing needs attention.</li>"
        rows = "".join(f"<tr><td>{e((r['started_at'] or '')[:16].replace('T', ' '))}</td><td>{e(r['summary'] or r['title'])}</td>"
                       f"<td>{e(r['outcome'] or 'in progress')}</td><td>{e(r['fixed_by'])}</td></tr>" for r in fixes) \
            or "<tr><td colspan='4'>No fixes yet.</td></tr>"
        from ..settings import PROBLEM_KINDS

        kinds = "".join(f"<li>{e(k['label'])}: {'off' if k['id'] in s.off else 'watching'}</li>"
                        for k in PROBLEM_KINDS)
        when = dt.now().strftime("%Y-%m-%d %H:%M")
        page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>SystemAutoBot health report</title>
<style>body{{font:15px/1.5 "Segoe UI",system-ui,sans-serif;color:#141218;max-width:860px;margin:24px auto;padding:0 16px}}
h1{{margin:0}}h2{{margin:28px 0 10px;font-size:18px}}.m{{color:#555}}.g{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}}
.t{{border:2px solid #141218;border-radius:10px;padding:10px}}.v{{font-weight:800;font-size:17px}}.d{{font-size:13px;color:#3d3a47}}
table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{border-bottom:1px solid #ddd;padding:6px;text-align:left;vertical-align:top}}</style></head><body>
<h1>Health report</h1><p class="m">SystemAutoBot · {e(platform.platform())} · made {e(when)}</p>
<h2>At a glance</h2><div class="g">{tiles}</div>
<h2>Needs attention</h2><ul>{needs}</ul>
<h2>Recent fixes</h2><table><tr><th>When</th><th>What</th><th>Result</th><th>By</th></tr>{rows}</table>
<h2>What it watches</h2><ul>{kinds}</ul>
<h2>AI use</h2><p>Last 24 hours: {use['day']['calls']} questions, {use['day']['tokens']} tokens. Last 7 days: {use['week']['calls']} questions, {use['week']['tokens']} tokens.</p>
<p class="m">This report has no API keys, passwords, or file contents. Full step-by-step history is in the app under Activity.</p>
</body></html>"""
        from fastapi.responses import HTMLResponse

        return HTMLResponse(page, headers={"Content-Disposition":
                                           f"attachment; filename=SystemAutoBot-report-{dt.now():%Y%m%d-%H%M}.html"})

    @app.post("/api/setup/skip", dependencies=ui)
    def setup_skip():
        """Use the built-in fixes only, for now. The AI can be connected later in Settings."""
        ctx.store.update(setup_done=True)
        return {"ok": True}

    @app.get("/api/cards/{card_id}", dependencies=ui)
    def get_card(card_id: str):
        card = ctx.pipeline.runner.cards.get(card_id)
        if card is None:
            raise HTTPException(404, "That card is gone.")
        return card.to_dict()

    @app.get("/api/digest", dependencies=ui)
    def get_digest():
        p = ctx.pipeline
        return {"items": [dict(i.card.to_dict(), rollovers=i.rollovers) for i in p.last_digest
                          if i.problem.fingerprint in p.digest.items],
                "went_away": [i.problem.title for i in p.last_digest_gone]}

    # ---- the person's answers ------------------------------------------------------

    def _approve(card_id: str, via: ApprovedVia) -> dict:
        res = ctx.pipeline.approve(card_id, ctx.token, via)
        if ctx.ui:
            ctx.ui.refresh()
        return {"outcome": res.outcome.value, "message": res.message, "fix_id": res.fix_id,
                "verification": res.verification.value if res.verification else None,
                "report": ctx.pipeline.fix_report(res.fix_id)}

    @app.post("/api/cards/{card_id}/approve", dependencies=ui)
    def approve(card_id: str, body: dict):
        via = body.get("via", "fix_this")
        if via not in ("fix_now", "fix_it_for_me", "fix_this"):
            raise HTTPException(400, "Unknown button.")
        return _approve(card_id, ApprovedVia(via))

    @app.post("/api/cards/{card_id}/not_now", dependencies=ui)
    def not_now(card_id: str):
        ctx.pipeline.not_now(card_id)
        return {"ok": True}

    @app.post("/api/cards/{card_id}/never", dependencies=ui)
    def never(card_id: str):
        ctx.pipeline.never(card_id)
        ctx.store.save()
        return {"ok": True}

    @app.post("/api/cards/{card_id}/check_again", dependencies=ui)
    def check_again(card_id: str, body: dict | None = None):
        body = body or {}
        try:
            verdict, message = ctx.pipeline.check_again(card_id, ctx.opened.get(card_id, []), body.get("note"),
                                                        sorted_by_person=bool(body.get("sorted")))
        except KeyError:
            raise HTTPException(404, "That card is gone. It may have been fixed already.")
        if ctx.ui:
            ctx.ui.refresh()
        return {"verification": verdict.value, "message": message}

    @app.post("/api/open", dependencies=ui)
    def open_target(body: dict):
        target = body.get("target", "")
        if target not in ALLOWED_PAGES:
            raise HTTPException(400, "I only open Windows Settings pages from my list.")
        ctx.open_target(target)
        if body.get("card_id"):
            ctx.opened.setdefault(body["card_id"], []).append(target)
        return {"ok": True}

    @app.post("/api/open_key_page", dependencies=ui)
    def open_key_page(body: dict):
        p = providers.get(body.get("provider", ""))
        if not p.key_page:
            raise HTTPException(400, "This provider has no key page.")
        ctx.open_target(p.key_page)
        return {"ok": True}

    # ---- chat and checks ----------------------------------------------------------------

    @app.post("/api/chat", dependencies=ui)
    def chat(body: dict):
        text = str(body.get("text", "")).strip()
        if not text:
            raise HTTPException(400, "Type something first.")
        return ctx.service.chat(text[:4000])

    @app.post("/api/check_now", dependencies=ui)
    def check_now():
        if ctx.watcher:
            threading.Thread(target=lambda: ctx.watcher.poll_once(force_slow=True), daemon=True).start()
        return {"ok": True, "message": "Checking now. I'll speak up only if something is wrong."}

    @app.post("/api/digest/now", dependencies=ui)
    def digest_now():
        if not ctx.store.settings.demo:
            raise HTTPException(404)
        ctx.pipeline.show_digest()
        return {"ok": True}

    # ---- setup: provider, key, model ------------------------------------------------------

    @app.get("/api/providers", dependencies=ui)
    def get_providers():
        return {"providers": providers.public_list(),
                "has_key": {p: bool(keystore.get_key(p)) for p in providers.PROVIDERS}}

    @app.post("/api/models", dependencies=ui)
    def models(body: dict):
        from ..grok.transport import AIUnavailable

        try:
            return {"models": ctx.service.models(body.get("provider", ""), body.get("key") or None,
                                                 body.get("base_url") or None)}
        except AIUnavailable as e:
            return JSONResponse({"models": [], "error": e.message}, status_code=200)
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/setup", dependencies=ui)
    def setup(body: dict):
        provider = body.get("provider", "")
        try:
            p = providers.get(provider)
        except ValueError as e:
            raise HTTPException(400, str(e))
        model = str(body.get("model", "")).strip()
        base_url = str(body.get("base_url", "")).strip()
        key = str(body.get("key", "")).strip() or keystore.get_key(provider) or ""
        if not model:
            raise HTTPException(400, "Pick or type a model.")
        if p.id == "custom" and not base_url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
            raise HTTPException(400, "The service address must start with https://")
        if not key:
            raise HTTPException(400, "Paste your key first.")
        ok, message = ctx.service.test(provider, key, model, base_url or None)
        if not ok and not body.get("save_anyway"):
            return {"ok": False, "message": message}
        keystore.set_key(provider, key)
        ctx.store.update(provider=provider, model=model, base_url=base_url, setup_done=True)
        ctx.service.history.clear()
        return {"ok": True, "message": message if ok else "Saved. I couldn't test it right now."}

    @app.post("/api/forget_key", dependencies=ui)
    def forget_key(body: dict):
        keystore.delete_key(providers.get(body.get("provider", "")).id)
        return {"ok": True}

    # ---- settings ------------------------------------------------------------------------

    @app.post("/api/settings", dependencies=ui)
    def settings(body: dict):
        changes: dict[str, Any] = {}
        if "warn_free_gb" in body:
            if body["warn_free_gb"] not in (10, 20, 40):
                raise HTTPException(400, "Pick 10, 20, or 40 GB.")
            changes["warn_free_gb"] = body["warn_free_gb"]
        if "end_of_day" in body:
            try:
                h, m = map(int, str(body["end_of_day"]).split(":"))
                changes["end_of_day"] = dtime(h, m)
            except ValueError:
                raise HTTPException(400, "Use a time like 18:00.")
        if "history_days" in body:
            if not isinstance(body["history_days"], int) or not 30 <= body["history_days"] <= 3650:
                raise HTTPException(400, "Keep history for 30 to 3650 days.")
            changes["history_days"] = body["history_days"]
        if "fixes_paused" in body:
            changes["fixes_paused"] = bool(body["fixes_paused"])
            ctx.pipeline.runner.paused = changes["fixes_paused"]
        if "ai_daily_limit" in body:
            v = body["ai_daily_limit"]
            if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 1000:
                raise HTTPException(400, "Pick a daily limit from 0 (no limit) to 1000.")
            changes["ai_daily_limit"] = v
        if "off" in body:
            from ..settings import PROBLEM_IDS

            off = body["off"]
            if not isinstance(off, list) or any(k not in PROBLEM_IDS for k in off):
                raise HTTPException(400, "Unknown kind of problem.")
            changes["off"] = sorted(set(off))
        if "forget_never" in body:
            fp = body["forget_never"]
            ctx.pipeline.triage.never.discard(fp)
            changes["never"] = [n for n in ctx.store.settings.never if n != fp]
        ctx.store.update(**changes)
        return ctx.store.public()

    # ---- fix history ---------------------------------------------------------------------

    @app.get("/api/history", dependencies=ui)
    def history(limit: int = 50, q: str = ""):
        h = ctx.history
        if q:
            ids = {r["id"] for r in h.touched(q)}
            rows = [r for r in h.recent(500) if r["id"] in ids or q.lower() in (r["title"] or "").lower()]
        else:
            rows = h.recent(min(max(limit, 1), 500))
        return {"items": rows}

    @app.get("/api/history/export", dependencies=ui)
    def export(fmt: str = "json"):
        rows = ctx.history.recent(5000)
        if fmt == "csv":
            buf = io.StringIO()
            cols = ["started_at", "finished_at", "title", "severity", "fixed_by", "approved_via", "outcome", "summary", "id"]
            w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
            return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                                     headers={"Content-Disposition": "attachment; filename=fix-history.csv"})
        full = [ctx.history.get(r["id"]) for r in rows]
        return JSONResponse(full, headers={"Content-Disposition": "attachment; filename=fix-history.json"})

    @app.get("/api/history/{fix_id}", dependencies=ui)
    def history_item(fix_id: str):
        rec = ctx.history.get(fix_id)
        if rec is None:
            raise HTTPException(404, "Not in the history.")
        return rec

    # ---- windows -------------------------------------------------------------------------

    @app.post("/api/ui/{action}", dependencies=ui)
    def ui_action(action: str, body: dict | None = None):
        body = body or {}
        if ctx.ui is None:
            return {"ok": False}
        if action == "close_popup":
            ctx.ui.close_popup(body.get("key", ""))
        elif action == "close_digest":
            ctx.ui.close_digest()
        elif action == "open_main":
            view = body.get("view", "home")
            if view not in ("home", "ask", "activity", "settings", "setup"):
                raise HTTPException(400, "Unknown page.")
            ctx.ui.show_main(view)
        else:
            raise HTTPException(404)
        return {"ok": True}

    # ---- lab (only with --demo --lab) ----------------------------------------------------------

    lab = [Depends(lab_auth)]

    @app.get("/api/lab/state", dependencies=lab)
    def lab_state():
        p = ctx.pipeline
        return {"cards": [c.to_dict() for c in p.runner.cards.values()], "history": ctx.history.recent(50),
                "refusals": ctx.history.refusals()}

    @app.post("/api/lab/approve", dependencies=lab)
    def lab_approve(body: dict):
        return _approve(body.get("card_id", ""), ApprovedVia.LAB)

    @app.post("/api/lab/chat", dependencies=lab)
    def lab_chat(body: dict):
        return ctx.service.chat(str(body.get("text", ""))[:4000])

    @app.post("/api/lab/check_now", dependencies=lab)
    def lab_check():
        if ctx.watcher:
            ctx.watcher.poll_once()
        return {"ok": True}

    @app.post("/api/lab/digest_now", dependencies=lab)
    def lab_digest():
        ctx.pipeline.show_digest()
        return get_digest()

    @app.get("/api/lab/history/{fix_id}", dependencies=lab)
    def lab_history(fix_id: str):
        return ctx.history.get(fix_id) or {}

    @app.post("/api/lab/replay", dependencies=lab)
    def lab_replay(body: dict):
        """Recorded events for problems the lab can't safely cause (a failing disk)."""
        from ..watcher import parse_events_xml

        routes = [ctx.pipeline.handle(s).value for s in parse_events_xml(str(body.get("xml", "")))]
        return {"routes": routes}

    return app


__all__ = ["AppContext", "create_app"]
