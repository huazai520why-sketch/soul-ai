$ProgressPreference='SilentlyContinue'
$log = 'D:\hub_cfg.log'
$lines = @()
function Log($s){ $script:lines += $s }

foreach($base in @("$env:APPDATA\OpenClawTray","$env:LOCALAPPDATA\OpenClawTray")){
  Log "===== $base ====="
  if(Test-Path $base){
    Get-ChildItem $base -Recurse -ErrorAction SilentlyContinue | ForEach-Object {
      Log "  $($_.FullName)  (dir=$($_.PSIsContainer))  size=$($_.Length)"
    }
  } else {
    Log "  (not found)"
  }
}

# Read any json/config we find
foreach($base in @("$env:APPDATA\OpenClawTray","$env:LOCALAPPDATA\OpenClawTray")){
  if(Test-Path $base){
    Get-ChildItem $base -Recurse -ErrorAction SilentlyContinue -Include *.json,*.toml,*.yaml,*.yml,*.cfg,*.ini,*.config,*.db,*.sqlite | ForEach-Object {
      Log "----- content of $($_.FullName) -----"
      try { Log (Get-Content $_.FullName -Raw -ErrorAction Stop | Out-String) } catch { Log "  (cannot read: $_)" }
    }
  }
}

$lines | Out-File -Encoding ascii $log
