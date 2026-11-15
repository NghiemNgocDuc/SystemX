# S3: an app keeps crashing because its cache is corrupt. Three native crashes -> event 1000 x3.
param([switch]$Undo)
. "$PSScriptRoot\_common.ps1"; Assert-LabVM
if ($Undo) { Remove-Item "$env:LOCALAPPDATA\Crashy" -Recurse -Force -ErrorAction SilentlyContinue; exit }
$py = Find-Python
& $py "$LabRoot\apps\crashy.py" --corrupt
for ($i = 0; $i -lt 3; $i++) { Start-Process $py -ArgumentList "`"$LabRoot\apps\crashy.py`"" -Wait; Start-Sleep 2 }
Write-Break "S3" @{ cache = "$env:LOCALAPPDATA\Crashy\cache\state.json"; crashes = 3 }
