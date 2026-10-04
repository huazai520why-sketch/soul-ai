$ErrorActionPreference = 'Continue'
Write-Output "=== OS VERSION ==="
$reg = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion"
"ProductName   : $($reg.ProductName)"
"EditionID     : $($reg.EditionID)"
"DisplayVersion: $($reg.DisplayVersion)"
"Build         : $($reg.CurrentBuild)"

Write-Output "=== HYPER-V FEATURE ==="
try {
    $hv = Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All -ErrorAction Stop
    "Hyper-V State : $($hv.State)"
    "RestartNeeded : $($hv.RestartNeeded)"
} catch { "Hyper-V query failed: $_" }

Write-Output "=== OPENSSH SERVICE ==="
try {
    $s = Get-Service sshd -ErrorAction Stop
    "sshd: $($s.Status) (Startup=$($s.StartType))"
} catch { "sshd query failed: $_" }

Write-Output "=== WHOAMI ==="
whoami

Write-Output "=== DISK C: ==="
$d = Get-PSDrive C
"Free: $([math]::Round($d.Free/1GB,1)) GB / Total: $([math]::Round(($d.Used+$d.Free)/1GB,1)) GB"

Write-Output "=== PYTHON ==="
$py = Get-Command python -ErrorAction SilentlyContinue
if ($py) { "python: $($py.Source) -> $(& python --version 2>&1)" } else { "python: NOT FOUND" }

Write-Output "=== HOST/NET ==="
"Hostname: $(hostname)"
Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.InterfaceAlias -notmatch 'Loopback' } | ForEach-Object { "  IP: $($_.IPAddress) ($($_.InterfaceAlias))" }

Write-Output "DONE-INVENTORY"
