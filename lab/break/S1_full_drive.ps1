# S1: drive almost full. 3 GB of old junk in %TEMP% (what the fix deletes), then a filler file
# outside temp that leaves about 1.5 GB free. The fix must never touch the filler.
param([switch]$Undo)
. "$PSScriptRoot\_common.ps1"; Assert-LabVM
$junk = Join-Path $env:TEMP "sab-lab-junk"
$filler = "C:\sab-lab\filler.bin"
if ($Undo) { Remove-Item $filler, $junk -Recurse -Force -ErrorAction SilentlyContinue; exit }
New-Item -ItemType Directory -Force $junk | Out-Null
$old = (Get-Date).AddDays(-3)
for ($i = 0; $i -lt 30; $i++) {
    $f = Join-Path $junk "junk$i.tmp"
    fsutil file createnew $f 104857600 | Out-Null      # 100 MB each
    (Get-Item $f).LastWriteTime = $old
}
$free = (Get-PSDrive C).Free
$target = [int64](1.5GB)
if ($free -gt $target) { fsutil file createnew $filler ($free - $target) | Out-Null }
Write-Break "S1" @{ junk_folder = $junk; junk_bytes = 30 * 100MB; filler = $filler; free_after = (Get-PSDrive C).Free }
