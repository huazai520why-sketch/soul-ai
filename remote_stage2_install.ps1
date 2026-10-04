$ErrorActionPreference = 'Continue'
$log = 'D:\install.log'
function Log($m){ $t = Get-Date -Format 'HH:mm:ss'; "$t $m" | Tee-Object -Append $log }

Log "=== STAGE2 INSTALL START ==="

# ---------- Node.js to D:\Nodejs ----------
Log "[Node] installing to D:\Nodejs"
Start-Process msiexec.exe -ArgumentList '/i','D:\Downloads\node.msi','INSTALLDIR=D:\Nodejs','/qn' -Wait
$nodeDir = 'D:\Nodejs'
$nodeExe = "$nodeDir\node.exe"
if (Test-Path $nodeExe) { Log "[Node] OK node.exe present" } else { Log "[Node] FAIL node.exe missing" }

# add to machine PATH
$p = [Environment]::GetEnvironmentVariable('Path','Machine')
if ($p -notlike "*$nodeDir*") { [Environment]::SetEnvironmentVariable('Path', "$p;$nodeDir", 'Machine'); Log '[Node] added to Machine PATH' }

# npm global prefix/cache on D:
& "$nodeDir\npm.cmd" config set prefix 'D:\npm-global' | Out-Null
& "$nodeDir\npm.cmd" config set cache 'D:\npm-cache' | Out-Null
$np = [Environment]::GetEnvironmentVariable('Path','Machine')
if ($np -notlike '*D:\npm-global*') { [Environment]::SetEnvironmentVariable('Path', "$np;D:\npm-global", 'Machine'); Log '[npm] global prefix set to D:\npm-global' }

# refresh env for this session
$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')

# ---------- Ollama ----------
Log "[Ollama] set OLLAMA_MODELS=D:\OllamaModels"
[Environment]::SetEnvironmentVariable('OLLAMA_MODELS','D:\OllamaModels','Machine')
New-Item -ItemType Directory -Force -Path 'D:\OllamaModels' | Out-Null
Log "[Ollama] running installer (silent)"
Start-Process 'D:\Downloads\OllamaSetup.exe' -ArgumentList '/S','/D=D:\Ollama' -Wait
$ollama = $null
for ($i=0; $i -lt 60; $i++) {
    $ollama = Get-ChildItem -Path 'D:\Ollama','C:\Users\JIAN\AppData\Local\Programs\Ollama' -Filter 'ollama.exe' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($ollama) { break }
    Start-Sleep 3
}
if ($ollama) { Log "[Ollama] found at $($ollama.FullName)" } else { Log "[Ollama] WARN ollama.exe not found" }

# start ollama server in background and create startup task for always-on
if ($ollama) {
    Start-Process -FilePath $ollama.FullName -ArgumentList 'serve' -WindowStyle Hidden
    Log "[Ollama] server starting..."
    # always-on scheduled task (runs at boot, no login needed)
    $taskCmd = "$($ollama.FullName) serve"
    schtasks /create /tn "OllamaServer" /tr $taskCmd /sc onstart /ru SYSTEM /rl highest /f 2>&1 | ForEach-Object { Log "[Ollama task] $_" }
}

# ---------- WeChat ----------
Log "[WeChat] silent install"
Start-Process 'D:\Downloads\WeChatSetup.exe' -ArgumentList '/S' -Wait
$wechatSrc = $null
for ($i=0; $i -lt 40; $i++) {
    if (Test-Path 'C:\Program Files (x86)\Tencent\WeChat') { $wechatSrc = 'C:\Program Files (x86)\Tencent\WeChat'; break }
    if (Test-Path 'C:\Program Files\Tencent\WeChat') { $wechatSrc = 'C:\Program Files\Tencent\WeChat'; break }
    Start-Sleep 3
}
if (-not $wechatSrc) { $wechatSrc = 'C:\Program Files (x86)\Tencent\WeChat' }
if (Test-Path $wechatSrc) {
    Log "[WeChat] installed at $wechatSrc ; relocating to D:\WeChat"
    Get-Process WeChat -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep 2
    if (Test-Path 'D:\WeChat') { Remove-Item 'D:\WeChat' -Recurse -Force }
    Move-Item $wechatSrc 'D:\WeChat' -Force
    cmd /c "mklink /J `"$wechatSrc`" `"D:\WeChat`"" 2>&1 | ForEach-Object { Log "[WeChat junction] $_" }
    # redirect WeChat data folder to D:
    New-Item -ItemType Directory -Force -Path 'D:\WeChatData' | Out-Null
    cmd /c "mklink /J `"C:\Users\JIAN\Documents\WeChat Files`" `"D:\WeChatData`"" 2>&1 | ForEach-Object { Log "[WeChat data junction] $_" }
    Log "[WeChat] done"
} else {
    Log "[WeChat] WARN install dir not found"
}

Log "=== STAGE2 INSTALL END ==="
