[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'SilentlyContinue'

"=== DISKS ==="
Get-Disk | ForEach-Object { "DISK#$($_.Number) SIZE=$([math]::Round($_.Size/1GB,1))GB OP=$($_.OperationalStatus) MODEL=$($_.FriendlyName)" }
"=== PARTITIONS ==="
Get-Partition | ForEach-Object { "DISK#$($_.DiskNumber) PART#$($_.PartitionNumber) LET=$($_.DriveLetter) SIZE=$([math]::Round($_.Size/1GB,1))GB TYPE=$($_.Type)" }

"=== PROGRAM FILES (x64) ==="
Get-ChildItem 'C:\Program Files' -Force | ForEach-Object { "PF: $($_.Name)" }
"=== PROGRAM FILES (x86) ==="
Get-ChildItem 'C:\Program Files (x86)' -Force | ForEach-Object { "PFX86: $($_.Name)" }
"=== PROGRAMDATA ==="
Get-ChildItem 'C:\ProgramData' -Force | ForEach-Object { "PD: $($_.Name)" }

"=== D: depth2 ==="
Get-ChildItem D:\ -Force -ErrorAction SilentlyContinue | ForEach-Object {
  "D1: $($_.Name)"
  if ($_.PSIsContainer) { Get-ChildItem $_.FullName -Force -ErrorAction SilentlyContinue | ForEach-Object { "  D2: $($_.Name)" } }
}
"=== E: depth2 ==="
Get-ChildItem E:\ -Force -ErrorAction SilentlyContinue | ForEach-Object {
  "E1: $($_.Name)"
  if ($_.PSIsContainer) { Get-ChildItem $_.FullName -Force -ErrorAction SilentlyContinue | ForEach-Object { "  E2: $($_.Name)" } }
}

"=== INSTALLED APP DISPLAYNAMES (registry) ==="
$keys = @('HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*','HKLM:\Software\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*')
foreach ($k in $keys) {
  Get-ItemProperty $k -ErrorAction SilentlyContinue | ForEach-Object { if ($_.DisplayName) { "APP: $($_.DisplayName)" } }
}
"DONE_INSPECT2"
