$ErrorActionPreference = 'Continue'
$log = 'D:\install_wechat.log'
function Log($m){ $t=Get-Date -Format 'HH:mm:ss'; "$t $m" | Tee-Object -Append $log }

Log "=== WeChat install (Stage2b) ==="
Start-Process 'D:\Downloads\WeChatSetup.exe' -ArgumentList '/S' -Wait
$src = $null
for ($i=0; $i -lt 40; $i++) {
    if (Test-Path 'C:\Program Files (x86)\Tencent\WeChat') { $src='C:\Program Files (x86)\Tencent\WeChat'; break }
    if (Test-Path 'C:\Program Files\Tencent\WeChat') { $src='C:\Program Files\Tencent\WeChat'; break }
    Start-Sleep 3
}
if (-not $src) { $src='C:\Program Files (x86)\Tencent\WeChat' }

if (Test-Path $src) {
    Get-Process WeChat -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep 2
    if (Test-Path 'D:\WeChat') { Remove-Item 'D:\WeChat' -Recurse -Force }
    Move-Item $src 'D:\WeChat' -Force
    cmd /c "mklink /J `"$src`" `"D:\WeChat`"" 2>&1 | ForEach-Object { Log "[junction] $_" }
    New-Item -ItemType Directory -Force -Path 'D:\WeChatData' | Out-Null
    cmd /c "mklink /J `"C:\Users\JIAN\Documents\WeChat Files`" `"D:\WeChatData`"" 2>&1 | ForEach-Object { Log "[data junction] $_" }
    Log "WeChat -> D:\WeChat ; WeChat Files -> D:\WeChatData"
} else {
    Log "WARN WeChat install dir not found"
}
Log "=== done ==="
