"""Think mode: "Fix for me", fully in the open.

Every "Fix for me" opens a live session the person can watch:

  1. A built-in repair shows its exact plan (each step, the real Windows command, what it may touch)
     and waits for Allow.
  2. A problem with no built-in repair is handed to the AI, but only after the person sees exactly
     what will be sent and presses Send. Then each thing the AI does appears as it happens: what it
     said, every check it ran and what that check saw. If it suggests a repair, the exact plan is
     shown and nothing runs until the person presses Allow. Stop works at any time.

The AI still never runs anything itself: its tools only read, and a repair it picks comes from the
app's fixed list and is built by the app. Allow goes through the same approval and runner as any fix.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from ..models import Card, FixOption, FixStep
from ..safety.redact import trim
from .client import SYSTEM_PROMPT, problem_prompt
from .tools import ToolSession
from .transport import AIUnavailable

log = logging.getLogger("systemautobot.think")
KEEP_SESSIONS = 30
AI_FIRST = ("unknown", "error_dialog")  # the app has nothing specific to say about these; the AI is the way in


def own_steps(card: Card) -> list[str]:
    """The card's built-in steps, minus the one that only says "press Fix for me" (we're already there)."""
    return [m.text for m in card.manual_steps if not m.text.startswith("Press Fix for me")]

THINK_ASK = ("\n\nThe person is watching you work. Before each check, say in under 12 words what you will look "
             "at. Keep your final answer to two short sentences, and any steps to under 15 words each.")

# What the AI is allowed to read, in plain words (shown before anything is sent).
CHECKS_IT_MAY_RUN = [
    "free space on a drive",
    "whether the internet, name lookups and the clock work",
    "which programs are Not Responding",
    "the 20 programs using the most memory",
    "recent Windows error events",
    "whether this account is an administrator",
]


# ---- plain words for each step and each check ----------------------------------------------------

ADMIN_WORDS = {
    "sfc_scan": "Check and repair Windows system files",
    "dism_restore": "Repair the Windows component store (uses the internet)",
    "winsock_reset": "Reset Windows networking (takes effect after a restart)",
    "time_sync": "Turn on the Windows time service and set the clock from the internet",
    "restart_print_spooler": "Stop and restart the Windows print service",
    "reset_windows_update": "Stop the Windows Update services, rename their download caches, and start them again",
    "defender_signature_update": "Ask Windows Security to download the latest virus definitions",
    "firewall_enable": "Turn the Windows firewall on for every network type",
    "dns_automatic": "Set every network adapter's DNS servers back to automatic",
    "check_disk": "Have Windows check the drive and repair file errors (online scan, no restart)",
}
COMMAND_WORDS = {
    "flush_dns": ("Clear the saved web addresses (DNS cache)", "ipconfig /flushdns"),
    "renew_address": ("Ask the router for a fresh network address", "ipconfig /renew"),
}


def _admin_commands(entry: str) -> tuple[list[str], bool]:
    try:
        from sab_helper.helper import ALLOWLIST
    except Exception:  # not on Windows (tests elsewhere): the words still describe the step
        return [], False
    spec = ALLOWLIST.get(entry, {})
    cmds = [" ".join(argv) for argv, _codes, _timeout in spec.get("commands", [])]
    return cmds, bool(spec.get("restore_point"))


def describe_step(step: FixStep) -> dict[str, Any]:
    """One step as the person sees it before allowing: what, the exact command, and any notes."""
    t, a = step.tool, step.args
    notes: list[str] = []
    commands: list[str] = []
    detail: list[str] = []  # long specifics (folder paths), shown only when the person asks
    if t.startswith("admin:"):
        entry = t.split(":", 1)[1]
        text = ADMIN_WORDS.get(entry, entry.replace("_", " "))
        commands, restore = _admin_commands(entry)
        notes.append("Runs as administrator: Windows shows its permission prompt first.")
        if restore:
            notes.append("Windows makes a restore point first, so you can go back.")
    elif t in COMMAND_WORDS:
        text, cmd = COMMAND_WORDS[t]
        commands = [cmd]
    elif t == "delete_temp_files":
        folders = list(a.get("folders", []))
        text = f"Delete temporary files from {len(folders)} folder{'' if len(folders) == 1 else 's'}"
        detail = folders
        notes.append("Skips anything changed in the last hour or in use, and never follows links into other folders.")
    elif t == "open_page":
        text = f"Open {a.get('name', 'a Windows page')}"
        notes.append("Opens the page for you. It changes nothing by itself.")
    elif t == "empty_recycle_bin":
        text = "Empty the Recycle Bin"
    elif t == "close_program":
        text = f"Close {a.get('name', 'the program')} (process {a.get('pid')})"
    elif t == "open_program":
        text = f"Open {a.get('name', 'the program')} again"
    elif t == "cancel_print_job":
        text = f"Cancel print job #{a.get('job_id')} on {a.get('printer')}"
    else:
        text = t.replace("_", " ")
    return {"text": text, "commands": commands, "notes": notes, "detail": detail, "admin": step.needs_admin}


def describe_plan(option: FixOption, card: Card) -> dict[str, Any]:
    try:
        steps = [describe_step(s) for s in option.build_steps(card.problem)]
    except Exception as e:  # building a step re-checks safety (a pid may be gone): say so
        steps = []
        log.info("plan could not be built: %s", e)
    restore = any("restore point" in n for s in steps for n in s["notes"])
    undo = ("You can undo this from Activity." if option.undo_possible
            else "There's no one-click undo, but the restore point lets you go back." if restore
            else option.no_undo_text)
    return {"title": option.title, "explain": option.explain, "warnings": list(option.warnings), "undo": undo,
            "admin": option.needs_admin, "steps": steps,
            "by_ai": card.proposed_by == "grok", "reason": (card.grok or {}).get("reason") or ""}


def tool_words(name: str, raw_args: str) -> str:
    try:
        a = json.loads(raw_args or "{}")
    except json.JSONDecodeError:
        a = {}
    a = a if isinstance(a, dict) else {}
    return {
        "disk_space": f"Checking free space on drive {str(a.get('drive', 'C'))[:1].upper()}:",
        "network_probe": "Testing the internet connection, name lookups and the clock",
        "hung_windows": "Looking for programs that are Not Responding",
        "running_apps": "Listing the programs using the most memory",
        "recent_errors": f"Reading Windows error events from the last {a.get('hours', 24)} hours",
        "is_admin": "Checking whether this account is an administrator",
        "propose_fix": f"Picking a repair from my fixed list: {str(a.get('entry', '')).replace('_', ' ')}",
        "suggest_manual_steps": "Writing steps you can follow yourself",
        "set_severity": f"Rating how urgent this is: {a.get('severity', '')}",
    }.get(name, f"Asked for an unknown tool ({trim(name, 40)}), which I refused")


def _seen(result: str) -> str:
    """What a check returned, readable. Already redacted by the tool session."""
    try:
        return trim(json.dumps(json.loads(result), indent=1, ensure_ascii=False), 1500)
    except (json.JSONDecodeError, TypeError):
        return trim(result, 1500)


# ---- one session --------------------------------------------------------------------------------

class Session:
    def __init__(self, card: Card):
        self.id = uuid.uuid4().hex
        self.card = card
        self.status = "starting"  # plan | consent | thinking | proposal | manual | answered | error | stopped
        self.events: list[dict[str, Any]] = []
        self.stop = threading.Event()
        self.started = datetime.now(timezone.utc)
        self._lock = threading.Lock()

    def add(self, kind: str, **data: Any) -> None:
        with self._lock:
            self.events.append({"seq": len(self.events), "kind": kind, **data})

    def view(self, after: int = 0) -> dict[str, Any]:
        with self._lock:
            return {"id": self.id, "status": self.status, "card_id": self.card.id,
                    "events": [e for e in self.events if e["seq"] >= after]}


class ThinkManager:
    def __init__(self, pipeline, service):
        self.pipeline = pipeline
        self.service = service
        self.sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def get(self, sid: str) -> Session | None:
        return self.sessions.get(sid)

    def _keep(self, s: Session) -> Session:
        with self._lock:
            self.sessions[s.id] = s
            for old in sorted(self.sessions.values(), key=lambda x: x.started)[:-KEEP_SESSIONS]:
                old.stop.set()
                self.sessions.pop(old.id, None)
        return s

    def start(self, card: Card) -> Session:
        s = self._keep(Session(card))
        if card.option is not None:
            self._show_plan(s, card)
            return s
        status = self.service.status()
        if not status["configured"]:
            s.status = "manual"
            s.add("no_ai", text="There's no built-in fix for this, and no AI is connected to look into it.")
            if own_steps(card):
                s.add("manual", steps=own_steps(card))
            return s
        label, model = status["label"], status["model"]
        p = card.problem
        prompt = problem_prompt(p.title, p.evidence, p.signal.text if p.signal else "") + THINK_ASK
        s.status = "consent"
        if card.problem.playbook_id not in AI_FIRST and own_steps(card):
            s.add("manual", steps=own_steps(card))  # a known problem: what to do comes first
        s.add("consent", provider=label, model=model, sent=prompt, rules=SYSTEM_PROMPT,
              checks=CHECKS_IT_MAY_RUN, limit=self.service.usage())
        return s

    def _show_plan(self, s: Session, card: Card) -> None:
        s.status = "plan"
        s.add("plan", card_id=card.id, **describe_plan(card.option, card))

    def send(self, s: Session) -> None:
        """The person pressed Send: the AI looks, in the background, and every step is shown."""
        if s.status != "consent":
            return
        s.status = "thinking"
        s.add("sent", text="Sent. Here's everything it does, as it happens.")
        threading.Thread(target=self._run, args=(s,), name="think", daemon=True).start()

    def stop(self, s: Session) -> None:
        s.stop.set()
        if s.status in ("consent", "plan", "thinking"):
            s.status = "stopped"
            s.add("stopped", text="Stopped. I didn't change anything.")

    def _run(self, s: Session) -> None:
        card, p = s.card, s.card.problem
        try:
            limited = self.service._over_limit()
            if limited:
                s.status = "error"
                s.add("error", text=limited)
                return
            client = self.service.client()
        except AIUnavailable as e:
            s.status = "error"
            s.add("error", text=e.message)
            return

        said: list[str] = []

        def observe(kind: str, data: dict) -> None:
            if kind == "wait":
                s.add("wait", seconds=round(data["seconds"]),
                      text=f"{data['text']} Waiting {round(data['seconds'])} seconds, then I'll carry on.")
            elif kind == "round":
                s.add("round", n=data["n"], max=data["max"])
            elif kind == "reasoning":
                s.add("reasoning", text=trim(data["text"], 2000))
            elif kind == "say":
                said.append(data["text"])
                s.add("say", text=trim(data["text"], 1000))
            elif kind == "tool":
                ok = '"error"' not in data["result"][:20]
                s.add("tool", name=data["name"], text=tool_words(data["name"], data["args"]),
                      args=trim(data["args"], 300), seen=_seen(data["result"]), ok=ok)

        session = ToolSession(self.service.tools)
        prompt = problem_prompt(p.title, p.evidence, p.signal.text if p.signal else "") + THINK_ASK
        out = client.run(session, prompt, observer=observe, stop=s.stop.is_set)
        self.service._record("think", out)
        s.add("usage", provider=client.label, model=out.model, rounds=out.rounds, tokens=out.tokens,
              replay=out.replay)
        if out.error is not None and out.error.kind != "stopped" and (session.proposal or session.manual_steps):
            # A later request failed, but the AI had already written steps or picked a repair. Keep that.
            s.add("cut_short", text=f"{self._why(out.error, client.label)} "
                                    f"{client.label} had already finished the part below, so here it is.")
            out = replace(out, error=None, text=said[-1] if said else "")
        if out.error is not None:
            if out.error.kind != "stopped":
                s.status = "error"
                s.add("error", text=self._why(out.error, client.label))
            return
        if s.stop.is_set():
            return
        filled = self.service._card(p, session, out)
        if filled.option is not None and self._disk_failing(p) and (
                filled.option.needs_admin or "Delete" in filled.option.title):
            s.add("held_back", text=(f"{client.label} suggested “{filled.option.title}”, but your disk may "
                                     "be failing, so I won't run repairs that write a lot to it. Back up first."))
            filled.option, filled.proposed_by = None, "playbook"
        self.service._fill(card, filled)  # the card on Home now carries the same answer
        card.ai_note = ""
        if out.text:
            s.add("answer", text=out.text, provider=client.label)
        if session.severity:
            self.pipeline.triage.raise_severity(p, session.severity)
        if card.option is not None:
            card.used = False
            card.created_at = datetime.now(timezone.utc)  # a fresh card for this decision
            self.pipeline.runner.register(card)
            self._show_plan(s, card)
            s.status = "proposal"
        elif card.manual_steps:
            s.status = "manual"
            s.add("manual", steps=[m.text for m in card.manual_steps],
                  suggested=any(m.suggested for m in card.manual_steps))
        else:
            s.status = "answered"

    @staticmethod
    def _why(e: AIUnavailable, label: str) -> str:
        """The app's sentence plus the provider's own words, so nothing about a failure is hidden."""
        reason = getattr(e, "reason", "")
        return f"{e.message} ({label} said: {reason})" if reason else e.message

    def _disk_failing(self, problem) -> bool:
        return problem.playbook_id != "disk_failing" and any(
            f.startswith("disk_failing:") for f in self.pipeline.triage.waiting)
