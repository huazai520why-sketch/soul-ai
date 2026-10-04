$ProgressPreference='SilentlyContinue'
$log = 'D:\hub_grep2.log'
$lines = @()
function Log($s){ $script:lines += $s }

$cands = 'DeviceToken|GatewayDeviceToken|AuthToken|AccessToken|StoredDeviceToken|SavedDeviceToken|DeviceSecret|SetupCode|PairingCode|GatewayToken|stored device|device token'
Log "UTF-16 grep for candidate token property names..."
$found = 0
Get-ChildItem 'D:\OpenClawCompanion' -Recurse -Include *.dll,*.exe -ErrorAction SilentlyContinue | Where-Object { $_.Length -lt 80MB } | ForEach-Object {
  $f = $_.Name
  try {
    Select-String -Path $_.FullName -Pattern $cands -Encoding unicode -ErrorAction SilentlyContinue | Select-Object -First 6 | ForEach-Object {
      $line = $_.Line.Trim()
      if($line.Length -gt 160){ $line = $line.Substring(0,160) }
      Log "  [$f] $line"
      $found++
    }
  } catch {}
}
Log "Candidate matches: $found"

# Broad: list all identifiers containing 'Token' to discover exact key
Log ""
Log "Broad 'Token' identifiers (unicode):"
$broad = 0
Get-ChildItem 'D:\OpenClawCompanion' -Recurse -Include *.dll,*.exe -ErrorAction SilentlyContinue | Where-Object { $_.Length -lt 80MB } | ForEach-Object {
  try {
    Select-String -Path $_.FullName -Pattern 'Token' -Encoding unicode -ErrorAction SilentlyContinue | Select-Object -First 12 | ForEach-Object {
      $line = $_.Line.Trim() -replace '[^\x20-\x7E]',''
      if($line.Length -gt 0 -and $line.Length -lt 120){
        Log "  [$($_.Name)] $line"
        $broad++
      }
    }
  } catch {}
}
Log "Broad token matches: $broad"

$lines | Out-File -Encoding ascii $log
