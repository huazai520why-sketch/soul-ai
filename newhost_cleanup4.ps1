[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'SilentlyContinue'
$log = 'C:\Users\wbadmin\cleanup4_log.txt'
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

# --- C: root junk ---
Del 'C:\360Downloads'
Del 'C:\360RecycleBin'
Del 'C:\DaBaiCai'
Del 'C:\ESD'
Del 'C:\KuGou'
Del 'C:\leidian'
Del 'C:\AMTAG.BIN'
Get-ChildItem C:\ -Force -Filter '360*' | ForEach-Object { Del $_.FullName }
Get-ChildItem C:\ -Force -Filter 'VC_RED*' | ForEach-Object { Del $_.FullName }
if (Test-Path -LiteralPath 'C:\install.exe') { Del 'C:\install.exe' }
Del 'C:\$WINDOWS.~BT'
Del 'C:\$Windows.~WS'
Del 'C:\$WinREAgent'
Del 'C:\Windows10Upgrade'

# --- Program Files (x64) junk ---
Del 'C:\Program Files\ldplayer9box'
Del 'C:\Program Files\Oray'
Del 'C:\Program Files\Tencent'
Del 'C:\Program Files\ToDesk'

# --- Program Files (x86) junk ---
Del 'C:\Program Files (x86)\360'
Del 'C:\Program Files (x86)\KuGou'

# --- ProgramData junk (never touch ssh / Microsoft / NVIDIA) ---
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

# --- Old owner profile JIANG ---
Del 'C:\Users\JIAN'
try {
  $up = Get-CimInstance Win32_UserProfile -ErrorAction SilentlyContinue | Where-Object { $_.LocalPath -like '*JIANG' }
  if ($up) { $up | Remove-CimInstance -ErrorAction SilentlyContinue; "REMOVED_PROFILE_JIANG" | Tee-Object -Append $log }
} catch { "PROFILE_REG_FAIL: $_" | Tee-Object -Append $log }

"END $(Get-Date)" | Tee-Object -Append $log
