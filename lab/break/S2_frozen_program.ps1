# S2: a program freezes in front of the person, with unsaved text.
param([switch]$Undo)
. "$PSScriptRoot\_common.ps1"; Assert-LabVM
if ($Undo) { Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object CommandLine -like "*hangme*" | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; exit }
$py = Find-Python
$p = Start-Process $py -ArgumentList "`"$LabRoot\apps\hangme.py`" --freeze" -PassThru
Write-Break "S2" @{ pid = $p.Id; app = "hangme" }
