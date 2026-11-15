# Builds dist\SystemAutoBot-windows.zip: the app folder, the admin helper, and the installer.
# Run from the repo root:  powershell -ExecutionPolicy Bypass -File packaging\build.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }
& $py -m pip install -q -e ".[grok,ui,dev]"
if ($LASTEXITCODE) { throw "pip install failed" }

New-Item -ItemType Directory -Force build | Out-Null
& $py -c "from systemautobot.ui.tray import icon_image; icon_image('app').save('build/icon.ico', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64)])"

# The app: one folder (starts faster than one-file), no console window.
& $py -m PyInstaller --noconfirm --clean --windowed --name SystemAutoBot --icon build\icon.ico `
  --paths src `
  --add-data "src\systemautobot\web\static;systemautobot\web\static" `
  --collect-all webview --collect-submodules uvicorn --collect-submodules keyring `
  --hidden-import pystray._win32 --hidden-import win32timezone `
  packaging\entry_app.py
if ($LASTEXITCODE) { throw "PyInstaller (app) failed" }

# The admin helper: one small self-contained file next to the app.
& $py -m PyInstaller --noconfirm --clean --onefile --noconsole --name sab-helper --icon build\icon.ico `
  src\sab_helper\helper.py
if ($LASTEXITCODE) { throw "PyInstaller (helper) failed" }

$app = "dist\SystemAutoBot"
Copy-Item dist\sab-helper.exe $app -Force
Copy-Item packaging\install.ps1, packaging\uninstall.ps1, packaging\Install.cmd, packaging\Uninstall.cmd $app -Force
Copy-Item packaging\README-FIRST.txt $app -Force

$zip = "dist\SystemAutoBot-windows.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path "$app\*" -DestinationPath $zip
$size = [math]::Round((Get-Item $zip).Length / 1MB, 1)
Write-Host "Built $zip ($size MB)"
