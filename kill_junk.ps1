$ErrorActionPreference = 'Continue'
$killNames = @('360DesktopLite64','360huabao','360TptMon','360Tray','360zipUpdate','360Safe','360ld','leidian','LDPlayer','ToDesk','SunloginClient','SunloginService','Oray','KuGou','Q360AMPPL')
foreach ($n in $killNames) {
    Get-Process -Name $n -ErrorAction SilentlyContinue | ForEach-Object {
        try { Stop-Process -Id $_.Id -Force -ErrorAction Stop; "KILLED PROC: $($_.Name) PID=$($_.Id)" } catch { "kill fail $($_.Name): $_" }
    }
}
Write-Output "DONE-KILL"
