$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$log = 'D:\stage3h.log'
function Log($m){ $t=Get-Date -Format 'HH:mm:ss'; "$t $m" | Tee-Object -Append $log }
"=== STAGE3H START (pid $PID) ===" | Set-Content $log

$shim = 'D:\npm-global\openclaw.cmd'
$env:Path = 'D:\Nodejs;D:\npm-global;' + [Environment]::GetEnvironmentVariable('Path','Machine')
$env:OLLAMA_HOST = '127.0.0.1:11434'

$ver = & $shim --version 2>&1 | Out-String
Log "[OpenClaw] version: $ver"

# choose model from live tags
$tags = curl.exe -s http://127.0.0.1:11434/api/tags
$model = 'qwen2.5:3b'
if ($tags -match 'qwen2.5:7b') { $model = 'qwen2.5:7b' }
Log "[OpenClaw] model: $model"

Log "[OpenClaw] onboarding (non-interactive, ollama) ..."
& $shim onboard --non-interactive --auth-choice ollama --custom-model-id $model --accept-risk *>> D:\openclaw_onboard.log
if (Test-Path D:\openclaw_onboard.log) { Get-Content D:\openclaw_onboard.log -Tail 12 | ForEach-Object { Log "  ob: $_" } }

$cfg = "$env:USERPROFILE\.openclaw\openclaw.json"
if (Test-Path $cfg) { Log "[OpenClaw] CONFIG OK: $cfg"; Get-Content $cfg -Raw | ForEach-Object { Log "  cfg: $_" } } else { Log "[OpenClaw] CONFIG MISSING at $cfg" }
Log "=== STAGE3H END ==="
