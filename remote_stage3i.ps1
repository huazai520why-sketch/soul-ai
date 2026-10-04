$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$log = 'D:\stage3i.log'
function Log($m){ $t=Get-Date -Format 'HH:mm:ss'; "$t $m" | Tee-Object -Append $log }
"=== STAGE3I START (pid $PID) ===" | Set-Content $log

$shim = 'D:\npm-global\openclaw.cmd'
$env:Path = 'D:\Nodejs;D:\npm-global;' + [Environment]::GetEnvironmentVariable('Path','Machine')
$env:OLLAMA_HOST = '127.0.0.1:11434'

Log "[OpenClaw] installing gateway daemon ..."
& $shim onboard --non-interactive --auth-choice ollama --custom-model-id qwen2.5:7b --accept-risk --install-daemon *>> D:\openclaw_daemon.log
Log "[OpenClaw] daemon install log tail:"
if (Test-Path D:\openclaw_daemon.log) { Get-Content D:\openclaw_daemon.log -Tail 15 | ForEach-Object { Log "  d: $_" } }

Start-Sleep 5
$g = curl.exe -s -o $null -w '%{http_code}' http://127.0.0.1:18789
Log "[OpenClaw] gateway 18789 http: $g"

# list any openclaw scheduled tasks / startup items
Log "[OpenClaw] scheduled tasks matching openclaw:"
schtasks /query /fo LIST 2>&1 | Select-String -Pattern 'openclaw' -SimpleMatch | ForEach-Object { Log "  $_" }
Log "=== STAGE3I END ==="
