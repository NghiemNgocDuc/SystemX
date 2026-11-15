# S5: time sync off and the clock set back two years, so secure websites fail.
param([switch]$Undo)
. "$PSScriptRoot\_common.ps1"; Assert-LabVM
if ($Undo) { Set-Service w32time -StartupType Automatic; Start-Service w32time; w32tm /resync /force | Out-Null; exit }
Stop-Service w32time -Force
Set-Service w32time -StartupType Disabled
$before = Get-Date
Set-Date -Date $before.AddYears(-2) | Out-Null
Write-Break "S5" @{ real_time = $before.ToString("o"); set_to = (Get-Date).ToString("o") }
