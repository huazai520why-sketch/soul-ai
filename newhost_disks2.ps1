$ErrorActionPreference = 'Continue'
Write-Output "=== ALL DISKS ==="
Get-Disk | ForEach-Object {
    "Disk $($_.Number): $($_.FriendlyName) | Size=$([math]::Round($_.Size/1GB,1))GB | PartStyle=$($_.PartitionStyle) | Offline=$($_.IsOffline)"
}
Write-Output "=== ALL VOLUMES ==="
Get-Volume | Where-Object { $_.DriveLetter } | ForEach-Object {
    "Drive $($_.DriveLetter): Size=$([math]::Round($_.Size/1GB,1))GB Free=$([math]::Round($_.SizeRemaining/1GB,1))GB FS=$($_.FileSystem)"
}
Write-Output "=== TOP-LEVEL SIZES ON C: (non-recursive of Win dirs) ==="
Get-ChildItem C:\ -ErrorAction SilentlyContinue | ForEach-Object {
    if ($_.PSIsContainer) {
        "  [DIR] $($_.Name)"
    } else {
        "  [FILE] $($_.Name): $([math]::Round($_.Length/1MB,1))MB"
    }
}
Write-Output "DONE-DISKS2"
