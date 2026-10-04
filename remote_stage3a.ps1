$ErrorActionPreference = 'Continue'
$log = 'D:\stage3a.log'
function Log($m){ $t=Get-Date -Format 'HH:mm:ss'; "$t $m" | Tee-Object -Append $log }
"=== STAGE3A START (pid $PID) ===" | Set-Content $log

# ---------- 1. Node upgrade to 22.22.3 ----------
Log "[Node] downloading v22.22.3 via curl ..."
curl.exe -sSL -o D:\Downloads\node_new.msi https://nodejs.org/dist/v22.22.3/node-v22.22.3-x64.msi
$ni = Get-Item D:\Downloads\node_new.msi -ErrorAction SilentlyContinue
Log "[Node] node_new.msi MB: $([math]::Round($ni.Length/1MB,1))"
if ($ni.Length -gt 1000000) {
    Log "[Node] installing via msiexec ..."
    Start-Process msiexec.exe -ArgumentList '/i','D:\Downloads\node_new.msi','INSTALLDIR=D:\Nodejs','/qn' -Wait
} else {
    Log "[Node] download FAILED (too small), keeping existing"
}
$v = & D:\Nodejs\node.exe --version
Log "[Node] version now: $v"

# ---------- 2. Ollama portable ----------
Log "[Ollama] downloading portable zip via curl ..."
curl.exe -sSL -L -o D:\Downloads\ollama.zip https://github.com/ollama/ollama/releases/latest/download/ollama-windows-amd64.zip
$zi = Get-Item D:\Downloads\ollama.zip -ErrorAction SilentlyContinue
Log "[Ollama] zip MB: $([math]::Round($zi.Length/1MB,1))"
if ($zi.Length -gt 10000000) {
    if (Test-Path 'D:\Ollama') { Remove-Item 'D:\Ollama' -Recurse -Force }
    Expand-Archive -Path D:\Downloads\ollama.zip -DestinationPath 'D:\Ollama' -Force
    Log "[Ollama] extracted to D:\Ollama"
} else {
    Log "[Ollama] zip download FAILED (too small)"
}
$ollamaItem = Get-ChildItem -Path 'D:\Ollama' -Filter 'ollama.exe' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
if ($ollamaItem) { $ollama = $ollamaItem.FullName; $ver = & $ollama --version 2>&1; Log "[Ollama] binary: $ollama : $ver" } else { Log "[Ollama] FAIL binary missing"; Log "=== STAGE3A END (ollama missing) ==="; exit }

# ---------- 3. models env + serve ----------
[Environment]::SetEnvironmentVariable('OLLAMA_MODELS','D:\OllamaModels','Machine')
$env:OLLAMA_MODELS = 'D:\OllamaModels'
New-Item -ItemType Directory -Force -Path 'D:\OllamaModels' | Out-Null
Start-Process -FilePath $ollama -ArgumentList 'serve' -WindowStyle Hidden
Log "[Ollama] serve started, waiting for 11434 ..."
$up = $false
for ($i=0; $i -lt 40; $i++) {
    $r = curl.exe -s -o $null -w '%{http_code}' http://127.0.0.1:11434
    if ($r -eq '200') { $up=$true; break }
    Start-Sleep 2
}
Log "[Ollama] reachable(11434): $up"

# ---------- 4. pull small model first ----------
Log "[Ollama] pulling qwen2.5:3b (this can take a while) ..."
& $ollama pull qwen2.5:3b 2>&1 | Select-Object -Last 1 | ForEach-Object { Log "  pull3b: $_" }
$list = & $ollama list 2>&1
Log "[Ollama] list: $list"
Log "=== STAGE3A END ==="
