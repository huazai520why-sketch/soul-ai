[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'SilentlyContinue'
$log = 'C:\Users\wbadmin\cleanup5_log.txt'
"START $(Get-Date)" | Out-File $log -Encoding utf8

function Del($p) {
  if (Test-Path -LiteralPath $p) {
    try {
      Remove-Item -LiteralPath $p -Recurse -Force -ErrorAction Stop
      "DELETED: $p" | Tee-Object -Append $log
    } catch {
      "FAIL: $p -> $($_.Exception.Message)" | Tee-Object -Append $log
    }
  } else {
    "SKIP_MISSING: $p" | Tee-Object -Append $log
  }
}

"=== PHASE 1: non-360 junk ==="
Del 'C:\DaBaiCai'
Del 'C:\ESD'
Del 'C:\KuGou'
Del 'C:\leidian'
Del 'C:\AMTAG.BIN'
Get-ChildItem C:\ -Force -Filter 'VC_RED*' | ForEach-Object { Del $_.FullName }
if (Test-Path -LiteralPath 'C:\install.exe') { Del 'C:\install.exe' }
Del 'C:\$WINDOWS.~BT'
Del 'C:\$Windows.~WS'
Del 'C:\$WinREAgent'
Del 'C:\Windows10Upgrade'

Del 'C:\Program Files\ldplayer9box'
Del 'C:\Program Files\Oray'
Del 'C:\Program Files\Tencent'
Del 'C:\Program Files\ToDesk'
Del 'C:\Program Files (x86)\360'
Del 'C:\Program Files (x86)\KuGou'

Del 'C:\ProgramData\360safe'
Del 'C:\ProgramData\360SD'
Del 'C:\ProgramData\deepscan'
Del 'C:\ProgramData\KuGou'
Del 'C:\ProgramData\Oray'
Del 'C:\ProgramData\OrayClient'
Del 'C:\ProgramData\Tencent'
Del 'C:\ProgramData\tianxiang'
Del 'C:\ProgramData\Windows Master Store'
Del 'C:\ProgramData\ZulerCoreTools'
Del 'C:\ProgramData\leidian'
Del 'C:\ProgramData\NeteaseWinDev'

Del 'C:\Users\JIAN'
try {
  $up = Get-CimInstance Win32_UserProfile -ErrorAction SilentlyContinue | Where-Object { $_.LocalPath -like '*JIANG' }
  if ($up) { $up | Remove-CimInstance -ErrorAction SilentlyContinue; "REMOVED_PROFILE_JIANG" | Tee-Object -Append $log }
} catch { "PROFILE_REG_FAIL: $_" | Tee-Object -Append $log }

"=== PHASE 2: uninstall 360 via its own uninstallers ==="
$keys = @('HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*','HKLM:\Software\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*')
$apps = Get-ItemProperty $keys -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -like '*360*' -and $_.UninstallString }
foreach ($a in $apps) {
  $us = $a.UninstallString
  "360_APP: $($a.DisplayName) | $us" | Tee-Object -Append $log
  if ($us -match 'msiexec') {
    $cmd = $us -replace '/I','/X' -replace '/i','/x'
    if ($cmd -notmatch '/qn') { $cmd += ' /qn /norestart' }
  } else {
    $cmd = $us
    if ($cmd -notmatch '/S') { $cmd += ' /S' }
  }
  "360_RUN: $cmd" | Tee-Object -Append $log
  try {
    $p = Start-Process cmd.exe -ArgumentList "/c $cmd" -Wait -PassThru -Timeout 120 -ErrorAction Stop
    "360_UNINSTALL_EXIT: $($p.ExitCode)" | Tee-Object -Append $log
  } catch {
    "360_UNINSTALL_TIMEOUT: $_" | Tee-Object -Append $log
  }
}

"=== PHASE 3: retry delete 360 dirs (timeout-guarded) ==="
$kill = 'C:\Users\wbadmin\kill360dirs.ps1'
"Get-ChildItem C:\ -Force -Filter '360*' -ErrorAction SilentlyContinue | ForEach-Object { try { Remove-Item `$_.FullName -Recurse -Force -ErrorAction Stop; 'DELETED '+`$_.FullName } catch { 'FAIL '+`$_.FullName+': '+`$_.Exception.Message } }" | Set-Content $kill -Encoding ascii
try {
  $p2 = Start-Process powershell.exe -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File $kill" -Wait -PassThru -Timeout 30 -ErrorAction Stop
  "KILL360_EXIT: $($p2.ExitCode)" | Tee-Object -Append $log
} catch {
  "KILL360_TIMEOUT: 360 dirs still protected by kernel filter -> $_" | Tee-Object -Append $log
}

"END $(Get-Date)" | Tee-Object -Append $log
