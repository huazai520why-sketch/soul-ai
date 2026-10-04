$ProgressPreference='SilentlyContinue'
$log = 'D:\hub_pair2.log'
$lines = @()
function Log($s){ $script:lines += $s }

# Find gateway device-key file anywhere reasonable
Log "===== Search for device-key-ed25519.json ====="
$roots = @('C:\Users\JIAN','D:\npm-global','D:\OllamaModels')
foreach($r in $roots){
  if(Test-Path $r){
    Get-ChildItem $r -Recurse -Filter 'device-key-ed25519.json' -ErrorAction SilentlyContinue | ForEach { Log "  FOUND: $($_.FullName)" }
  }
}
# Also list .openclaw dir fully
Log ""
Log "===== C:\Users\JIAN\.openclaw contents ====="
if(Test-Path 'C:\Users\JIAN\.openclaw'){
  Get-ChildItem 'C:\Users\JIAN\.openclaw' -Recurse -ErrorAction SilentlyContinue | ForEach { Log "  $($_.FullName)" }
} else { Log "  (no .openclaw dir)" }

# Experiment: if found, copy to Hub Local dir
Log ""
Log "===== Experiment copy ====="
$src = $null
foreach($r in $roots){
  if(Test-Path $r){
    $f = Get-ChildItem $r -Recurse -Filter 'device-key-ed25519.json' -ErrorAction SilentlyContinue | Select-Object -First 1
    if($f){ $src = $f.FullName; break }
  }
}
$dst = "$env:LOCALAPPDATA\OpenClawTray\device-key-ed25519.json"
if($src -and !(Test-Path $dst)){
  try { Copy-Item $src $dst -Force; Log "Copied $src -> $dst : exists=$(Test-Path $dst)" }
  catch { Log "Copy FAILED: $_" }
} elseif($src -and (Test-Path $dst)) { Log "Hub device-key already present" }
else { Log "No gateway device-key found to copy" }

# Launch Hub headless, observe
Log ""
Log "===== Launch Hub, observe ====="
$svcLog = "$env:LOCALAPPDATA\OpenClawTray\openclaw-tray.log"
if(Test-Path $svcLog){ Clear-Content $svcLog -ErrorAction SilentlyContinue }
try { Start-Process -FilePath 'D:\OpenClawCompanion\OpenClaw.Tray.WinUI.exe' -WindowStyle Hidden -ErrorAction Stop; Log "Hub launched" }
catch { Log "Launch FAILED: $_" }
Start-Sleep -Seconds 12
if(Test-Path $svcLog){ Get-Content $svcLog -Tail 25 | ForEach { Log "  $($_)" } }
else { Log "  (no tray log)" }

$lines | Out-File -Encoding ascii $log
