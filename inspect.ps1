$ErrorActionPreference = 'Continue'
Write-Output "=== 360 dirs item counts ==="
Get-ChildItem "C:\" -ErrorAction SilentlyContinue | Where-Object { $_.Name -like "360*" } | ForEach-Object {
    $n = (Get-ChildItem $_.FullName -Recurse -ErrorAction SilentlyContinue | Measure-Object).Count
    "360DIR: $($_.Name) items=$n"
}
Write-Output "=== junk processes ==="
Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Name -match "360|leidian|ldplayer|todesk|oray|sunlogin|kugou" } | ForEach-Object { "PROC: $($_.Name) PID=$($_.Id)" }
Write-Output "DONE-INSPECT"
