$out='D:\occheck.log'
"=== OC CHECK $(Get-Date -Format 'HH:mm:ss') ===" | Set-Content $out
$cfg = "$env:USERPROFILE\.openclaw\openclaw.json"
"USERPROFILE=$env:USERPROFILE" | Add-Content $out
if (Test-Path $cfg) { "CONFIG OK: $cfg" | Add-Content $out; Get-Content $cfg -Raw | Add-Content $out } else { "CONFIG MISSING: $cfg" | Add-Content $out }
"--- onboard log tail ---" | Add-Content $out
if (Test-Path D:\openclaw_onboard.log) { Get-Content D:\openclaw_onboard.log -Tail 25 | Add-Content $out } else { "no onboard log" | Add-Content $out }
"--- openclaw processes ---" | Add-Content $out
Get-Process -Name node,openclaw -ErrorAction SilentlyContinue | Select-Object Id,ProcessName | ForEach-Object { "$($_.ProcessName) $($_.Id)" | Add-Content $out }
