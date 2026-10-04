$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$log = 'D:\stage3c.log'
function Log($m){ $t=Get-Date -Format 'HH:mm:ss'; "$t $m" | Tee-Object -Append $log }
"=== STAGE3C START (pid $PID) ===" | Set-Content $log

$ollama = (Get-ChildItem -Path 'D:\Ollama' -Filter 'ollama.exe' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1).FullName
Log "[Ollama] binary: $ollama"

# kill ALL ollama processes to clear port conflicts
Get-Process ollama -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep 3
Log "[Ollama] killed old serve instances"

# start ONE serve
[Environment]::SetEnvironmentVariable('OLLAMA_MODELS','D:\OllamaModels','Machine')
$env:OLLAMA_MODELS = 'D:\OllamaModels'
Start-Process -FilePath $ollama -ArgumentList 'serve' -WindowStyle Hidden
Start-Sleep 6
$r = curl.exe -s -o $null -w '%{http_code}' http://127.0.0.1:11434
Log "[Ollama] reachable(11434): $r"

# pull 7b with up to 3 retries
$ok = $false
for ($try=1; $try -le 3; $try++) {
    Log "[Ollama] pulling qwen2.5:7b attempt $try ..."
    & $ollama pull qwen2.5:7b *>> D:\pull7b.log
    $tail = (Get-Content D:\pull7b.log -Tail 1)
    Log "[Ollama] attempt $try tail: $tail"
    if ($tail -match 'success') { $ok = $true; break }
    Start-Sleep 5
}
Log "[Ollama] 7b success: $ok"

# authoritative model list via API
$tags = curl.exe -s http://127.0.0.1:11434/api/tags
Log "[Ollama] api/tags: $tags"
Log "=== STAGE3C END ==="
