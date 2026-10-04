[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'SilentlyContinue'
Set-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Services\Ld9BoxSup' -Name Start -Value 4 -Type DWord -ErrorAction SilentlyContinue
"LD9_DISABLED"
Restart-Computer -Force -ErrorAction Stop
"REBOOT_ISSUED"
