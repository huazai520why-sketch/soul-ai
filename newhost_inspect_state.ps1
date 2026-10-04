[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'SilentlyContinue'

# 1) Kill any stale cleanup processes from the interrupted run
$stale = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object { $_.CommandLine -like '*cleanup*' -or $_.CommandLine -like '*newhost_clean*' }
if ($stale) {
    foreach ($p in $stale) { $p.Terminate() | Out-Null; "KILLED_STALE_PID=$($p.ProcessId)" }
} else {
    "NO_STALE_CLEANUP_RUNNING"
}

# 2) All volumes with drive letters + free space
"=== VOLUMES ==="
Get-Volume | Where-Object { $_.DriveLetter } | ForEach-Object {
    $free = [math]::Round($_.SizeRemaining/1GB,1)
    $tot  = [math]::Round($_.Size/1GB,1)
    "DRIVE $($_.DriveLetter): TOTAL=${tot}GB FREE=${free}GB FS=$($_.FileSystem) LABEL=$($_.FileSystemLabel)"
}

# 3) SSH channel integrity (must keep)
"=== SSH CHANNEL ==="
if (Test-Path 'C:\ProgramData\ssh') { "ssh_dir=PRESENT" } else { "ssh_dir=MISSING!!" }
if (Test-Path 'C:\ProgramData\ssh\administrators_authorized_keys') { "authkeys=PRESENT" } else { "authkeys=MISSING!!" }

# 4) wbadmin account
"=== ACCOUNTS ==="
Get-LocalUser | ForEach-Object { "USER=$($_.Name) ENABLED=$($_.Enabled)" }

# 5) C: root top-level (non-system we may delete)
"=== C: ROOT ==="
Get-ChildItem C:\ -Force | ForEach-Object { "CROOT: $($_.Name)" }

# 6) D: root (must keep 无界趣连)
"=== D: ROOT ==="
Get-ChildItem D:\ -Force | ForEach-Object { "DROOT: $($_.Name)" }

# 7) Junk processes still alive?
"=== JUNK PROCS ==="
Get-Process | Where-Object { $_.Name -match '360|leidian|ldplayer|todesk|oray|sunlogin|kugou|tencent' } | ForEach-Object { "JUNKPROC: $($_.Name) PID=$($_.Id)" }
"DONE_INSPECT"
