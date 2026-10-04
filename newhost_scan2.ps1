$ErrorActionPreference = 'Continue'
function ListNames($path) {
    if (-not (Test-Path $path)) { "  (missing)" ; return }
    Get-ChildItem $path -ErrorAction SilentlyContinue | ForEach-Object {
        if ($_.PSIsContainer) { "  [DIR ] $($_.Name)" } else { "  [FILE] $($_.Name)" }
    }
}
Write-Output "===== C:\Program Files ====="
ListNames "C:\Program Files"
Write-Output "===== C:\Program Files (x86) ====="
ListNames "C:\Program Files (x86)"
Write-Output "===== C:\ProgramData ====="
ListNames "C:\ProgramData"
Write-Output "===== C:\Users ====="
ListNames "C:\Users"
Write-Output "===== D:\ ====="
ListNames "D:\"
Write-Output "===== E:\ ====="
ListNames "E:\"
Write-Output "DONE-SCAN2"
