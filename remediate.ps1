[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'SilentlyContinue'
$log = 'C:\Users\wbadmin\remediate_log.txt'
"START $(Get-Date)" | Out-File $log -Encoding utf8

# kill any previous hung cleanup
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object { $_.CommandLine -like '*cleanup*' } | ForEach-Object { $_.Terminate() | Out-Null; "KILLED_HUNG $($_.ProcessId)" | Tee-Object -Append $log }

function KillDel($path) {
  if (-not (Test-Path -LiteralPath $path)) { "SKIP_MISSING: $path" | Tee-Object -Append $log; return }
  try {
    $p = Start-Process cmd.exe -ArgumentList "/c rd /s /q `"$path`"" -Wait -PassThru -Timeout 40 -ErrorAction Stop
    if (-not (Test-Path -LiteralPath $path)) {
      "DELETED: $path" | Tee-Object -Append $log
    } else {
      "STILL_THERE: $path (rd exit=$($p.ExitCode))" | Tee-Object -Append $log
    }
  } catch {
    "TIMEOUT_OR_FAIL: $path -> $_" | Tee-Object -Append $log
  }
}

"=== non-360 junk ==="
KillDel 'C:\DaBaiCai'
KillDel 'C:\ESD'
KillDel 'C:\KuGou'
KillDel 'C:\leidian'
KillDel 'C:\AMTAG.BIN'
KillDel 'C:\Windows10Upgrade'
KillDel 'C:\$WINDOWS.~BT'
KillDel 'C:\$Windows.~WS'
KillDel 'C:\$WinREAgent'
KillDel 'C:\Program Files\ldplayer9box'
KillDel 'C:\Program Files\Oray'
KillDel 'C:\Program Files\Tencent'
KillDel 'C:\Program Files\ToDesk'
KillDel 'C:\Program Files (x86)\360'
KillDel 'C:\Program Files (x86)\KuGou'
KillDel 'C:\ProgramData\360safe'
KillDel 'C:\ProgramData\360SD'
KillDel 'C:\ProgramData\deepscan'
KillDel 'C:\ProgramData\KuGou'
KillDel 'C:\ProgramData\Oray'
KillDel 'C:\ProgramData\OrayClient'
KillDel 'C:\ProgramData\Tencent'
KillDel 'C:\ProgramData\tianxiang'
KillDel 'C:\ProgramData\Windows Master Store'
KillDel 'C:\ProgramData\ZulerCoreTools'
KillDel 'C:\ProgramData\leidian'
KillDel 'C:\ProgramData\NeteaseWinDev'
KillDel 'C:\Users\JIAN'

"=== 360 dirs (last) ==="
KillDel 'C:\360Downloads'
KillDel 'C:\360RecycleBin'
Get-ChildItem C:\ -Force -Filter '360*' | ForEach-Object { KillDel $_.FullName }
Get-ChildItem C:\ -Force -Filter 'VC_RED*' | ForEach-Object { KillDel $_.FullName }
if (Test-Path -LiteralPath 'C:\install.exe') { KillDel 'C:\install.exe' }

"END $(Get-Date)" | Tee-Object -Append $log
