$ProgressPreference='SilentlyContinue'
$log = 'D:\hub_autostart.log'
$lines = @()
function Log($s){ $script:lines += $s }

$hubExe = 'D:\OpenClawCompanion\OpenClaw.Tray.WinUI.exe'
Log "Hub exe exists: $(Test-Path $hubExe)"

# 1) Create Startup shortcut for JIANG (launches on every logon, no password needed)
$startupDir = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup"
Log "Startup dir exists: $(Test-Path $startupDir)"
try {
  $WshShell = New-Object -ComObject WScript.Shell
  $lnkPath = Join-Path $startupDir 'OpenClaw Companion.lnk'
  $lnk = $WshShell.CreateShortcut($lnkPath)
  $lnk.TargetPath = $hubExe
  $lnk.WorkingDirectory = 'D:\OpenClawCompanion'
  $lnk.Description = 'OpenClaw Companion'
  $lnk.Save()
  Log "Startup shortcut created: $(Test-Path $lnkPath)"
} catch {
  Log "Startup shortcut ERROR: $_"
}

# 2) Ensure the OpenClaw Gateway task is enabled (it starts the gateway on JIANG logon)
try {
  $q = schtasks /query /tn 'OpenClaw Gateway' 2>&1
  Log "Gateway task query exit: $LASTEXITCODE"
  Log ($q | Out-String)
  schtasks /change /tn 'OpenClaw Gateway' /ENABLE 2>&1 | ForEach { Log "Enable gateway: $_" }
} catch {
  Log "Gateway task ERROR: $_"
}

# 3) Search for Hub config files to see if we can pre-seed the gateway connection
$roots = @("$env:APPDATA","$env:LOCALAPPDATA","$env:USERPROFILE\AppData")
$cfg = @()
foreach($r in $roots){
  if(Test-Path $r){
    $cfg += Get-ChildItem $r -Recurse -ErrorAction SilentlyContinue |
      Where-Object { $_.PSIsContainer -eq $false -and $_.Name -match 'openclaw|molty|companion|tray' -and $_.Name -match '\.(json|toml|yaml|yml|cfg|ini|db|sqlite|config)$' } |
      Select-Object -First 30 FullName
  }
}
Log "Config candidates found: $($cfg.Count)"
Log ($cfg.FullName | Out-String)

# 4) Also list any OpenClaw-related dirs under APPDATA/LOCALAPPDATA
$dirs = @()
foreach($r in @("$env:APPDATA","$env:LOCALAPPDATA")){
  if(Test-Path $r){
    $dirs += Get-ChildItem $r -Directory -ErrorAction SilentlyContinue |
      Where-Object { $_.Name -match 'openclaw|molty|companion' } |
      Select-Object FullName
  }
}
Log "Related dirs:"
Log ($dirs.FullName | Out-String)

$lines | Out-File -Encoding ascii $log
