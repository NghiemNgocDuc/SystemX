"""No internet, including the two causes people rarely guess: DNS servers left set by
hand (often by an old VPN) and a wrong clock. Works with no Grok and no internet."""

from __future__ import annotations

import sys

from ..models import FixOption, ManualStep, Problem, Signal, StepResult
from ..tools.checks import CLOCK_SKEW_LIMIT
from ..tools.fixes import admin_step, command_step
from .base import Playbook

FAILS_BEFORE_PROBLEM = 2


def manual_dns_servers() -> list[str]:
    """IPv4 DNS servers typed in by hand on any interface. Readable without admin."""
    if sys.platform != "win32":
        return []
    import winreg

    found = []
    base = r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as root:
            for i in range(winreg.QueryInfoKey(root)[0]):
                with winreg.OpenKey(root, winreg.EnumKey(root, i)) as k:
                    try:
                        servers, _ = winreg.QueryValueEx(k, "NameServer")
                    except OSError:
                        continue
                    if servers.strip():
                        found.append(servers)
    except OSError:
        pass
    return found


def classify(d: dict) -> str | None:
    """None when healthy, else 'offline' or 'clock'."""
    if not d.get("adapter_up"):
        return None  # Wi-Fi off or airplane mode is the person's choice, not a fault
    if d.get("dns_ok") and d.get("http_ok"):
        skew = d.get("clock_skew_seconds")
        if skew is not None and abs(skew) > CLOCK_SKEW_LIMIT:
            return "clock"
        return None
    return "offline"


class Network(Playbook):
    id = "network"

    def __init__(self, tools, manual_dns=manual_dns_servers):
        super().__init__(tools)
        self._streak = 0
        self._manual_dns = manual_dns

    def match(self, signal: Signal) -> Problem | None:
        if signal.source != "check" or signal.name != "network":
            return None
        kind = classify(signal.data)
        self._streak = self._streak + 1 if kind else 0
        return self._problem(signal.data, kind if self._streak >= FAILS_BEFORE_PROBLEM else None, signal)

    def _problem(self, d: dict, kind: str | None, signal: Signal | None = None) -> Problem:
        ev = dict(d, kind=kind, broken=kind is not None)
        if kind == "offline":
            ev["manual_dns"] = self._manual_dns()
        title = {
            "clock": "Your clock is wrong, so secure websites won't load.",
            "offline": "You're connected, but the internet isn't working.",
            None: "The internet is working.",
        }[kind]
        # One fingerprint for "the internet", so a healthy reading clears a waiting problem.
        return Problem(self.id, f"{self.id}:internet", title, evidence=ev, signal=signal)

    def refresh(self, problem: Problem) -> Problem:
        d = self.tools.checks.network_probe()
        return self._problem(d, classify(d))

    def options(self, problem: Problem) -> list[FixOption]:
        launcher = self.tools.launcher
        kind = problem.evidence.get("kind")
        if kind == "clock":
            return [FixOption(
                title="Set the clock",
                explain="I'll set the clock from the internet time server. Windows will ask for permission.",
                build_steps=lambda p: [admin_step("time_sync", launcher)],
                needs_admin=True, auto="safe",
            )]
        opts = []
        if problem.evidence.get("manual_dns"):
            opts.append(FixOption(
                title="Switch DNS back to automatic",
                explain="Your DNS servers were set by hand and aren't answering. I'll switch them back to automatic. "
                        "Windows will ask for permission.",
                build_steps=lambda p: [admin_step("dns_automatic", launcher)],
                undo_possible=True, needs_admin=True,
            ))
        opts.append(FixOption(
            title="Refresh the connection",
            explain="I'll clear saved web addresses and ask the router for a fresh address.",
            build_steps=lambda p: [command_step("flush_dns"), command_step("renew_address")],
            undo_possible=False, no_undo_text="Nothing to undo: this only refreshes.", auto="safe",
        ))
        opts.append(FixOption(
            title="Reset the network stack",
            explain="I'll reset Windows networking. It takes effect after a restart. Windows will ask for permission.",
            build_steps=lambda p: [admin_step("winsock_reset", launcher)],
            needs_admin=True,
        ))
        return opts

    def manual_steps(self, problem: Problem) -> list[ManualStep]:
        if problem.evidence.get("kind") == "clock":
            return [ManualStep("Open Date & time and turn on Set time automatically, then press Sync now.",
                               "ms-settings:dateandtime")]
        return [
            ManualStep("Unplug the router for 30 seconds, then plug it back in."),
            ManualStep("If you use a VPN, turn it off and try again."),
            ManualStep("Forget the Wi-Fi network and join it again with the password.", "ms-settings:network-wifi"),
            ManualStep("Open Network settings and run Network reset if nothing else works.", "ms-settings:network-status"),
        ]

    def verify(self, problem, fresh, results: list[StepResult]):
        return None
