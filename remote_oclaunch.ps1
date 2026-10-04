$log='D:\oclaunch.log'
schtasks /delete /tn "OCInstall" /f 2>&1 | Out-Null
schtasks /create /tn "OCInstall" /tr "powershell -NoProfile -ExecutionPolicy Bypass -File D:\remote_ocinstall.ps1" /sc once /st 00:00 /ru SYSTEM /rl highest /f 2>&1 | Set-Content $log
schtasks /run /tn "OCInstall" 2>&1 | Add-Content $log
Get-Content $log
