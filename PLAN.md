# SystemAutoBot build plan: start to tested

The order we build and test SystemAutoBot in. [README.md](README.md) says **what** the app does, [DEMO.md](DEMO.md) says **how we prove it**, and this file says **what to do, in what order**. Hours are from the start of work and assume about 24 working hours. Scale them if you have more or less time.

## Ground rules

- Safety code comes first. Nothing that changes the PC is written before the guard, the fix history, and the approval check exist.
- Every module gets its tests in the same pull request. No "tests later".
- Never run a breaker or an unfinished fix on a real laptop. Only the VM.
- Keep `main` runnable. Work on branches and merge small.
- If a step slips by more than 2 hours, cut from the **Nice to have** list at the bottom instead of skipping tests.

## Team split (4 tracks)

- **A: Core and safety.** History store, guard, fix runner, admin helper, packaging.
- **B: Watcher and playbooks.** Checks, event log watcher, triage, playbooks.
- **C: Grok.** xAI client, tool loop, chat flow for unfamiliar errors, key storage.
- **D: UI and lab.** Tray, popups, end-of-day list, history page, VM lab, breakers, scoreboard.

With 3 people, merge C into B. With 2, A+C and B+D.

## Phase 0: Setup (H0 to H2)

- **D:** start the Windows 11 Enterprise evaluation download right away. It's large and the lab depends on it.
- **D:** install VirtualBox or VMware Workstation. Our laptops are Windows 11 Home, so Hyper-V is not available.
- **Everyone:** install Python 3.12 and create the repo layout:
  - `src/systemautobot/` with `grok/`, `tools/`, `safety/`, `playbooks/`, `web/`
  - `src/sab_helper/`, `tests/`, `lab/break/`, `lab/apps/`, `lab/replay/`, `lab/results/`
- **A:** `pyproject.toml` with `pywin32`, `psutil`, `keyring`, `openai`, `fastapi`, `uvicorn`, `pywebview`, `pystray`, `pillow`, `pytest`, `pyinstaller`. Add a one-command test run (`pytest -q`).
- **C:** create the xAI API key and set a spending limit in the xAI console. Confirm the current model name and that `/v1/responses` with `store: false` works, using one manual `curl` call.
- **B:** check which event logs a **standard user** can read: run `wevtutil qe <log> /c:1` for each log in the README table (Application, System, Windows Defender/Operational, Diagnostics-Performance/Operational). Write the results in the README.
- **Everyone:** agree on the shared data shapes before splitting up. Write them as Python dataclasses in `src/systemautobot/models.py`:
  - `Signal` (source, event ID or check name, raw text, time)
  - `Problem` (fingerprint, playbook ID or `unknown`, severity, evidence)
  - `Card` (problem, proposed fix, warnings, undo or not, expiry)
  - `FixRecord`, `Step`, `TouchedFile`, `TouchedSetting`, `Verification`

**Gate:** the repo installs, `pytest` runs (with zero tests), every track knows the data shapes, and the VM download is underway.

## Phase 1: Safety core (H2 to H6), track A

- **Fix history store** (`history.py`), using SQLite in `%LOCALAPPDATA%\SystemAutoBot\history.db`:
  - Tables: `fixes`, `steps`, `touched_files`, `touched_settings`, `verifications`, `undos`, `refusals`.
  - Append-only API: `open_fix()`, `add_step()`, `add_touched_file()`, `add_touched_setting()`, `close_fix()`, `add_verification()`, `link_undo()`. No update or delete functions.
  - Bulk file lists go to `history/{fix_id}.files.jsonl.gz`.
  - Build the summary sentence from step results only.
- **Guard** (`safety/guard.py`):
  - Protected-path list, normalized with real-path resolution.
  - Refuse links, junctions, `..`, and short 8.3 names that resolve into protected folders.
  - `delete_file()` is the only delete in the codebase. It skips locked files and counts them.
  - `close_process()` refuses system processes and returns Couldn't for programs running as admin.
- **Fix runner** (`tools/fixes.py`):
  - Refuse without a valid approval: card ID, per-launch token, not expired (15 min).
  - Re-check the target, then open a history record before step 1.
  - Run the steps in order. An exception stops the run, saves the traceback, and closes the record as Couldn't.
  - At startup, find records that were opened but never closed and raise an "Interrupted repair" card.
- **Admin helper** (`sab_helper/`):
  - Accepts only `<entry_id> <run_id>`. Holds its own allowlist of 8 repairs, each with a hard-coded command.
  - Rejects unknown IDs and extra arguments, writes `result.json` (steps, exit codes, settings touched), and exits.
  - Restore point logic: check System Protection, check for one in the last 24 hours, and report which case applied.
- **Main app refuses admin:** if started elevated, it relaunches unelevated and exits.
- **Tests:** guard paths (every protected folder, every path trick), append-only history, runner refusals, helper argument rejection, redaction helper.

**Gate:** a test proves no file outside temp folders can be deleted, and no fix can run without approval and a history record.

## Phase 2: Watcher and triage (H2 to H8), track B

- **Read-only checks** (`tools/checks.py`). Each check returns small trimmed data (20 rows, 500 characters):
  - free space
  - largest folders
  - running apps and memory
  - frozen windows (`EnumWindows` + `IsHungAppWindow`)
  - recent errors
  - network: adapter, DNS lookup, the `connecttest.txt` probe, and clock skew from the `Date` header
  - print queue
  - startup programs
  - Windows Security status
  - admin or not, managed PC or not, System Protection on or not
- **Watcher** (`watcher.py`):
  - `EvtSubscribe` on the readable logs from Phase 0.
  - Poll loop: 5 minutes normally, 10 seconds with `--demo`.
  - Turn each signal into a `Problem` with a fingerprint.
- **Replay input:** the watcher can read event XML files from `lab/replay/` instead of the live log. This is used by tests and by the disk-failing scenario.
- **Triage** (`triage.py`):
  - The severity rules as one data table.
  - Grok may raise severity, never lower it.
  - Dedupe by fingerprint.
  - Rate limit: 3 major popups an hour, then group the rest. **Not now** quiets a problem for 4 hours unless it gets worse.
  - Waits: fullscreen and Do Not Disturb (`SHQueryUserNotificationState`), locked, asleep.
  - Recurrence cutoff: 3 fixes in 14 days.
- **Collect real events:** each teammate exports a few real error events from their own PC with `wevtutil qe Application /q:"*[System[(Level=2)]]" /c:20 /f:xml`, removes user names and paths, and commits them to `tests/fixtures/events/`.
- **Tests:** replay fixtures produce the right fingerprint and severity, plus dedupe, rate limit, and recurrence.

**Gate:** in demo mode on a dev laptop, a hung test window and a fake low-space value each produce a `Problem` with the right severity within 10 seconds.

## Phase 3: Playbooks (H8 to H14), track B, with track A for the admin parts

Build in this order. Each playbook is one file with `detect()`, `severity()`, `fix_steps()`, `manual_steps()`, `verify()`, and `undo()` (or "no undo" text).

1. **Network**: bad DNS set by hand → back to automatic (admin, old servers saved for undo); clock wrong → time sync (admin); otherwise flush DNS and renew the address. It must work with no internet and no Grok.
2. **Drive almost full**: delete temp and known caches, empty the Recycle Bin as a separate card. "No undo" goes on the card.
3. **Program frozen**: close (with the unsaved-work warning) and reopen. Also restart Explorer.
4. **App keeps crashing**: clear that app's known cache, then reopen.
5. **Print job stuck**: cancel the job, and restart the print service through the helper.

Every playbook also needs:
- Manual steps with **Open** buttons (`ms-settings:` links).
- A test with a faked check result.

**Gate:** playbooks 1 to 5 pass their tests, and each fix writes a complete history record (summary, steps, touched files or settings, verification).

## Phase 4: Grok (H4 to H12), track C

- **Key storage:** `keyring` (Credential Manager). First-run setter and a "test key" button.
- **Redaction:** the key is scrubbed from logs, history, tracebacks, and outgoing tool results.
- **Client** (`grok/client.py`):
  - OpenAI SDK, `base_url="https://api.x.ai/v1"`, Responses API, `store: false`.
  - Pass `reasoning.encrypted_content` back each round.
  - At most 8 tool rounds.
- **Tool schemas** come from the check registry. Fix tools are offered as "propose a card" only; Grok can never run a fix itself. Unknown tool names come back to Grok as errors.
- **Untrusted text:** event text and pasted logs are wrapped and labeled as data before they're sent.
- **Failure handling:**
  - 401 or 403: "the key needs attention"
  - 429 or 5xx: one retry with backoff, then queue for the end-of-day list
  - offline: the fallback line, while known problems keep working
- **Unfamiliar-error flow:** Grok runs checks, then either proposes one allowlisted fix as a card or writes manual steps labeled "Grok's suggestion. I haven't checked these."
- **Fake Grok** for tests: a scripted client. Also a **hostile fake Grok** for the red-team suite. Both live in `tests/`.
- **Recorded replay mode** for demo backup, with a visible "Replay" label.

**Gate:** the S10 error from DEMO.md gives a sensible explanation with the real API, and the full suite passes with the fake client and no network.

## Phase 5: UI and tray (H6 to H16), track D

- **Local server** (`web/`): FastAPI on `127.0.0.1`. The per-launch token lives in memory only, and every request must have a matching Origin. Approve is a POST.
- **`pywebview` windows:**
  - first run (key)
  - chat
  - card (with **Fix this**, **More detail**, **Undo**)
  - major popup (**Fix now**, **Not now**, **More detail**, or **Show me how** and **Remind me later** when there's no fix)
  - end-of-day list (per-row **Fix it for me**, **I'll do it myself**, **Not now**, **Never for this**, plus **Fix the ones I checked**)
  - guided manual steps with **Check again**
  - history page with filters, search by file path, detail view, and export to JSON or CSV
  - settings
- **Tray** (`pystray`):
  - States: Fine, Needs you, Working.
  - Menu: Open, Pause fixes, Quit.
  - Ctrl+Alt+Space opens the popover. Closing the window keeps the app running.
- **End-of-day scheduler:** fires at the set time, re-checks items first, drops ones that went away, catches up after sleep or sign-in, and rolls items over. Demo mode adds **End of day now**.
- **Look:** Segoe UI 16px, 44px buttons, one accent color, visible focus, light and dark, reduced motion. Use only the status words from the README.

**Gate:** with fake problems injected, a major problem pops up right away, a minor one shows only in the end-of-day list, and approving from either one runs the fix and shows it in history.

## Phase 6: Wire up and package (H14 to H18), track A

- Connect the full pipeline: watcher → triage → playbook or Grok → popup or end-of-day list → runner or helper → verify → history.
- **PyInstaller:** build `SystemAutoBot.exe` and `sab-helper.exe`.
- **Per-user installer script:** copy to `%LOCALAPPDATA%\Programs\SystemAutoBot`, add the Startup shortcut, and use the single-instance mutex.
- **Uninstall script:** remove the shortcut and folder, and ask whether to keep history.
- **Observe-only build:** the same exe with **Pause fixes** forced on. **Hand it to teammates as early as possible** (even a rough H12 build), because the weekend observe-only data needs hours to build up.
- Install on the VM as the **standard user** and confirm the admin prompt appears for helper fixes.

**Gate:** clean install on the VM, it starts at sign-in, there's one tray icon, and the main process is not elevated (check in Task Manager's "Elevated" column).

## Phase 7: Lab (H8 to H18), track D

- **VM:** standard user plus admin user, System Protection on, Tamper Protection off. Install the build and **take the clean checkpoint**.
- **Test apps** (`lab/apps/`):
  - `hangme`: tkinter window with a text box and a **Freeze** button that blocks the UI thread.
  - `crashy`: reads `%LOCALAPPDATA%\Crashy\cache\state.json` and crashes natively (`ctypes.string_at(0)`) if the file is corrupt, so Windows logs event 1000.
- **Breakers** (`lab/break/`), one script per scenario S1 to S11 in DEMO.md. Each writes what it changed to `lab/results/break-<id>.json`.
- **Replay files** for S9 (disk events 7 and 153).
- **Scenario runner** (`lab/run_scenarios.py`), for each scenario:
  1. Restore the checkpoint (`VBoxManage snapshot restore` or `vmrun revertToSnapshot`).
  2. Run the breaker.
  3. Poll for detection.
  4. Approve through the lab-only endpoint (exists only with `--demo --lab`).
  5. Wait for verify.
  6. Read the history record and check that it's complete.
  7. Write the results JSON.
- **Admin prompts:** only in the automated VM, set them to approve without asking (no script can click the real prompt). The live demo VM keeps the normal prompt.
- **Scoreboard** (`lab/scoreboard.py`): results JSON → `lab/results/scoreboard.html`.
- **Break it panel:** a small lab window with one button per breaker, so a judge can choose.

**Gate:** `run_scenarios.py --only S1` runs from start to finish with no hands.

## Phase 8: Testing (H16 to H22), everyone

Run in this order. Fix bugs as they show up and re-run the failed layer.

1. **Unit tests** on every commit: guard, history, runner, triage, playbooks, Grok client with the fake, redaction, scheduler.
2. **Replay tests:** every fixture in `tests/fixtures/events/` and `lab/replay/` gets the right fingerprint and severity.
3. **Red-team suite:** the hostile fake Grok tries every attempt in the DEMO.md table. Pass means zero changes on disk or settings, every attempt refused, and every refusal in history.
4. **Integration on the VM, by hand once:** walk through S1, S2, S4, S6, S10, and S11 as a person would, with the real admin prompt. Note anything confusing in the wording.
5. **Automated VM runs:** `run_scenarios.py --repeat 10` for every scenario that's built. Any scenario under 9 out of 10 gets a bug ticket before demo prep.
6. **Offline test:** block `api.x.ai` in the VM's hosts file. S1, S2, S4, S5, and S6 must still pass with zero Grok calls.
7. **Edge checks:**
   - run as a standard user with no admin password: admin fixes say "This needs an administrator" before the button
   - System Protection off: the card says so before the button
   - kill the app mid-repair: the "Interrupted repair" card appears at next launch
   - fullscreen video playing: a major popup waits
   - sleep through the end-of-day time: the list appears at wake
8. **Observe-only results:** collect the counts from teammates' laptops (popups, end-of-day items, problems found, Grok calls). If there is more than 1 major popup a day on a healthy laptop, tune the triage table and re-run step 2.
9. **Manual comparison:** a teammate who didn't build the scenario fixes S1, S2, S4, and S6 by hand with a stopwatch. Record minutes, windows opened, and clicks.
10. **Final scoreboard:** regenerate from the latest runs. Show the real numbers even where they miss the target.

**Gate:**
- the red-team suite shows 0 unapproved changes
- the must-have scenarios pass at least 9 out of 10
- the offline test passes
- the scoreboard is generated

## Phase 9: Demo prep (H22 to H24)

- **Freeze the code.** Only fix bugs that break the demo.
- **Rehearse the 5-minute script** from DEMO.md three times with a timer. One person drives, one talks.
- **Record a backup video** of the full demo.
- **Prepare the fallbacks:** phone hotspot, recorded Grok replay, the VM restored to the demo checkpoint.
- **Prepare the Q&A answers:** admin rights, wrong Grok answers, privacy, cost.
- Put the scoreboard and the architecture diagram from the README on the final slides.

## Must have vs nice to have

**Must have for judging:**
- safety core and fix history
- watcher and triage
- playbooks 1 to 3
- Grok for unfamiliar errors
- major popup and end-of-day list
- history page
- red-team suite
- scenarios S1, S2, S4, S6, S10, S11
- scoreboard

**Nice to have, cut in this order if late:**
1. S9 (disk-failing replay)
2. S7 (slow startup)
3. Windows Update reset
4. sound output switch
5. history export
6. S8 (protection off)
7. S5 (clock)
8. playbook 5 (print service restart; keep cancelling the job)
9. S3 (crash loop)
