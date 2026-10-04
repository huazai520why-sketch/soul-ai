$out = 'D:\probe.log'
function P($m){ $m | Tee-Object -Append $out }
"=== PROBE $(Get-Date -Format 'HH:mm:ss') ===" | Set-Content $out
# Node
if (Test-Path 'D:\Nodejs\node.exe') { P "Node(D:) : $(& D:\Nodejs\node.exe --version)" } else { P "Node(D:) : MISSING" }
$npmc = 'D:\Nodejs\npm.cmd'
if (Test-Path $npmc) { P "npm      : present" } else { P "npm      : MISSING" }
# Ollama
$oll = Get-ChildItem -Path 'D:\Ollama' -Filter 'ollama.exe' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
if ($oll) { P "Ollama   : $($oll.FullName) : $(& $oll.FullName --version 2>&1)" } else { P "Ollama   : MISSING" }
# port 11434
try { $r = curl.exe -s -o $null -w '%{http_code}' http://127.0.0.1:11434 } catch { $r = 'ERR' }
P "11434    : $r"
# models dir
if (Test-Path 'D:\OllamaModels') { $sz = (Get-ChildItem 'D:\OllamaModels' -Recurse -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum; P "Models   : dir exists, bytes=$sz" } else { P "Models   : no dir" }
# openclaw
$ocDir = "$env:APPDATA\npm\node_modules\openclaw"
$ocDirD = "D:\npm-global\node_modules\openclaw"
if (Test-Path $ocDir) { P "OpenClaw : installed at $ocDir" } elseif (Test-Path $ocDirD) { P "OpenClaw : installed at $ocDirD" } else { P "OpenClaw : not found in npm dirs" }
# downloads
Get-ChildItem 'D:\Downloads' -ErrorAction SilentlyContinue | ForEach-Object { P ("DL: {0}  {1} MB" -f $_.Name, [math]::Round($_.Length/1MB,1)) }
# disks
Get-PSDrive C,D,E -ErrorAction SilentlyContinue | ForEach-Object { P ("Disk {0}: free {1} GB" -f $_.Name, [math]::Round($_.Free/1GB,1)) }
P "=== PROBE END ==="
