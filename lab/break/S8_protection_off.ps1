# S8: Windows Security real-time protection turned off (needs Tamper Protection off in the VM).
param([switch]$Undo)
. "$PSScriptRoot\_common.ps1"; Assert-LabVM
if ($Undo) { Set-MpPreference -DisableRealtimeMonitoring $false; exit }
Set-MpPreference -DisableRealtimeMonitoring $true
Write-Break "S8" @{ realtime_protection = "off" }
