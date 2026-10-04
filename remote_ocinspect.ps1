$out='D:\ocinspect.log'
"=== OC INSPECT ===" | Set-Content $out
$pkg='D:\npm-global\node_modules\openclaw'
"--- dir listing ---" | Add-Content $out
Get-ChildItem $pkg -ErrorAction SilentlyContinue | Select-Object Name,Length | ForEach-Object { "$($_.Name)  $($_.Length)" | Add-Content $out }
"--- package.json ---" | Add-Content $out
if (Test-Path "$pkg\package.json") { Get-Content "$pkg\package.json" -Raw | Add-Content $out }
"--- npm global list ---" | Add-Content $out
& D:\Nodejs\npm.cmd -g ls --depth=0 2>&1 | Add-Content $out
"--- npm view openclaw (registry) ---" | Add-Content $out
& D:\Nodejs\npm.cmd view openclaw version description bin 2>&1 | Add-Content $out
"=== END ===" | Add-Content $out
