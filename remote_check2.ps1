$ProgressPreference='SilentlyContinue'
$env:Path='D:\Nodejs;D:\npm-global;'+$env:Path
$out=@()
# Ollama inference test (CPU-only, use 3b for speed)
try {
  $body = '{"model":"qwen2.5:3b","prompt":"Reply with exactly the word: PONG","stream":false}'
  $r = Invoke-RestMethod http://127.0.0.1:11434/api/generate -Method Post -Body $body -ContentType 'application/json' -TimeoutSec 90
  $out += "Ollama inference test: response='$($r.response)'"
} catch { $out += "Ollama inference ERR: $($_.Exception.Message)" }

# OpenClaw config inspection
$cfg = 'C:\Users\JIAN\.openclaw\openclaw.json'
if (Test-Path $cfg) {
  $j = Get-Content $cfg -Raw -Encoding utf8
  $out += "openclaw.json size: $((Get-Item $cfg).Length) bytes"
  if ($j -match '"language"') { $out += "language field: $([regex]::Match($j,'"language"\s*:\s*"[^"]*"').Value)" } else { $out += "no 'language' field" }
  if ($j -match '"locale"') { $out += "locale field: $([regex]::Match($j,'"locale"\s*:\s*"[^"]*"').Value)" } else { $out += "no 'locale' field" }
  $out += "primary model line: $([regex]::Match($j,'"primary"\s*:\s*"[^"]*"').Value)"
}

# scheduled task details
$out += "=== OllamaServer task ==="
$out += (schtasks /query /tn "\OllamaServer" /fo LIST /v 2>$null | findstr /i "Task Name Trigger Status Next Run")
$out += "=== OpenClaw Gateway task ==="
$out += (schtasks /query /tn "\OpenClaw Gateway" /fo LIST /v 2>$null | findstr /i "Task Name Trigger Status Next Run")

$out | Out-File D:\check2.log -Encoding ascii
$out -join "`n"
