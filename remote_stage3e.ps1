$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$log = 'D:\stage3e.log'
function Log($m){ $t=Get-Date -Format 'HH:mm:ss'; "$t $m" | Tee-Object -Append $log }
"=== STAGE3E START (pid $PID) ===" | Set-Content $log

$ollama = (Get-ChildItem -Path 'D:\Ollama' -Filter 'ollama.exe' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1).FullName

# hard reset: kill all ollama, set env, start ONE serve with correct models dir
Get-Process ollama -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep 3
[Environment]::SetEnvironmentVariable('OLLAMA_MODELS','D:\OllamaModels','Machine')
$env:OLLAMA_MODELS = 'D:\OllamaModels'
New-Item -ItemType Directory -Force -Path 'D:\OllamaModels' | Out-Null
Start-Process -FilePath $ollama -ArgumentList 'serve' -WindowStyle Hidden
Start-Sleep 6
$r = curl.exe -s -o $null -w '%{http_code}' http://127.0.0.1:11434
Log "[Ollama] serve up(11434): $r ; OLLAMA_MODELS=$env:OLLAMA_MODELS"

$tags1 = curl.exe -s http://127.0.0.1:11434/api/tags
Log "[Ollama] tags before finalize: $tags1"

# finalize 7b (blobs cached -> fast). Retry up to 3.
$ok = $false
for ($try=1; $try -le 3; $try++) {
    Log "[Ollama] finalize 7b attempt $try"
    & $ollama pull qwen2.5:7b *>> D:\pull7b_final.log
    $t = curl.exe -s http://127.0.0.1:11434/api/tags
    if ($t -match 'qwen2.5:7b') { $ok = $true; break }
    Start-Sleep 4
}
$tags2 = curl.exe -s http://127.0.0.1:11434/api/tags
Log "[Ollama] 7b registered: $ok"
Log "[Ollama] tags after finalize: $tags2"
Log "=== STAGE3E END ==="
