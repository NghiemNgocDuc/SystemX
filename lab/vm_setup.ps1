# One-time setup INSIDE the Windows 11 lab VM. Run as the VM's admin account.
#   powershell -ExecutionPolicy Bypass -File lab\vm_setup.ps1 [-Automated]
# -Automated: admin prompts approve without asking, for unattended scenario runs.
#             The live demo VM must NOT use this, so judges see the real Windows prompt.
param([switch]$Automated)
$ErrorActionPreference = "Stop"

New-Item -ItemType Directory -Force C:\sab-lab | Out-Null
Set-Content C:\sab-lab\THIS_IS_A_LAB_VM "Breakers may run here."

# Python for the lab test apps and the scenario runner.
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    winget install --id Python.Python.3.11 -e --silent --accept-package-agreements --accept-source-agreements
}

# System Protection on, so admin repairs can make restore points.
Enable-ComputerRestore -Drive "C:\"

if ($Automated) {
    Set-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System" ConsentPromptBehaviorAdmin 0
    Write-Host "Admin prompts now approve without asking (automated VM only)."
}

Write-Host "Done. Turn off Tamper Protection by hand (Windows Security > Virus & threat protection settings) for S8."
Write-Host "Then install SystemAutoBot, start it with --demo --lab, and take the clean snapshot."
