# SystemAutoBot: 5-minute demo script

## Before you hit record (about 10 minutes)

1. **Start from a clean slate.** Quit the app from the tray. Rename `%LOCALAPPDATA%\SystemAutoBot` to `SystemAutoBot-old`, then start the app with `.venv\Scripts\python.exe -m systemautobot app --demo --lab`.
   - **Why:** the app limits how many times it fixes the same problem in one day, so rehearsals would use up the fixes you need on camera. A fresh folder also clears the leftover cards (disk failing, Volsnap, Cities.exe).
   - **Setup:** pick **xAI Grok**, load models, and choose a tool-capable Grok model (the default suggestion is `grok-4.7`). Leave the key box empty only if your xAI key is already saved; otherwise enter it and press **Test and continue**. Groq and xAI Grok are different providers, and their keys are not interchangeable.
2. **Open the demo panel:** `.venv\Scripts\python.exe lab\laptop_demo.py`. Put it on the left, with the app window on the right.
3. **Notifications:** turn off Do Not Disturb (Windows Settings → System → Notifications). Do one practice run of step 2 below to confirm the notification shows.
   - **If no notification appears:** right-click the tray icon → **Show what needs me**. That opens the same box, so the demo still works.
4. **Grok API:** confirm the xAI key and model with **Test and continue** before recording. Think mode uses xAI API credits and may be rate-limited, so avoid repeated rehearsals.
5. **Second window:** have a terminal open in the repo, ready to run `pytest tests/test_redteam.py -q`.

## The script

| Time | Do | Say |
|---|---|---|
| **0:00–0:30** Hook | Show the app's Home: "Your PC is fine", the tiles, Recent activity. | "Most PC problems are the same handful: a full drive, no internet, a frozen program. SystemAutoBot lives in the tray, notices them, tells you exactly what the fix does, and writes down everything it changes. The AI can look and suggest, but it never touches your PC itself." |
| **0:30–1:30** Critical problem | Panel → **1 Freeze a program**. Click the test window so it's in front. Wait about 20 s. | "Here's a program freezing with unsaved work. I don't need to open anything…" |
|  | The notification appears. Click it: the small box opens. | "…Windows tells me. The box says in one sentence what the fix does, and warns me that unsaved work is lost. **Exactly what runs** shows the real steps." (Open it for a second.) |
|  | Press **Allow**. | "One click. It closed the program, reopened it, then checked again. It's fixed, and here's what changed." |
| **1:30–2:45** Think mode with xAI Grok | Panel → **E Show an error message box**. On Home, open the new row → **Fix for me**. | "Now an error it doesn't recognize. There's no built-in fix, so it offers to ask xAI Grok. Before anything is sent, I can inspect exactly what goes out." (Open **See exactly what I'll send** briefly.) |
|  | Press **Ask xAI Grok**. Show the live response summary, if provided, and the checks as they run. | "Grok is looking at the error with read-only checks. You can see what it checks and what the PC reports. It can't run a command." |
|  | When Grok proposes a plan, review it and press **Don't**. | "Grok can suggest one repair from the fixed list. The app builds the plan and asks me separately. Nothing runs unless I approve it." |
| **2:45–3:45** Autopilot | Settings → **Autopilot** → switch on **Fix critical problems automatically** and **Also close frozen programs**. | "If you trust it, Autopilot fixes critical problems with no click, inside a harness. Only safe repairs, one try per problem a day, a few an hour, and it stops when fixes are paused." |
|  | Panel → **1 Freeze a program** again, with its window in front. Wait. | (While waiting) "Admin repairs still show the Windows prompt, unless you set up the no-prompt option yourself from an admin account. That's off by default and covers five quick repairs." |
|  | The "Fixed automatically" notification appears. Open **Activity**. | "Fixed with no click, and I was told right after. Activity marks it 'Autopilot, no click', with every step." |
| **3:45–4:30** Safety | Panel → **4 Malicious error message**. Open the row and its text. | "Attackers can write text into error messages. This one tries to order the AI to run commands. The app treats it as a quote: it never reaches a title, a button or a command." |
|  | Switch to the terminal: `pytest tests/test_redteam.py -q` → **19 passed**. | "We attack it ourselves: unknown tools, command injection, approvals without a click, key leaks. All 19 refused, and 226 tests in total." |
| **4:30–5:00** Close | Show Settings → AI (the provider list), then the website. | "SystemAutoBot supports xAI Grok and other providers. The key stays in Windows Credential Manager. The app is free and open source; xAI may charge for API use. Download it from our site." |

## If something goes wrong on camera

- **No notification:** tray icon → **Show what needs me**. Say: "Here's the same box from the tray."
- **Grok is rate-limited or unavailable:** show its actual message, say "The AI is unavailable, so nothing was changed," and move on to Autopilot. Don't claim a live diagnosis if it didn't happen.
- **The frozen program shows as "Can wait":** it wasn't in front. Click its window and wait about 10 more seconds.
- **The box says "I already tried twice today":** you rehearsed too much. Repeat the clean-slate step from the setup list.
