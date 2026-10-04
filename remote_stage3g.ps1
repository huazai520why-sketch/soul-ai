$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$log = 'D:\stage3g.log'
function Log($m){ $t=Get-Date -Format 'HH:mm:ss'; "$t $m" | Tee-Object -Append $log }
"=== STAGE3G START (pid $PID) ===" | Set-Content $log

$npm = 'D:\Nodejs\npm.cmd'
$node = 'D:\Nodejs\node.exe'
$env:Path = 'D:\Nodejs;D:\npm-global;' + [Environment]::GetEnvironmentVariable('Path','Machine')

# show available versions / latest
$dt = & $npm view openclaw dist-tags 2>&1 | Out-String
Log "[npm] dist-tags: $dt"

# remove placeholder, install real version
Log "[npm] uninstalling placeholder ..."
& $npm uninstall -g openclaw *>> D:\ocinstall.log
Log "[npm] installing openclaw@2026.7.1-2 (real) ..."
& $npm install -g openclaw@2026.7.1-2 *>> D:\ocinstall.log
if (Test-Path D:\ocinstall.log) { Get-Content D:\ocinstall.log -Tail 6 | ForEach-Object { Log "  npm: $_" } }

# locate real cli
$pkg = 'D:\npm-global\node_modules\openclaw'
$mjs = Join-Path $pkg 'openclaw.mjs'
$shim = 'D:\npm-global\openclaw.cmd'
Log "[OpenClaw] pkg files:"
Get-ChildItem $pkg -ErrorAction SilentlyContinue | Select-Object -First 20 | ForEach-Object { Log "    $($_.Name)" }
Log "[OpenClaw] shim exists: $(Test-Path $shim) ; mjs exists: $(Test-Path $mjs)"

# version
if (Test-Path $shim) { $ver = & $shim --version 2>&1 | Out-String } elseif (Test-Path $mjs) { $ver = & $node $mjs --version 2>&1 | Out-String } else { $ver = 'NO ENTRY' }
Log "[OpenClaw] version: $ver"
Log "=== STAGE3G END ==="
