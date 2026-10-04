$ErrorActionPreference = 'Continue'
$log = 'D:\stage3.log'
function Log($m){ $t=Get-Date -Format 'HH:mm:ss'; "$t $m" | Tee-Object -Append $log }

Log "=== STAGE3 REVISED START (pid $PID) ==="

# kill any PREVIOUS stage3 run still in progress (exclude self)
$myPid = $PID
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*remote_stage3*' -and $_.ProcessId -ne $myPid } | ForEach-Object { $_.Terminate() } -ErrorAction SilentlyContinue
Start-Sleep 2

# refresh env in this session
$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')

# ---------- 1. Upgrade Node to v22.22.3 (curl download, msiexec) ----------
Log "[Node] downloading v22.22.3 via curl ..."
curl.exe -sSL -o D:\Downloads\node_new.msi https://nodejs.org/dist/v22.22.3/node-v22.22.3-x64.msi
Log "[Node] msiexec install ..."
Start-Process msiexec.exe -ArgumentList '/i','D:\Downloads\node_new.msi','INSTALLDIR=D:\Nodejs','/qn' -Wait
$v = & D:\Nodejs\node.exe --version
Log "[Node] version now: $v"
if ($v -notmatch '22.22') {
    Log "[Node] upgrade did not apply; forcing uninstall of old Node ..."
    wmic product where "name like 'Node.js%'" call uninstall /nointeractive | Out-Null
    Start-Process msiexec.exe -ArgumentList '/i','D:\Downloads\node_new.msi','INSTALLDIR=D:\Nodejs','/qn' -Wait
    $v = & D:\Nodejs\node.exe --version
    Log "[Node] version after forced reinstall: $v"
}
$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')

# ---------- 2. Ollama portable via direct GitHub URL ----------
Log "[Ollama] downloading portable zip via curl (latest/download) ..."
curl.exe -sSL -L -o D:\Downloads\ollama.zip https://github.com/ollama/ollama/releases/latest/download/ollama-windows-amd64.zip
$zi = Get-Item D:\Downloads\ollama.zip
Log "[Ollama] zip size MB: $([math]::Round($zi.Length/1MB,1))"
if (Test-Path 'D:\Ollama') { Remove-Item 'D:\Ollama' -Recurse -Force }
Expand-Archive -Path D:\Downloads\ollama.zip -DestinationPath 'D:\Ollama' -Force
$ollamaItem = Get-ChildItem -Path 'D:\Ollama' -Filter 'ollama.exe' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
if ($ollamaItem) { $ollama = $ollamaItem.FullName; $ollamaVer = & $ollama --version; Log "[Ollama] binary OK : $ollama : $ollamaVer" } else { Log "[Ollama] FAIL binary missing" }

[Environment]::SetEnvironmentVariable('OLLAMA_MODELS','D:\OllamaModels','Machine')
New-Item -ItemType Directory -Force -Path 'D:\OllamaModels' | Out-Null

Start-Process -FilePath $ollama -ArgumentList 'serve' -WindowStyle Hidden
Log "[Ollama] server starting, waiting for 11434 ..."
$up = $false
for ($i=0; $i -lt 40; $i++) {
    try { $r = curl.exe -s -o $null -w '%{http_code}' http://127.0.0.1:11434; if ($r -eq '200') { $up=$true; break } } catch {}
    Start-Sleep 3
}
Log "[Ollama] reachable: $up"

# ---------- 3. pull models ----------
Log "[Ollama] pulling qwen2.5:7b ..."
& $ollama pull qwen2.5:7b 2>&1 | ForEach-Object { Log "  pull7b: $_" }
Log "[Ollama] pulling qwen2.5:3b ..."
& $ollama pull qwen2.5:3b 2>&1 | ForEach-Object { Log "  pull3b: $_" }
Log "[Ollama] test run qwen2.5:3b ..."
& $ollama run qwen2.5:3b "用一句话中文回答：本地大模型测试是否成功？" 2>&1 | ForEach-Object { Log "  test: $_" }

# ---------- 4. OpenClaw ----------
$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
Log "[OpenClaw] npm install -g openclaw ..."
& D:\Nodejs\npm.cmd install -g openclaw 2>&1 | ForEach-Object { Log "  npm: $_" }
$ocVer = & openclaw --version 2>&1
Log "[OpenClaw] version: $ocVer"
$oc = 'openclaw'

[Environment]::SetEnvironmentVariable('OLLAMA_API_KEY','ollama-local','User')

Log "[OpenClaw] onboard (non-interactive, ollama, qwen2.5:7b) ..."
& $oc onboard --non-interactive --auth-choice ollama --custom-model-id qwen2.5:7b --accept-risk 2>&1 | ForEach-Object { Log "  onboard: $_" }
& $oc models set ollama/qwen2.5:7b 2>&1 | ForEach-Object { Log "  modelset: $_" }

# ---------- 5. scheduled tasks ----------
Log "[tasks] Ollama auto-start (SYSTEM on boot) ..."
schtasks /create /tn "OllamaServer" /tr "$ollama serve" /sc onstart /ru SYSTEM /rl highest /f 2>&1 | ForEach-Object { Log "  task: $_" }

Log "=== STAGE3 END ==="
