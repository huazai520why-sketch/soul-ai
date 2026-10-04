$ErrorActionPreference = 'Continue'
Write-Output "=== ALL DISKS ==="
Get-Disk | ForEach-Object {
    "Disk $($_.Number): $($_.FriendlyName) | Size=$([math]::Round($_.Size/1GB,1))GB | PartStyle=$($_.PartitionStyle) | Online=$($_.IsOffline)"
}
Write-Output "=== ALL PARTITIONS/VOLUMES ==="
Get-Volume | Where-Object { $_.DriveLetter } | ForEach-Object {
    "Drive $($_.DriveLetter): Size=$([math]::Round($_.Size/1GB,1))GB Free=$([math]::Round($_.SizeRemaining/1GB,1))GB FS=$($_.FileSystem)"
}
Write-Output "=== LARGEST DIRS ON C: (top-level) ==="
Get-ChildItem C:\ -ErrorAction SilentlyContinue | ForEach-Object {
    if ($_.PSIsContainer) {
        try { $sz = (Get-ChildItem $_.FullName -Recurse -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum }
        catch { $sz = 0 }
        "  $($_.Name): $([math]::Round($sz/1GB,2))GB"
    } else {
        "  $($_.Name): $([math]::Round($_.Length/1MB,1))MB"
    }
}
Write-Output "DONE-DISKS"
