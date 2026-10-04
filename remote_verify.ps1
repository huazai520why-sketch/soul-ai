$out = 'D:\verify.log'
function P($m){ $m | Tee-Object -Append $out }
"=== VERIFY $(Get-Date -Format 'HH:mm:ss') ===" | Set-Content $out

# WeChat
$wxJunc = 'C:\Program Files\Tencent\WeChat'
$wxReal = 'D:\WeChat'
if (Test-Path $wxReal) {
    $exe = Get-ChildItem $wxReal -Filter 'WeChat.exe' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    P "WeChat realdir D:\WeChat exists; exe: $(if($exe){$exe.FullName}else{'NOT FOUND'})"
} else { P "WeChat D:\WeChat MISSING" }
$it = Get-Item $wxJunc -ErrorAction SilentlyContinue
if ($it) { P "WeChat junction: $($it.LinkType) -> $($it.Target)" } else { P "WeChat junction MISSING" }

# WeChat data junction
$wd = Get-Item 'C:\Users\JIAN\Documents\WeChat Files' -ErrorAction SilentlyContinue
if ($wd) { P "WeChatData junction: $($wd.LinkType) -> $($wd.Target)" } else { P "WeChatData junction: none" }

# Models pull progress
if (Test-Path 'D:\OllamaModels') {
    $mb = (Get-ChildItem 'D:\OllamaModels' -Recurse -File -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum
    P "OllamaModels size MB: $([math]::Round($mb/1MB,1))"
}
if (Test-Path 'D:\pull3b.log') { P "pull3b tail: $((Get-Content D:\pull3b.log -Tail 1))" }
if (Test-Path 'D:\pull7b.log') { P "pull7b tail: $((Get-Content D:\pull7b.log -Tail 1))" }
$ollama = (Get-ChildItem -Path 'D:\Ollama' -Filter 'ollama.exe' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1).FullName
$env:OLLAMA_MODELS = 'D:\OllamaModels'
P "ollama list:"
& $ollama list 2>&1 | ForEach-Object { P "  $_" }
P "=== VERIFY END ==="
