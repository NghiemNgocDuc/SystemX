# S7: six startup entries that keep the CPU busy for a minute after sign-in.
param([switch]$Undo)
. "$PSScriptRoot\_common.ps1"; Assert-LabVM
$key = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
if ($Undo) { 1..6 | ForEach-Object { Remove-ItemProperty $key -Name "SABLabSlow$_" -ErrorAction SilentlyContinue }; exit }
$cmd = 'powershell.exe -NoProfile -WindowStyle Hidden -Command "$e=(Get-Date).AddSeconds(60); while((Get-Date) -lt $e){}"'
1..6 | ForEach-Object { Set-ItemProperty $key -Name "SABLabSlow$_" -Value $cmd }
Write-Break "S7" @{ run_key = $key; entries = 6; note = "Sign out and back in to feel it." }
