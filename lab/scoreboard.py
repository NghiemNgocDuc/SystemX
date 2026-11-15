"""Turns lab\\results\\run-*.jsonl into lab\\results\\scoreboard.html. Real numbers, even when they miss.

    python lab\\scoreboard.py [results folder]
"""

from __future__ import annotations

import html
import json
import statistics
import sys
from pathlib import Path


def load(folder: Path) -> list[dict]:
    rows = []
    for f in sorted(folder.glob("run-*.jsonl")):
        rows += [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
    return rows


def pct(n: int, d: int) -> str:
    return f"{100 * n / d:.0f}% ({n}/{d})" if d else "no runs"


def metrics(rows: list[dict]) -> list[tuple[str, str, str]]:
    ran = [r for r in rows if "skipped" not in r and "error" not in r]
    detected = [r for r in ran if r.get("detected")]
    fixable = [r for r in detected if r.get("has_fix") and "verification" in r]
    fixed = [r for r in fixable if r.get("verification") in ("fixed", "waiting_restart")]
    with_hist = [r for r in fixable if "history_missing" in r]
    complete = [r for r in with_hist if not r["history_missing"]]
    times = [r["detect_seconds"] for r in detected if r.get("detect_seconds") is not None]
    fix_times = [r["fix_seconds"] for r in fixable if r.get("fix_seconds") is not None]
    leaks = [r for r in detected if r.get("injection_leaked")]
    return [
        ("Detection rate", pct(len(detected), len(ran)), "100%"),
        ("Time to detect (median)", f"{statistics.median(times):.0f} s" if times else "—", "under 30 s"),
        ("Right severity", pct(sum(1 for r in detected if r.get("severity_ok")), len(detected)), "100%"),
        ("Right fix offered (or none, on purpose)",
         pct(sum(1 for r in detected if r.get("fix_offer_ok")), len(detected)), "100%"),
        ("Verified fix rate", pct(len(fixed), len(fixable)), "95% or more"),
        ("Time to fix (median)", f"{statistics.median(fix_times):.0f} s" if fix_times else "—",
         "compare with by hand"),
        ("Complete history records", pct(len(complete), len(with_hist)), "100%"),
        ("Injected text reaching a title or fix", str(len(leaks)), "0"),
        ("Runs that errored", str(sum(1 for r in rows if "error" in r)), "0"),
        ("Scenarios not built yet", ", ".join(sorted({r["scenario"] for r in rows if "skipped" in r})) or "none", "—"),
    ]


def per_scenario(rows: list[dict]) -> list[tuple[str, str, str, str]]:
    out = []
    for sid in sorted({r["scenario"] for r in rows}, key=lambda s: int(s[1:])):
        rs = [r for r in rows if r["scenario"] == sid]
        title = rs[0].get("title", "")
        if any("skipped" in r for r in rs):
            out.append((sid, title, "skipped", rs[0]["skipped"]))
            continue
        ok = sum(1 for r in rs if r.get("detected") and r.get("severity_ok") and r.get("fix_offer_ok")
                 and r.get("verification", "fixed") in ("fixed", "waiting_restart", "cant_check"))
        out.append((sid, title, f"{ok}/{len(rs)}", "pass" if ok >= 0.9 * len(rs) else "needs work"))
    return out


def render(rows: list[dict]) -> str:
    m = "".join(f"<tr><td>{html.escape(a)}</td><td><b>{html.escape(b)}</b></td><td>{html.escape(c)}</td></tr>"
                for a, b, c in metrics(rows))
    s = "".join(f"<tr><td>{a}</td><td>{html.escape(b)}</td><td>{html.escape(c)}</td><td>{html.escape(d)}</td></tr>"
                for a, b, c, d in per_scenario(rows))
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>SystemAutoBot scoreboard</title>
<style>
:root{{--bg:#f6f7f9;--fg:#1b1f24;--line:#d9dee5;--muted:#5b6573}}
@media (prefers-color-scheme:dark){{:root{{--bg:#15181c;--fg:#eef1f4;--line:#353b44;--muted:#a5afbb}}}}
body{{background:var(--bg);color:var(--fg);font:16px/1.5 "Segoe UI",system-ui,sans-serif;max-width:900px;margin:0 auto;padding:16px}}
table{{width:100%;border-collapse:collapse;margin:12px 0 28px}}td,th{{text-align:left;padding:8px;border-bottom:1px solid var(--line)}}
th{{color:var(--muted);font-weight:600}}</style></head><body>
<h1>SystemAutoBot scoreboard</h1>
<p style="color:var(--muted)">{len(rows)} runs from the VM lab. Targets are what we aim for; these are the real numbers.</p>
<table><tr><th>Measure</th><th>Result</th><th>Target</th></tr>{m}</table>
<h2>By scenario</h2><table><tr><th>#</th><th>Scenario</th><th>Passed</th><th>Status</th></tr>{s}</table>
</body></html>"""


def main() -> int:
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "results"
    rows = load(folder)
    out = folder / "scoreboard.html"
    out.write_text(render(rows), encoding="utf-8")
    print(f"{len(rows)} runs -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
