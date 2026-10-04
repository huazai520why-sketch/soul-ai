$ErrorActionPreference = 'Continue'
function ListOneLevel($path) {
    if (-not (Test-Path $path)) { "  (missing $path)"; return }
    Get-ChildItem $path -ErrorAction SilentlyContinue | ForEach-Object {
        if ($_.PSIsContainer) {
            try { $sz = (Get-ChildItem $_.FullName -Recurse -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum }
            catch { $sz = 0 }
            "  [DIR ] $($_.Name)  $([math]::Round($sz/1GB,2))GB"
        } else {
            "  [FILE] $($_.Name)  $([math]::Round($_.Length/1MB,1))MB"
        }
    }
}
Write-Output "===== C: TOP-LEVEL ====="
ListOneLevel "C:\"
Write-Output "===== C:\Program Files (1 level) ====="
ListOneLevel "C:\Program Files"
Write-Output "===== C:\Program Files (x86) (1 level) ====="
ListOneLevel "C:\Program Files (x86)"
Write-Output "===== C:\ProgramData (1 level) ====="
ListOneLevel "C:\ProgramData"
Write-Output "===== C:\Users (1 level) ====="
ListOneLevel "C:\Users"
Write-Output "===== D: FULL ====="
ListOneLevel "D:\"
Write-Output "===== E: FULL ====="
ListOneLevel "E:\"
Write-Output "DONE-SCAN"
