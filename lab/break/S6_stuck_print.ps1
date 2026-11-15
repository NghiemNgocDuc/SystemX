# S6: a print job stuck on a printer whose address can't be reached (192.0.2.1 is reserved for tests).
param([switch]$Undo)
. "$PSScriptRoot\_common.ps1"; Assert-LabVM
$name = "SAB Lab Printer"
if ($Undo) { Get-PrintJob -PrinterName $name -ErrorAction SilentlyContinue | Remove-PrintJob; Remove-Printer $name -ErrorAction SilentlyContinue; Remove-PrinterPort "SABLab_192.0.2.1" -ErrorAction SilentlyContinue; exit }
if (-not (Get-PrinterPort "SABLab_192.0.2.1" -ErrorAction SilentlyContinue)) { Add-PrinterPort -Name "SABLab_192.0.2.1" -PrinterHostAddress "192.0.2.1" }
if (-not (Get-Printer $name -ErrorAction SilentlyContinue)) { Add-Printer -Name $name -DriverName "Microsoft Print To PDF" -PortName "SABLab_192.0.2.1" }
"SystemAutoBot lab test page" | Out-Printer -Name $name
Write-Break "S6" @{ printer = $name; port = "192.0.2.1" }
