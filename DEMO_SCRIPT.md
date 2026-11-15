# SystemAutoBot: 5-minute demo script

## Before you hit record (about 10 minutes)

1. **Start from a clean slate.** Quit the app from the tray. Rename `%LOCALAPPDATA%\SystemAutoBot` to `SystemAutoBot-old`, then start the app with `.venv\Scripts\python.exe -m systemautobot app --demo --lab`.
   - **Why:** the app limits how many times it fixes the same problem in one day, so rehearsals would use up the fixes you need on camera. A fresh folder also clears the leftover cards (disk failing, Volsnap, Cities.exe).
   - **Setup:** pick Groq and leave the key box empty, because your key is still saved. Press **Test and continue**, then **Start watching**.
2. **Open the demo panel:** `.venv\Scripts\python.exe lab\laptop_demo.py`. Put it on the left, with the app window on the right.
3. **Notifications:** turn off Do Not Disturb (Windows Settings → System → Notifications). Do one practice run of step 2 below to confirm the notification shows.
   - **If no notification appears:** right-click the tray icon → **Show what needs me**. That opens the same box, so the demo still works.
4. **Groq limits:** don't rehearse the think-mode step (step 3) in the minutes before recording. One run uses most of Groq's per-minute token limit.
5. **Second window:** have a terminal open in the repo, ready to run `pytest tests/test_redteam.py -q`.

## The script

| Time | Do | Say |
|---|---|---|
| **0:00–0:30** Hook | Show the app's Home: "Your PC is fine", the tiles, Recent activity. | "Most PC problems are the same handful: a full drive, no internet, a frozen program. SystemAutoBot lives in the tray, notices them, tells you exactly what the fix does, and writes down everything it changes. The AI can look and suggest, but it never touches your PC itself." |
| **0:30–1:30** Critical problem | Panel → **1 Freeze a program**. Click the test window so it's in front. Wait about 20 s. | "Here's a program freezing with unsaved work. I don't need to open anything…" |
|  | The notification appears. Click it: the small box opens. | "…Windows tells me. The box says in one sentence what the fix does, and warns me that unsaved work is lost. **Exactly what runs** shows the real steps." (Open it for a second.) |
|  | Press **Allow**. | "One click. It closed the program, reopened it, then checked again. It's fixed, and here's what changed." |
| **1:30–2:45** Think mode with the AI | Panel → **E Show an error message box**. On Home, open the new row → **Fix for me**. | "Now an error it doesn't recognize. There's no built-in fix, so it offers to ask my AI. Before anything is sent, I can see exactly what goes out." (Open **See exactly what I'll send** briefly.) |
|  | Press **Ask Groq**. Let the live steps run. | "Every check the AI runs shows up live. Its tools only read: free space, recent errors, frozen programs. It can't run a command." |
|  | The answer and a plan appear. Press **Don't**, or **Allow** if it's harmless. | "It explains the error and picks one repair from a fixed list. The app builds that repair itself, and it waits for me." |
| **2:45–3:45** Autopilot | Settings → **Autopilot** → switch on **Fix critical problems automatically** and **Also close frozen programs**. | "If you trust it, Autopilot fixes critical problems with no click, inside a harness. Only safe repairs, one try per problem a day, a few an hour, and it stops when fixes are paused." |
|  | Panel → **1 Freeze a program** again, with its window in front. Wait. | (While waiting) "Admin repairs still show the Windows prompt, unless you set up the no-prompt option yourself from an admin account. That's off by default and covers five quick repairs." |
|  | The "Fixed automatically" notification appears. Open **Activity**. | "Fixed with no click, and I was told right after. Activity marks it 'Autopilot, no click', with every step." |
| **3:45–4:30** Safety | Panel → **4 Malicious error message**. Open the row and its text. | "Attackers can write text into error messages. This one tries to order the AI to run commands. The app treats it as a quote: it never reaches a title, a button or a command." |
|  | Switch to the terminal: `pytest tests/test_redteam.py -q` → **19 passed**. | "We attack it ourselves: unknown tools, command injection, approvals without a click, key leaks. All 19 refused, and 226 tests in total." |
| **4:30–5:00** Close | Show Settings → AI (the provider list), then the website. | "Bring your own AI: Grok, OpenAI, Claude, Gemini, Groq and more. The key stays in Windows Credential Manager. Everything is free and open source. Download it from our site." |

## If something goes wrong on camera

- **No notification:** tray icon → **Show what needs me**. Say: "Here's the same box from the tray."
- **Groq says it hit a limit:** say "The AI is rate-limited, so it waits and tells me why. Nothing is hidden." Move on to Autopilot.
- **The frozen program shows as "Can wait":** it wasn't in front. Click its window and wait about 10 more seconds.
- **The box says "I already tried twice today":** you rehearsed too much. Repeat the clean-slate step from the setup list.
