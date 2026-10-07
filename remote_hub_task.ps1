$ErrorActionPreference = 'Stop'
$log = 'D:\hub_task.log'
function Log($m){ "$(Get-Date -Format 'HH:mm:ss') $m" | Tee-Object -Append -FilePath $log }
$action = 'powershell -ExecutionPolicy Bypass -File D:\remote_hub_install.ps1'
# ⭐ 2026-10-07 安全整改：**不再把主机密码写进本文件**（旧版明文密码已随仓库公开，须轮换）。
#   跑之前先设：  $env:NEWHOST_PASS = '<新密码>'
$rp = $env:NEWHOST_PASS
if (-not $rp) { throw 'NEWHOST_PASS 未设置。请先 $env:NEWHOST_PASS="<主机密码>" 再运行（勿写入本文件）' }
Log 'Creating scheduled task HubInstall as JIANG (run whether logged on or not)'
schtasks /create /tn HubInstall /ru JIANG /rp $rp /sc once /st 00:00 /tr $action /f
Log 'Launching task'
schtasks /run /tn HubInstall
Start-Sleep -Seconds 2
schtasks /query /tn HubInstall /fo LIST | Out-String | ForEach-Object { Log $_ }
Log 'Task launched; install progress at D:\hub_install.log'
