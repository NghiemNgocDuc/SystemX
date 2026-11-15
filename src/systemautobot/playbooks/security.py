"""Windows Security and the firewall: protection turned off, old virus definitions, firewall off.

Stays quiet when another antivirus or firewall is in charge (Defender in passive mode, or a
third-party firewall registered with Windows), so it never nags about a choice the person made."""

from __future__ import annotations

from ..models import FixOption, ManualStep, Problem, Signal
from ..tools.fixes import admin_step
from .base import Playbook

SIGNATURE_DAYS = 3
WATCHED_PROFILES = {"Private", "Public"}  # Domain only matters on work networks


def assess(data: dict | None) -> dict[str, dict]:
    """Turn one security reading into {issue: evidence}. Empty dict means all good (or unknown)."""
    if not data:
        return {}
    issues: dict[str, dict] = {}
    d = data.get("defender") or None
    if d and str(d.get("mode", "Normal")).lower() == "normal":
        if not d.get("realtime"):
            issues["realtime"] = {"tamper_protected": d.get("tamper_protected")}
        age = d.get("signature_age_days")
        if isinstance(age, int) and age > SIGNATURE_DAYS:
            issues["signatures"] = {"age_days": age}
    others = [n for n in data.get("other_firewalls") or [] if "windows" not in n.lower()]
    off = sorted(p["name"] for p in data.get("firewall") or [] if p.get("name") in WATCHED_PROFILES and not p.get("enabled"))
    if off and not others:
        issues["firewall"] = {"profiles_off": off}
    return issues


TITLES = {
    "realtime": "Windows Security real-time protection is off.",
    "signatures": "Windows Security's virus definitions are {age_days} days old.",
    "firewall": "The Windows firewall is off for {profiles} networks.",
}


class Security(Playbook):
    id = "security"
    ISSUES = ("realtime", "signatures", "firewall")

    def match(self, signal: Signal) -> Problem | None:
        if signal.source != "check" or not signal.name.startswith("security_"):
            return None
        issue = signal.name.removeprefix("security_")
        if issue not in self.ISSUES:
            return None
        return self._problem(issue, signal.data, signal)

    def _problem(self, issue: str, ev: dict | None, signal: Signal | None = None) -> Problem:
        if ev is None:
            return Problem(self.id, f"{self.id}:{issue}", "Windows Security is fine.", evidence={"issue": issue, "bad": False})
        fmt = dict(ev, profiles=" and ".join(ev.get("profiles_off", [])).lower() or "some")
        return Problem(self.id, f"{self.id}:{issue}", TITLES[issue].format(**fmt),
                       evidence=dict(ev, issue=issue, bad=True), signal=signal)

    def refresh(self, problem: Problem) -> Problem:
        issue = problem.evidence["issue"]
        return self._problem(issue, assess(self.tools.checks.security_status()).get(issue))

    def options(self, problem: Problem) -> list[FixOption]:
        issue, launcher = problem.evidence.get("issue"), self.tools.launcher
        if issue == "signatures":
            return [FixOption("Update virus definitions",
                              "I'll ask Windows Security to download the latest virus definitions. "
                              "Windows will ask for permission.",
                              build_steps=lambda p: [admin_step("defender_signature_update", launcher)],
                              needs_admin=True, no_undo_text="Nothing to undo: this only updates definitions.")]
        if issue == "firewall":
            return [FixOption("Turn the firewall back on",
                              "I'll turn the Windows firewall back on for every network type. "
                              "Windows will ask for permission.",
                              build_steps=lambda p: [admin_step("firewall_enable", launcher)],
                              needs_admin=True, no_undo_text="You can turn it off again in Windows Security.")]
        return []  # real-time protection: Tamper Protection rightly blocks apps from switching it on

    def manual_steps(self, problem: Problem) -> list[ManualStep]:
        issue = problem.evidence.get("issue")
        if issue == "realtime":
            return [
                ManualStep("Open Windows Security, then Virus & threat protection.", "windowsdefender:"),
                ManualStep("Under Virus & threat protection settings, press Manage settings."),
                ManualStep("Turn Real-time protection on. Windows asks for permission."),
                ManualStep("If it keeps turning off, another antivirus or a work policy may be controlling it."),
            ]
        if issue == "firewall":
            return [ManualStep("Open Windows Security, then Firewall & network protection, and turn each network "
                               "type back on.", "windowsdefender:")]
        return [ManualStep("Open Windows Security and press Check for updates under Protection updates.",
                           "windowsdefender:")]
