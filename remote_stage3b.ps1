$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$log = 'D:\stage3b.log'
function Log($m){ $t=Get-Date -Format 'HH:mm:ss'; "$t $m" | Tee-Object -Append $log }
"=== STAGE3B START (pid $PID) ===" | Set-Content $log

$env:OLLAMA_MODELS = 'D:\OllamaModels'
$ollama = (Get-ChildItem -Path 'D:\Ollama' -Filter 'ollama.exe' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1).FullName
Log "[Ollama] binary: $ollama"

# ensure serve running
$r = curl.exe -s -o $null -w '%{http_code}' http://127.0.0.1:11434
if ($r -ne '200') {
    Log "[Ollama] not up, starting serve ..."
    Start-Process -FilePath $ollama -ArgumentList 'serve' -WindowStyle Hidden
    Start-Sleep 5
    $r = curl.exe -s -o $null -w '%{http_code}' http://127.0.0.1:11434
}
Log "[Ollama] reachable(11434): $r"

# pull 3b (fast fallback) - redirect all streams to file to avoid console progress errors
Log "[Ollama] pulling qwen2.5:3b ..."
& $ollama pull qwen2.5:3b *>> D:\pull3b.log
Log "[Ollama] 3b pull exit, tail:"
if (Test-Path D:\pull3b.log) { Get-Content D:\pull3b.log -Tail 2 | ForEach-Object { Log "  $_" } }

# pull 7b (primary large model)
Log "[Ollama] pulling qwen2.5:7b ..."
& $ollama pull qwen2.5:7b *>> D:\pull7b.log
Log "[Ollama] 7b pull exit, tail:"
if (Test-Path D:\pull7b.log) { Get-Content D:\pull7b.log -Tail 2 | ForEach-Object { Log "  $_" } }

$list = & $ollama list 2>&1 | Out-String
Log "[Ollama] list:`n$list"
Log "=== STAGE3B END ==="
