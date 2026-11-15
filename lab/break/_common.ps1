# Shared by every breaker. VM ONLY: these scripts break the network, the clock, and Windows Security.
$ErrorActionPreference = "Stop"
$LabRoot = Split-Path -Parent $PSScriptRoot
$Results = Join-Path $LabRoot "results"
New-Item -ItemType Directory -Force $Results | Out-Null

function Assert-LabVM {
    if (-not (Test-Path "C:\sab-lab\THIS_IS_A_LAB_VM")) {
        throw "Refusing to run: C:\sab-lab\THIS_IS_A_LAB_VM is missing. Breakers only run in the lab VM (see lab\vm_setup.ps1)."
    }
}

function Write-Break([string]$Id, [hashtable]$Changed) {
    $record = @{ scenario = $Id; at = (Get-Date).ToString("o"); changed = $Changed }
    $record | ConvertTo-Json -Depth 5 | Set-Content -Encoding utf8 (Join-Path $Results "break-$Id.json")
    Write-Host "[$Id] broke: $($Changed | ConvertTo-Json -Compress)"
}

function Find-Python {
    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) { throw "Python is needed for the lab test apps (lab\vm_setup.ps1 installs it)." }
    return $py.Source
}
