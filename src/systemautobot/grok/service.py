"""Connects the AI to the pipeline: chat, diagnosis of unfamiliar errors, and first-run setup."""

from __future__ import annotations

import hashlib
import logging
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from ..models import Card, Problem, Severity
from ..playbooks.base import Toolbox
from ..settings import Settings
from . import keystore, providers
from .client import AssistantClient, Outcome, problem_prompt
from .tools import ToolSession
from .transport import AIUnavailable, ReplayTransport, Transport, list_models, make_transport, offline_line

log = logging.getLogger("systemautobot.ai")
HISTORY_TURNS = 10
CACHE_FOR = timedelta(hours=24)

TransportFactory = Callable[[providers.Provider, str, str | None], Transport]


class AssistantService:
    def __init__(self, pipeline, tools: Toolbox, settings: Settings, *,
                 transport_factory: TransportFactory = make_transport,
                 key_loader: Callable[[str], str | None] = keystore.get_key,
                 replay_file: Path | None = None):
        self.pipeline = pipeline
        self.tools = tools
        self.settings = settings
        self.transport_factory = transport_factory
        self.key_loader = key_loader
        self.replay = ReplayTransport.from_file(replay_file) if replay_file else None
        self.history: list[dict] = []
        self._lock = threading.Lock()
        self._diag_cache: dict[str, tuple[datetime, Card]] = {}  # fingerprint -> last diagnosis
        pipeline.diagnoser = self.diagnose

    # ---- usage, limits, cache ----------------------------------------------------

    def _record(self, purpose: str, out: Outcome, provider: str | None = None) -> None:
        try:
            self.pipeline.history.add_ai_usage(provider=provider or self.settings.provider,
                                               model=out.model or self.settings.model,
                                               purpose=purpose, rounds=out.rounds, tokens=out.tokens,
                                               ok=out.error is None)
        except Exception:
            log.exception("could not record AI usage")

    def usage(self) -> dict:
        h = self.pipeline.history
        return {"day": h.ai_usage(timedelta(days=1)), "week": h.ai_usage(timedelta(days=7)),
                "limit": self.settings.ai_daily_limit}

    def _over_limit(self) -> str | None:
        limit = self.settings.ai_daily_limit
        if limit and self.pipeline.history.ai_usage(timedelta(days=1))["calls"] >= limit:
            return (f"I've asked the AI {limit} times today, which is your daily limit. "
                    "You can raise it in Settings. I didn't change anything.")
        return None

    # ---- status and setup ------------------------------------------------------

    @property
    def provider(self) -> providers.Provider:
        return providers.get(self.settings.provider)

    def status(self) -> dict:
        has_key = bool(self.key_loader(self.settings.provider))
        return {"provider": self.settings.provider, "label": self.provider.label, "model": self.settings.model,
                "configured": bool(self.replay) or (has_key and bool(self.settings.model)),
                "replay": bool(self.replay)}

    def client(self) -> AssistantClient:
        if self.replay:
            return AssistantClient(self.replay, self.settings.model or "replay", label="Replay")
        key = self.key_loader(self.settings.provider)
        if not key:
            raise AIUnavailable("no_key", f"Add your {self.provider.label} key in Settings first. "
                                          "I didn't change anything.")
        transport = self.transport_factory(self.provider, key, self.settings.base_url or None)
        return AssistantClient(transport, self.settings.model, label=self.provider.label)

    def models(self, provider_id: str, key: str | None, base_url: str | None) -> list[str]:
        p = providers.get(provider_id)
        key = key or self.key_loader(provider_id)
        if not key:
            raise AIUnavailable("no_key", "Paste a key first.")
        wrong = providers.mismatch(provider_id, key)
        if wrong:
            raise AIUnavailable("bad_key", wrong)
        return list_models(p, key, base_url or None)

    def test(self, provider_id: str, key: str, model: str, base_url: str | None) -> tuple[bool, str]:
        """Check the key on its own first, then one tiny request to the model, so the message says which failed."""
        p = providers.get(provider_id)
        wrong = providers.mismatch(provider_id, key)
        if wrong:
            return False, wrong
        transport = self.transport_factory(p, key, base_url or None)
        available: list[str] = []
        lister = getattr(transport, "models", None)
        if lister:
            try:
                available = lister()
            except AIUnavailable as e:
                if e.kind == "bad_key":
                    return False, _with_reason(e.message, p.label, e.reason)
                # offline and the like: the request below says so plainly
        if available and model not in available:
            return False, (f"Your {p.label} key works, but it can't use the model \u201c{model}\u201d. "
                           "Press Load my models and pick one from the list.")
        client = AssistantClient(transport, model, label=p.label, max_rounds=1)
        out = client.run(ToolSession(self.tools), "Reply with the single word OK.", effort="low")
        self._record("test", out, provider=p.id)
        if out.error:
            e = out.error
            msg = e.message
            if available and e.kind in ("bad_request", "no_tools"):
                msg = f"Your {p.label} key works, but the model \u201c{model}\u201d didn't. " + msg
            return False, _with_reason(msg, p.label, getattr(e, "reason", ""))
        return True, f"{p.label} answered. You're set."

    # ---- chat ------------------------------------------------------------------

    def chat(self, text: str) -> dict:
        with self._lock:
            try:
                client = self.client()
            except AIUnavailable as e:
                return {"reply": e.message, "card": None, "error": e.kind}
            limited = self._over_limit()
            if limited:
                return {"reply": limited, "card": None, "error": "limit"}
            session = ToolSession(self.tools)
            out = client.run(session, text, self.history, effort="high" if not self.history else "medium")
            self._record("chat", out)
            if not out.error:
                self.history += [{"role": "user", "content": text}, {"role": "assistant", "content": out.text}]
                self.history = self.history[-2 * HISTORY_TURNS:]
            card = None
            if session.proposal or session.manual_steps:
                fp = "chat:" + hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:12]
                problem = Problem("unknown", fp, _first_sentence(out.text) or "Something you asked about.",
                                  evidence={"asked": text[:200]}, source="chat", severity=Severity.MINOR)
                card = self._card(problem, session, out)
                self.pipeline.add_chat_card(card)
            return {"reply": out.text, "card": card.to_dict() if card else None,
                    "error": out.error.kind if out.error else None, "replay": out.replay}

    # ---- unfamiliar errors from the watcher --------------------------------------

    def diagnose(self, card: Card, force: bool = False) -> None:
        """Fill a card for an unfamiliar error: explanation, one proposed repair or manual steps.
        The same problem isn't sent twice in 24 hours: the earlier answer is reused."""
        if card.explanation and not force:
            return
        p = card.problem
        cached = self._diag_cache.get(p.fingerprint)
        if cached and datetime.now(timezone.utc) - cached[0] < CACHE_FOR:
            self._fill(card, cached[1])
            return
        try:
            client = self.client()
        except AIUnavailable as e:
            card.ai_note = e.message
            return
        limited = self._over_limit()
        if limited:
            card.ai_note = limited
            return
        session = ToolSession(self.tools)
        out = client.run(session, problem_prompt(p.title, p.evidence, p.signal.text if p.signal else ""))
        self._record("diagnose", out)
        if out.error:
            card.ai_note = out.error.message
            if not (session.proposal or session.manual_steps):
                return
            # A later request failed, but the AI had already written steps or picked a repair: keep them.
            self._fill(card, self._card(p, session, replace(out, error=None, text="")))
            return  # not cached: the next ask should get the full answer
        card.ai_note = ""
        filled = self._card(p, session, out)
        self._diag_cache[p.fingerprint] = (datetime.now(timezone.utc), filled)
        self._fill(card, filled)
        if session.severity:
            self.pipeline.triage.raise_severity(p, session.severity)

    @staticmethod
    def _fill(card: Card, filled: Card) -> None:
        card.option, card.manual_steps = filled.option, list(filled.manual_steps) or card.manual_steps
        card.explanation, card.proposed_by, card.grok = filled.explanation, filled.proposed_by, filled.grok

    def _card(self, problem: Problem, session: ToolSession, out: Outcome) -> Card:
        prop = session.proposal
        return Card(problem=problem, option=prop.option if prop else None, manual_steps=session.manual_steps,
                    proposed_by="grok" if prop else "playbook", explanation=out.text,
                    grok=out.meta(prop.reason if prop else None))


def _first_sentence(text: str) -> str:
    text = (text or "").strip()
    for end in (". ", "? ", "! "):
        if end in text:
            return text.split(end, 1)[0] + end.strip()
    return text[:160]


__all__ = ["AssistantService", "offline_line"]


def _with_reason(message: str, label: str, reason: str) -> str:
    return f"{message} ({label} said: {reason})" if reason else message
