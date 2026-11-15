# Runs on the HOST: restore the clean VirtualBox snapshot, start the VM, run one scenario
# inside it, and copy the results out. Each run starts from the same clean state.
#   powershell -File lab\host\run_in_vm.ps1 -Vm "SAB-Lab" -User labadmin -Password <pw> -Scenario S1 -Repeat 10
param(
  [Parameter(Mandatory)] [string]$Vm,
  [string]$Snapshot = "clean",
  [Parameter(Mandatory)] [string]$User,
  [Parameter(Mandatory)] [string]$Password,
  [Parameter(Mandatory)] [string]$Scenario,
  [int]$Repeat = 10,
  [string]$GuestRepo = "C:\sab-lab\SystemX",
  [string]$Out = "$PSScriptRoot\..\results"
)
$ErrorActionPreference = "Stop"
$vbox = "$env:ProgramFiles\Oracle\VirtualBox\VBoxManage.exe"
New-Item -ItemType Directory -Force $Out | Out-Null
for ($i = 1; $i -le $Repeat; $i++) {
  & $vbox controlvm $Vm poweroff 2>$null
  & $vbox snapshot $Vm restore $Snapshot
  & $vbox startvm $Vm --type headless
  & $vbox guestproperty wait $Vm "/VirtualBox/GuestInfo/OS/LoggedInUsers" --timeout 300000 | Out-Null
  Start-Sleep 20   # SystemAutoBot starts from the Startup folder with --demo --lab
  & $vbox guestcontrol $Vm run --username $User --password $Password --exe "C:\Windows\System32\cmd.exe" -- `
      cmd /c "cd /d $GuestRepo && python lab\run_scenarios.py --only $Scenario"
  & $vbox guestcontrol $Vm copyfrom --username $User --password $Password --target-directory $Out "$GuestRepo\lab\results\"
  Write-Host "Run $i/$Repeat of $Scenario done."
}
& $vbox controlvm $Vm poweroff
python "$PSScriptRoot\..\scoreboard.py" $Out
