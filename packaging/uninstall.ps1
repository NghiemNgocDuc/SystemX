# Removes SystemAutoBot for this Windows user.
$ErrorActionPreference = "Continue"
$dest = Join-Path $env:LOCALAPPDATA "Programs\SystemAutoBot"
$data = Join-Path $env:LOCALAPPDATA "SystemAutoBot"

Get-Process SystemAutoBot -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 500
Remove-Item (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\SystemAutoBot.lnk") -ErrorAction SilentlyContinue
Remove-Item (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup\SystemAutoBot.lnk") -ErrorAction SilentlyContinue

$keep = Read-Host "Keep your fix history and settings? (Y/n)"
if ($keep -match "^[nN]") { Remove-Item $data -Recurse -Force -ErrorAction SilentlyContinue }

$keys = Read-Host "Remove saved AI keys from Windows Credential Manager? (y/N)"
if ($keys -match "^[yY]") {
    foreach ($p in "xai", "openai", "anthropic", "gemini", "groq", "openrouter", "custom") {
        cmdkey /delete:"${p}_api_key@SystemAutoBot" 2>$null | Out-Null
    }
    Write-Host "If any key remains, open Credential Manager > Windows Credentials and remove entries named SystemAutoBot."
}

# The running script lives in the folder being removed, so delete it after this window closes.
Start-Process cmd.exe -ArgumentList "/c timeout /t 2 >nul & rmdir /s /q `"$dest`"" -WindowStyle Hidden
Write-Host "SystemAutoBot is removed."
