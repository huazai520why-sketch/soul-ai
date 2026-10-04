$out='D:\ochelp.log'
$shim='D:\npm-global\openclaw.cmd'
"=== onboard --help ===" | Set-Content $out
& $shim onboard --help *>> $out
"=== gateway --help ===" | Add-Content $out
& $shim gateway --help *>> $out
"=== top help (locale/lang search) ===" | Add-Content $out
& $shim --help *>> $out
