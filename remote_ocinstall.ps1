$ErrorActionPreference = 'Continue'
$env:npm_config_prefix = 'D:\npm-global'
$env:npm_config_cache  = 'D:\npm-cache'
$env:Path = 'D:\Nodejs;D:\npm-global;' + $env:Path
'START ' + (Get-Date -Format 'HH:mm:ss') | Set-Content D:\ocinstall_status.log
& D:\Nodejs\npm.cmd install -g openclaw@2026.7.1-2 --no-fund --no-audit *>> D:\ocinstall.log
$pkg = 'D:\npm-global\node_modules\openclaw'
$mjs = Join-Path $pkg 'openclaw.mjs'
$shim = 'D:\npm-global\openclaw.cmd'
"shim=$([bool](Test-Path $shim)) mjs=$([bool](Test-Path $mjs))" | Add-Content D:\ocinstall_status.log
if (Test-Path $pkg) { (Get-ChildItem $pkg | Measure-Object).Count | ForEach-Object { "pkgfiles=$_" | Add-Content D:\ocinstall_status.log } }
'DONE ' + (Get-Date -Format 'HH:mm:ss') | Add-Content D:\ocinstall_status.log
