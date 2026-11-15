SystemAutoBot for Windows 10 and 11
===================================

Install
  1. Unzip this folder anywhere (Downloads is fine).
  2. Double-click Install.cmd. No administrator rights needed.
  3. The app opens. Pick an AI provider (xAI Grok, OpenAI, Anthropic Claude,
     Google Gemini, Groq, OpenRouter, or any OpenAI-compatible service), paste your
     API key, choose a model, and press Test and save.

Windows may say "Windows protected your PC" because this build is not code-signed yet.
Press More info, then Run anyway. You can read every line of the code on GitHub first.

What it does
  - Watches for common problems: a full drive, no internet, a wrong clock, a frozen program.
  - Big problems: a popup asks "Fix now?". Small ones wait for one list at the end of the day.
  - Nothing changes without your click. Repairs that need administrator rights show the
    normal Windows permission prompt every time.
  - Every fix is recorded in Activity: what ran, which files and settings it touched.

Your key is stored in Windows Credential Manager on this PC, never in a file.
Your fix history stays on this PC. Conversations go only to the AI provider you chose.

Uninstall
  Double-click Uninstall.cmd in %LOCALAPPDATA%\Programs\SystemAutoBot.
