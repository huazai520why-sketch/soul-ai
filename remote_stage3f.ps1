$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$log = 'D:\stage3f.log'
function Log($m){ $t=Get-Date -Format 'HH:mm:ss'; "$t $m" | Tee-Object -Append $log }
"=== STAGE3F START (pid $PID) ===" | Set-Content $log

# ---- 1. Ollama as scheduled task (survives SSH disconnect) ----
Get-Process ollama -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep 2
schtasks /delete /tn "OllamaServer" /f 2>&1 | Out-Null
schtasks /create /tn "OllamaServer" /tr "D:\ollama_serve.cmd" /sc onstart /ru SYSTEM /rl highest /f 2>&1 | ForEach-Object { Log "  task-create: $_" }
schtasks /run /tn "OllamaServer" 2>&1 | ForEach-Object { Log "  task-run: $_" }
Log "[Ollama] waiting for 11434 via scheduled task ..."
$up=$false
for ($i=0; $i -lt 30; $i++){ $r=curl.exe -s -o $null -w '%{http_code}' http://127.0.0.1:11434; if($r -eq '200'){$up=$true;break}; Start-Sleep 2 }
Log "[Ollama] reachable(11434): $up"
$tags = curl.exe -s http://127.0.0.1:11434/api/tags
Log "[Ollama] tags: $tags"

# ---- 2. OpenClaw onboard via node + bin entry ----
$node = 'D:\Nodejs\node.exe'
$entry = 'D:\npm-global\node_modules\openclaw\bin\openclaw.js'
if (-not (Test-Path $entry)) {
    $entry = (Get-ChildItem 'D:\npm-global\node_modules\openclaw' -Filter '*.js' -Recurse | Where-Object { $_.Name -match 'openclaw|cli|index' } | Select-Object -First 1).FullName
}
Log "[OpenClaw] node=$node entry=$entry"
$ov = & $node $entry --version 2>&1 | Out-String
Log "[OpenClaw] version: $ov"

$model = 'qwen2.5:3b'
if ($tags -match 'qwen2.5:7b') { $model = 'qwen2.5:7b' }
Log "[OpenClaw] model: $model"

Log "[OpenClaw] onboarding ..."
& $node $entry onboard --non-interactive --auth-choice ollama --custom-model-id $model --accept-risk *>> D:\openclaw_onboard.log
if (Test-Path D:\openclaw_onboard.log) { Get-Content D:\openclaw_onboard.log -Tail 8 | ForEach-Object { Log "  onboard: $_" } }

& $node $entry models set ollama/$model *>> D:\openclaw_onboard.log
Log "[OpenClaw] models set done"

$cfg = "$env:USERPROFILE\.openclaw\openclaw.json"
if (Test-Path $cfg) { Log "[OpenClaw] CONFIG OK: $cfg"; Get-Content $cfg -Raw | ForEach-Object { Log "  cfg: $_" } } else { Log "[OpenClaw] CONFIG MISSING: $cfg" }
Log "=== STAGE3F END ==="
