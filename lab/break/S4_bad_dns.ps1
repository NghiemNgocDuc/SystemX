# S4: DNS servers set by hand to an address that never answers (like a leftover VPN setting).
param([switch]$Undo)
. "$PSScriptRoot\_common.ps1"; Assert-LabVM
$nic = Get-NetAdapter | Where-Object Status -eq Up | Select-Object -First 1
if ($Undo) { Set-DnsClientServerAddress -InterfaceIndex $nic.ifIndex -ResetServerAddresses; Clear-DnsClientCache; exit }
$old = (Get-DnsClientServerAddress -InterfaceIndex $nic.ifIndex -AddressFamily IPv4).ServerAddresses
Set-DnsClientServerAddress -InterfaceIndex $nic.ifIndex -ServerAddresses "10.255.255.1"
Clear-DnsClientCache
Write-Break "S4" @{ adapter = $nic.Name; old_dns = ($old -join ","); new_dns = "10.255.255.1" }
