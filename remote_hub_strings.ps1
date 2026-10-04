$ProgressPreference='SilentlyContinue'
$log = 'D:\hub_strings.log'
$lines = @()
function Log($s){ $script:lines += $s }

function Extract-Utf16Ascii($path){
  $bytes = [IO.File]::ReadAllBytes($path)
  $runs = @()
  $cur = [System.Collections.Generic.List[char]]::new()
  $i = 0
  while($i -lt $bytes.Length){
    $b = $bytes[$i]; $b2 = if($i+1 -lt $bytes.Length){$bytes[$i+1]}else{1}
    if($b2 -eq 0 -and $b -ge 32 -and $b -lt 127){
      $cur.Add([char]$b); $i += 2
    } else {
      if($cur.Count -ge 5){ $runs += -join $cur }
      $cur.Clear(); $i += 1
    }
  }
  return $runs
}

$targets = @('OpenClaw.Connection.dll','OpenClaw.Tray.WinUI.dll','OpenClaw.Shared.dll')
foreach($t in $targets){
  $p = "D:\OpenClawCompanion\$t"
  if(-not (Test-Path $p)){ continue }
  Log "===== $t ====="
  try {
    $runs = Extract-Utf16Ascii $p
    $runs | Where-Object { $_ -match 'Token|Device|Setup|Pair|Gateway' } | Sort-Object -Unique | Select-Object -First 60 | ForEach-Object { Log "  $_" }
  } catch { Log "  ERR: $_" }
}

$lines | Out-File -Encoding ascii $log
