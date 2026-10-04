$ProgressPreference='SilentlyContinue'
$log = 'D:\hub_final.log'
$lines = @()
function Log($s){ $script:lines += $s }

# Gateway still serving?
try {
  $r = Invoke-WebRequest -Uri 'http://127.0.0.1:18789' -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop
  Log "Gateway HTTP: $($r.StatusCode)"
} catch { Log "Gateway HTTP ERROR: $_" }

# Startup shortcut present?
$sc = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\OpenClaw Companion.lnk"
Log "Startup shortcut present: $(Test-Path $sc)"

# Gateway scheduled task enabled?
$tq = schtasks /query /tn 'OpenClaw Gateway' /v /fo LIST 2>&1
$tq | ForEach-Object { if($_ -match 'Status|Enabled|Next Run'){ Log "  $_" } }

# Hub exe present?
Log "Hub exe present: $(Test-Path 'D:\OpenClawCompanion\OpenClaw.Tray.WinUI.exe')"

$lines | Out-File -Encoding ascii $log
