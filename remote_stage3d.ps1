$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$log = 'D:\stage3d.log'
function Log($m){ $t=Get-Date -Format 'HH:mm:ss'; "$t $m" | Tee-Object -Append $log }
"=== STAGE3D START (pid $PID) ===" | Set-Content $log

# PATH: Node + npm global
$env:Path = 'D:\Nodejs;D:\npm-global;' + [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')

# locate openclaw shim
$ocCmd = $null
foreach ($c in @('D:\npm-global\openclaw.cmd','D:\npm-global\openclaw','D:\Nodejs\openclaw.cmd')) {
    if (Test-Path $c) { $ocCmd = $c; break }
}
if (-not $ocCmd) {
    # fall back to node + cli entry
    $entry = 'D:\npm-global\node_modules\openclaw\dist\index.js'
    if (-not (Test-Path $entry)) { $entry = 'D:\npm-global\node_modules\openclaw\bin\openclaw.js' }
    Log "[OpenClaw] shim not found, using node entry: $entry"
    $ocCmd = "node `"$entry`""
}
Log "[OpenClaw] cmd: $ocCmd"

# node + openclaw version
$nv = & D:\Nodejs\node.exe --version
Log "[OpenClaw] node: $nv"
$ov = & D:\npm-global\openclaw.cmd --version 2>&1 | Out-String
Log "[OpenClaw] openclaw --version: $ov"

# pick model: prefer 7b if present else 3b
$env:OLLAMA_MODELS = 'D:\OllamaModels'
$env:OLLAMA_HOST = '127.0.0.1:11434'
$tags = curl.exe -s http://127.0.0.1:11434/api/tags
Log "[OpenClaw] tags: $tags"
$model = 'qwen2.5:3b'
if ($tags -match 'qwen2.5:7b') { $model = 'qwen2.5:7b' }
Log "[OpenClaw] chosen model: $model"

# onboard non-interactive with local ollama
Log "[OpenClaw] onboarding ..."
& D:\npm-global\openclaw.cmd onboard --non-interactive --auth-choice ollama --custom-model-id $model --accept-risk *>> D:\openclaw_onboard.log
Log "[OpenClaw] onboard tail:"
if (Test-Path D:\openclaw_onboard.log) { Get-Content D:\openclaw_onboard.log -Tail 5 | ForEach-Object { Log "  $_" } }

# set model explicitly
& D:\npm-global\openclaw.cmd models set ollama/$model *>> D:\openclaw_onboard.log
Log "[OpenClaw] models set done"

# show config
$cfg = "$env:USERPROFILE\.openclaw\openclaw.json"
if (Test-Path $cfg) { Log "[OpenClaw] config exists: $cfg"; Get-Content $cfg -Raw | ForEach-Object { Log $_ } } else { Log "[OpenClaw] config MISSING at $cfg" }
Log "=== STAGE3D END ==="
