$ProgressPreference='SilentlyContinue'
$log = 'D:\hub_grep.log'
$lines = @()
function Log($s){ $script:lines += $s }

Log "Searching OpenClawCompanion binaries for token/device/setup identifiers..."
$pat = 'DeviceToken|deviceToken|SetupCode|setupCode|GatewayToken|gatewayToken|stored device|DeviceAuth|device auth|PairingCode|pairingCode|AccessToken|accessToken'
$count = 0
Get-ChildItem 'D:\OpenClawCompanion' -Recurse -Include *.dll,*.exe -ErrorAction SilentlyContinue | Where-Object { $_.Length -lt 120MB } | ForEach-Object {
  $f = $_.FullName
  try {
    Select-String -Path $f -Pattern $pat -Encoding ascii -ErrorAction SilentlyContinue | Select-Object -First 4 | ForEach-Object {
      $line = $_.Line.Trim()
      if($line.Length -gt 200){ $line = $line.Substring(0,200) }
      Log "  [$f] ...$line..."
      $count++
    }
  } catch {}
}
Log "Total matches: $count"

# Also list all files in the app root (top level) to spot config/credential helpers
Log ""
Log "Top-level files:"
Get-ChildItem 'D:\OpenClawCompanion' -ErrorAction SilentlyContinue | Where-Object { -not $_.PSIsContainer } | Select-Object Name,Length | ForEach-Object { Log "  $($_.Name) ($($_.Length))" }

$lines | Out-File -Encoding ascii $log
