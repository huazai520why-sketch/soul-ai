$ErrorActionPreference = 'Stop'
$log = 'D:\hub_verify.log'
function Log($m){ "$(Get-Date -Format 'HH:mm:ss') $m" | Tee-Object -Append -FilePath $log }

Log '=== Hub verify ==='
$exe = 'D:\OpenClawCompanion\OpenClaw.Tray.WinUI.exe'
if (Test-Path $exe) { Log "MAIN EXE OK: $exe  ($([math]::Round((Get-Item $exe).Length/1KB,1)) KB)" } else { Log "MAIN EXE MISSING" }

# Bundled WebView2 inside install dir?
$bundled = Get-ChildItem 'D:\OpenClawCompanion' -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.Name -like '*WebView2*' -or $_.Name -eq 'WebView2Loader.dll' }
Log "Bundled WebView2 items: $(($bundled | Select-Object -ExpandProperty FullName) -join ' ; ')"

# System WebView2 runtime (several possible locations)
$sysPaths = @(
  'C:\Program Files (x86)\Microsoft\EdgeWebView\Application',
  'C:\Program Files\Microsoft\EdgeWebView\Application',
  "$env:LOCALAPPDATA\Microsoft\EdgeWebView\Application"
)
foreach ($p in $sysPaths) {
  if (Test-Path $p) {
    $ver = Get-ChildItem $p -Directory -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name -First 1
    Log "System WebView2 FOUND at $p (version dir: $ver)"
  } else { Log "No WebView2 at $p" }
}

# Any WebView2 client GUID in registry (machine + user)
$guids = @('{F3017226-FE2A-4295-8BDF-00C3A9A08C11}', '{EDNXHSB2-1XJ8-4X2B-9X9X-000000000000}')
$edgeUpdate = 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients'
if (Test-Path $edgeUpdate) {
  $clients = Get-ChildItem $edgeUpdate -ErrorAction SilentlyContinue | Select-Object -ExpandProperty PSChildName
  Log "EdgeUpdate clients: $($clients -join ', ')"
} else { Log "No EdgeUpdate clients key" }

# Edge browser (implies WebView2 availability)
$edge = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Edge\BLBeacon' -ErrorAction SilentlyContinue
if ($edge -and $edge.version) { Log "Microsoft Edge present: $($edge.version)" } else { Log "Microsoft Edge NOT found in registry" }

Log '=== verify done ==='
