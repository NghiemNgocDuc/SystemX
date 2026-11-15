"""Presentation text derived from this run's real card and history, never a timer's guess."""

from __future__ import annotations

from dataclasses import dataclass


def readiness(state: dict) -> str | None:
    settings = state.get("settings", {})
    if not settings.get("demo"):
        return "Restart SystemAutoBot with --demo --lab."
    if settings.get("fixes_paused"):
        return "In Settings, turn off Pause fixes before starting."
    if settings.get("autopilot"):
        return "In Settings, turn off Autopilot to demonstrate approval first."
    if "frozen_app" in settings.get("off", []):
        return "In Settings, turn on Frozen programs."
    if any(fp.startswith("frozen_app:") for fp in settings.get("never", [])):
        return "In Settings, remove the frozen-program rule from Never for this."
    return None


@dataclass
class DemoSession:
    pid: int
    started: float
    card_id: str | None = None
    detected_seconds: float | None = None

    def observe(self, state: dict, now: float, record: dict | None = None) -> dict:
        card = next((c for c in state.get("cards", [])
                     if c.get("kind") == "frozen_app" and c.get("process_id") == self.pid), None)
        if card and self.card_id is None:
            self.card_id = card["id"]
            self.detected_seconds = max(0, now - self.started)
        result = (card or {}).get("result") or {}
        view = {"stage": 1, "title": "The test window is frozen. Watching for detection...",
                "next": "Keep Demo Notes in front until Windows marks it Not Responding. Detection usually takes 15-30 seconds.",
                "evidence": [f"Test process: {self.pid}", f"Elapsed: {int(max(0, now - self.started))} s"],
                "can_open": False, "fix_id": result.get("fix_id")}
        if self.card_id:
            view.update(stage=2, title="Detected. The repair is ready for your permission.",
                        next="Open the approval box. Read the unsaved-work warning, then choose Allow. You can also choose Not now.",
                        can_open=not bool((card or {}).get("used")))
            view["evidence"].append(f"Detected after {self.detected_seconds:.1f} s (panel observation)")
            if card and card.get("fix"):
                view["evidence"].append("Proposed repair: " + card["fix"]["title"])
        # A result is evidence of an attempt, not necessarily a successful fix.
        if result:
            view.update(stage=3, title="Repair result: " + result.get("outcome", "unknown"), can_open=False,
                        next="Review the actual repair record below. Then try typing in the reopened Demo Notes window.")
            view["evidence"].append(result.get("message", ""))
        if record and record.get("card_id") == self.card_id:
            steps = record.get("steps", [])
            checks = record.get("verifications", [])
            verdict = checks[-1].get("result") if checks else None
            view["evidence"] += [f"{s.get('status', 'unknown')}: {s.get('summary', s.get('tool', ''))}" for s in steps]
            view["evidence"].append("Re-check: " + (verdict or "not recorded yet"))
            if checks and checks[-1].get("detail"):
                view["evidence"].append(checks[-1]["detail"])
            view["evidence"].append("Approval: " + (record.get("approved_via") or "not recorded"))
            view["evidence"].append("History record: " + record["id"])
            reopened = any(s.get("tool") == "open_program" and s.get("status") == "done" for s in steps)
            closed = any(s.get("tool") == "close_program" and s.get("status") == "done" for s in steps)
            if record.get("outcome") == "done" and verdict == "fixed" and closed and reopened:
                view.update(stage=4, title="Repair recorded. The re-check found no frozen window.",
                            next="Type in the reopened Demo Notes window to show that it responds. Activity contains these same steps.")
            elif record.get("outcome"):
                view.update(stage=3, title="Repair needs review: " + str(verdict or record["outcome"]),
                            next="The evidence does not confirm a complete recovery. Review the result in Activity before continuing.")
        if not self.card_id and now - self.started >= 45:
            view["next"] = ("Still waiting. Keep the test window in front; check that Frozen programs is on. "
                            "The panel has not received a detection card yet.")
        return view
