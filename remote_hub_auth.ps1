$ProgressPreference='SilentlyContinue'
$log = 'D:\hub_auth.log'
$lines = @()
function Log($s){ $script:lines += $s }

# 1) Gateway auth config from openclaw.json
Log "===== openclaw.json gateway.auth ====="
$cfg = Get-Content 'C:\Users\JIAN\.openclaw\openclaw.json' -Raw -ErrorAction SilentlyContinue
if($cfg){
  try {
    $j = $cfg | ConvertFrom-Json
    $j.gateway | ConvertTo-Json -Depth 5 | Out-String | ForEach { Log $_ }
  } catch { Log "  parse error: $_" }
}

# 2) Any token reference in Hub config dirs
Log ""
Log "===== search 'token' in OpenClawTray dirs ====="
foreach($base in @("$env:APPDATA\OpenClawTray","$env:LOCALAPPDATA\OpenClawTray")){
  if(Test-Path $base){
    Get-ChildItem $base -Recurse -ErrorAction SilentlyContinue | Where-Object { -not $_.PSIsContainer } | ForEach-Object {
      try {
        $txt = Get-Content $_.FullName -Raw -ErrorAction Stop
        if($txt -match 'token'){ Log "  MATCH in $($_.FullName): $(($txt -split [Environment]::NewLine | Where-Object { $_ -match 'token' } | Select-Object -First 3) -join ' | ')" }
      } catch {}
    }
  }
}

# 3) Tray log tail
Log ""
Log "===== openclaw-tray.log tail ====="
if(Test-Path "$env:LOCALAPPDATA\OpenClawTray\openclaw-tray.log"){
  Get-Content "$env:LOCALAPPDATA\OpenClawTray\openclaw-tray.log" -Tail 30 -ErrorAction SilentlyContinue | ForEach { Log "  $_" }
} else { Log "  (no tray log yet)" }

$lines | Out-File -Encoding ascii $log
