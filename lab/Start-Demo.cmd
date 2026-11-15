@echo off
setlocal
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo Create the environment first. See DEMO.md, Quick start.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -c "import systemautobot, tkinter, fastapi, uvicorn, webview, pystray, psutil, win32api"
if errorlevel 1 (
  echo Demo dependencies are missing. See DEMO.md, Quick start.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m systemautobot app --demo --lab --show
".venv\Scripts\python.exe" lab\laptop_demo.py
