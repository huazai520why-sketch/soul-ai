$ErrorActionPreference = 'Stop'
$log = 'D:\hub_sc.log'
function Log($m){ "$(Get-Date -Format 'HH:mm:ss') $m" | Tee-Object -Append -FilePath $log }
Log '=== Hub shortcuts / autostart check ==='
$sm = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs"
Log "Start Menu root: $sm"
Get-ChildItem $sm -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.Name -like '*OpenClaw*' -or $_.Name -like '*Companion*' -or $_.Name -like '*Molty*' } | ForEach-Object { Log "SHORTCUT: $($_.FullName)" }
$tray = "$env:APPDATA\OpenClawTray"
if (Test-Path $tray) { Log "OpenClawTray settings dir EXISTS: $tray"; Get-ChildItem $tray -Recurse -ErrorAction SilentlyContinue | ForEach-Object { Log "  $($_.FullName)" } } else { Log "OpenClawTray settings dir NOT present yet (created on first launch)" }
$rk = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -ErrorAction SilentlyContinue
if ($rk) { $rk.PSObject.Properties | Where-Object { $_.Name -like '*OpenClaw*' -or $_.Name -like '*Molty*' -or $_.Name -like '*Claw*' } | ForEach-Object { Log "RUN KEY: $($_.Name) = $($_.Value)" } }
Log '=== done ==='
