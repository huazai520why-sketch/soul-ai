$out='D:\tags.log'
$ollama=(Get-ChildItem -Path 'D:\Ollama' -Filter 'ollama.exe' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1).FullName
$r = curl.exe -s -o $null -w '%{http_code}' http://127.0.0.1:11434
if ($r -ne '200') { Start-Process -FilePath $ollama -ArgumentList 'serve' -WindowStyle Hidden; Start-Sleep 6 }
$tags = curl.exe -s http://127.0.0.1:11434/api/tags
"11434=$r`ntags=$tags" | Set-Content $out
