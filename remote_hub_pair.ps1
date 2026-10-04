$ProgressPreference='SilentlyContinue'
$log = 'D:\hub_pair.log'
$lines = @()
function Log($s){ $script:lines += $s }

# 1) Gateway device key (the npm gateway's own identity)
$src = 'C:\Users\JIAN\.openclaw\device-key-ed25519.json'
Log "===== Gateway device-key source ====="
if(Test-Path $src){
  Log "EXISTS. Content:"
  Get-Content $src -Raw | ForEach { Log "  $($_.Trim())" }
} else {
  Log "  (not found at $src)"
  # search .openclaw for any device-key file
  Get-ChildItem 'C:\Users\JIAN\.openclaw' -Recurse -ErrorAction SilentlyContinue | Where { $_.Name -match 'device-key' } | ForEach { Log "  found: $($_.FullName)" }
}

# 2) Hub dirs contents
Log ""
Log "===== Hub AppData dirs ====="
foreach($b in @("$env:APPDATA\OpenClawTray","$env:LOCALAPPDATA\OpenClawTray")){
  Log "--- $b ---"
  if(Test-Path $b){
    Get-ChildItem $b -Recurse -ErrorAction SilentlyContinue | ForEach { Log "  $($_.FullName)  (dir=$($_.PSIsContainer))" }
  } else { Log "  (not found)" }
}

# 3) gateways.json anywhere in OpenClawTray
Log ""
Log "===== gateways.json / registry files ====="
Get-ChildItem "$env:LOCALAPPDATA\OpenClawTray","$env:APPDATA\OpenClawTray" -Recurse -ErrorAction SilentlyContinue | Where { $_.Name -match 'gateway|registry' } | ForEach { Log "  $($_.FullName)" }

# 4) EXPERIMENT: copy gateway device-key to Hub Local dir (COPY, not move)
Log ""
Log "===== EXPERIMENT: copy gateway device-key to Hub ====="
$dst = "$env:LOCALAPPDATA\OpenClawTray\device-key-ed25519.json"
if(Test-Path $src -and -not (Test-Path $dst)){
  try {
    Copy-Item $src $dst -Force
    Log "Copied gateway device-key -> $dst : $(Test-Path $dst)"
  } catch { Log "Copy FAILED: $_" }
} elseif(Test-Path $dst) { Log "Hub device-key already exists, skip copy" }
else { Log "No source device-key to copy" }

# 5) Launch Hub headless and observe log
Log ""
Log "===== Launch Hub headless, observe ====="
$svcLog = "$env:LOCALAPPDATA\OpenClawTray\openclaw-tray.log"
if(Test-Path $svcLog){ Clear-Content $svcLog }
try {
  Start-Process -FilePath 'D:\OpenClawCompanion\OpenClaw.Tray.WinUI.exe' -WindowStyle Hidden -ErrorAction Stop
  Log "Hub launched"
} catch { Log "Launch FAILED: $_" }
Start-Sleep -Seconds 12
if(Test-Path $svcLog){
  Log "--- tray log tail ---"
  Get-Content $svcLog -Tail 25 | ForEach { Log "  $($_)" }
} else { Log "  (no tray log produced)" }

$lines | Out-File -Encoding ascii $log
