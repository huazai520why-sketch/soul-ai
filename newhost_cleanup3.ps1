$ErrorActionPreference = 'Continue'
$log = "C:\Users\wbadmin\cleanup_log3.txt"
"=== CLEANUP3 START $(Get-Date) ===" | Out-File $log -Encoding ascii

function Del($p) {
    if (-not (Test-Path $p)) { "SKIP (missing): $p" | Tee-Object -FilePath $log -Append; return }
    try {
        Remove-Item $p -Recurse -Force -ErrorAction Stop
        if (Test-Path $p) { "PARTIAL: $p" | Tee-Object -FilePath $log -Append } else { "DELETED: $p" | Tee-Object -FilePath $log -Append }
    } catch {
        "FAILED: $p -> $($_.Exception.Message)" | Tee-Object -FilePath $log -Append
    }
}

# kill junk procs again
$killNames = @('360DesktopLite64','360huabao','360TptMon','360Tray','360zipUpdate','360Safe','360ld','leidian','LDPlayer','ToDesk','SunloginClient','Oray','KuGou','Q360AMPPL')
foreach ($n in $killNames) { Get-Process -Name $n -ErrorAction SilentlyContinue | ForEach-Object { try { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue } catch {} } }

Write-Output "--- C: root junk (SKIP 360* due to 360 self-protection lock) ---"
Del "C:\DaBaiCai"; Del "C:\ESD"; Del "C:\inetpub"; Del "C:\KuGou"; Del "C:\leidian"
Del "C:\install.exe"; Del "C:\install.ini"; Del "C:\install.res.2052.dll"
Del "C:\eula.2052.txt"; Del "C:\globdata.ini"; Del "C:\vcredist.bmp"; Del "C:\VC_RED.cab"; Del "C:\VC_RED.MSI"

Write-Output "--- Program Files junk ---"
Del "C:\Program Files\ldplayer9box"; Del "C:\Program Files\Oray"; Del "C:\Program Files\Tencent"; Del "C:\Program Files\ToDesk"

Write-Output "--- Program Files (x86) junk ---"
Del "C:\Program Files (x86)\360"; Del "C:\Program Files (x86)\KuGou"

Write-Output "--- ProgramData junk ---"
Del "C:\ProgramData\360safe"; Del "C:\ProgramData\360SD"; Del "C:\ProgramData\deepscan"
Del "C:\ProgramData\KuGou"; Del "C:\ProgramData\NeteaseWinDev"; Del "C:\ProgramData\Oray"
Del "C:\ProgramData\OrayClient"; Del "C:\ProgramData\Tencent"; Del "C:\ProgramData\tianxiang"
Del "C:\ProgramData\Windows Master Store"; Del "C:\ProgramData\ZulerCoreTools"; Del "C:\ProgramData\OEM Links"

Write-Output "--- User profile JIANG ---"
Del "C:\Users\JIAN"

Write-Output "--- Wipe D: ---"
Get-ChildItem "D:\" -Force -ErrorAction SilentlyContinue | ForEach-Object { Del $_.FullName }
Write-Output "--- Wipe E: ---"
Get-ChildItem "E:\" -Force -ErrorAction SilentlyContinue | ForEach-Object { Del $_.FullName }

Write-Output "=== CLEANUP3 DONE ==="
"=== CLEANUP3 DONE $(Get-Date) ===" | Out-File $log -Append -Encoding ascii
