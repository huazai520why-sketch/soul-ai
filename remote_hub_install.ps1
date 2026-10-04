$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$log = 'D:\hub_install.log'
function Log($m){ "$(Get-Date -Format 'HH:mm:ss') $m" | Tee-Object -Append -FilePath $log }

Log '=== OpenClaw Hub (Companion) install start ==='

# 1. WebView2 runtime check (WinUI app needs it)
try {
  $wv = Get-ItemProperty 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A08C11}' -ErrorAction SilentlyContinue
  if ($wv -and $wv.pv) { Log "WebView2 runtime present: $($wv.pv)" }
  else { Log 'WARNING: WebView2 runtime NOT found in registry (app may fail to launch until installed)' }
} catch { Log "WebView2 check error: $_" }

# 2. Download installer (try candidates)
$candidates = @(
  'https://github.com/openclaw/openclaw/releases/latest/download/OpenClawCompanion-Setup-x64.exe',
  'https://github.com/openclaw/openclaw-windows-node/releases/latest/download/OpenClawCompanion-Setup-x64.exe'
)
$dest = 'D:\Downloads\OpenClawCompanion-Setup-x64.exe'
if (Test-Path $dest) { Remove-Item $dest -Force }
$ok = $false
foreach ($url in $candidates) {
  Log "Downloading $url"
  for ($i=1; $i -le 3; $i++) {
    try {
      Invoke-WebRequest -Uri $url -OutFile $dest -TimeoutSec 600 -ErrorAction Stop
      if ((Get-Item $dest).Length -gt 1MB) { $ok = $true; break }
    } catch { Log "  attempt $i failed: $_"; Start-Sleep -Seconds 5 }
  }
  if ($ok) { break }
}
if (-not $ok) { Log 'DOWNLOAD FAILED for all candidates'; exit 1 }
Log "Downloaded $([math]::Round((Get-Item $dest).Length/1MB,1)) MB to $dest"

# 3. Silent install to D:\OpenClawCompanion (keep C: clean)
$installDir = 'D:\OpenClawCompanion'
if (-not (Test-Path $installDir)) { New-Item -ItemType Directory -Path $installDir | Out-Null }
Log "Installing silently to $installDir"
$proc = Start-Process -FilePath $dest -ArgumentList "/SILENT","/SUPPRESSMSGBOXES","/NORESTART","/DIR=`"$installDir`"" -Wait -PassThru
Log "Installer exit code: $($proc.ExitCode)"

# 4. Verify
$expected = Join-Path $installDir 'OpenClawCompanion.exe'
if (Test-Path $expected) { Log "INSTALLED OK: $expected" }
else {
  Log "WARN: expected exe not found at $expected; listing install dir:"
  Get-ChildItem $installDir -Recurse -ErrorAction SilentlyContinue | Select-Object -First 30 Name | Out-String | ForEach-Object { Log $_ }
}
Log '=== done ==='
