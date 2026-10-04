$ErrorActionPreference = 'Stop'
$log = 'D:\hub_task.log'
function Log($m){ "$(Get-Date -Format 'HH:mm:ss') $m" | Tee-Object -Append -FilePath $log }
$action = 'powershell -ExecutionPolicy Bypass -File D:\remote_hub_install.ps1'
Log 'Creating scheduled task HubInstall as JIANG (run whether logged on or not)'
schtasks /create /tn HubInstall /ru JIANG /rp 123456 /sc once /st 00:00 /tr $action /f
Log 'Launching task'
schtasks /run /tn HubInstall
Start-Sleep -Seconds 2
schtasks /query /tn HubInstall /fo LIST | Out-String | ForEach-Object { Log $_ }
Log 'Task launched; install progress at D:\hub_install.log'
