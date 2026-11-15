# Installs SystemAutoBot for this Windows user. No administrator rights needed.
$ErrorActionPreference = "Stop"
$src = $PSScriptRoot
$dest = Join-Path $env:LOCALAPPDATA "Programs\SystemAutoBot"

Get-Process SystemAutoBot -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 500

New-Item -ItemType Directory -Force $dest | Out-Null
Copy-Item -Path (Join-Path $src "*") -Destination $dest -Recurse -Force

$exe = Join-Path $dest "SystemAutoBot.exe"
$shell = New-Object -ComObject WScript.Shell
$startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\SystemAutoBot.lnk"
$startup = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup\SystemAutoBot.lnk"
foreach ($path in @($startMenu, $startup)) {
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = $exe
    $lnk.WorkingDirectory = $dest
    $lnk.Description = "SystemAutoBot keeps an eye on this PC"
    $lnk.Save()
}

Write-Host "Installed to $dest"
Write-Host "It starts when you sign in. Look for the blue icon in the tray, or press Ctrl+Alt+Space."
Start-Process $exe -ArgumentList "--show"
