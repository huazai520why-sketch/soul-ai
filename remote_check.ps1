$ProgressPreference='SilentlyContinue'
$out=@()
$out += "=== local ports (node listener) ==="
try { $l = netstat -an | findstr /i "11434 18789 22 3389 5985" ; $out += ($l -join "`n") } catch { $out += "netstat err" }

$out += ""
$out += "=== WeChat on D: ==="
$out += "WeChat.exe D: = $(Test-Path 'D:\WeChat\WeChat.exe')"
$out += "Junction C:\Program Files\Tencent\WeChat = $(Test-Path 'C:\Program Files\Tencent\WeChat')"
$out += "WeChatData D: = $(Test-Path 'D:\WeChatData')"

$out += ""
$out += "=== Ollama (11434) ==="
try { $r = Invoke-RestMethod http://127.0.0.1:11434/api/tags -TimeoutSec 5; $out += "models: $($r.models.name -join ', ')" } catch { $out += "API ERR: $_" }

$out += ""
$out += "=== OpenClaw ==="
$oc = Test-Path 'C:\Users\JIAN\.openclaw\openclaw.json'
$out += "openclaw.json exists = $oc"
$env:Path='D:\Nodejs;D:\npm-global;'+$env:Path
try { $v = & 'D:\npm-global\openclaw.cmd' --version 2>&1 | Out-String; $out += "version: $($v.Trim())" } catch { $out += "ver ERR: $_" }
try { $g = Invoke-WebRequest http://127.0.0.1:18789 -TimeoutSec 5 -UseBasicParsing; $out += "gateway HTTP = $($g.StatusCode)" } catch { $out += "gateway ERR: $($_.Exception.Message)" }

$out += ""
$out += "=== scheduled tasks ==="
$out += (schtasks /query /fo LIST 2>$null | findstr /i "Ollama OpenClaw TaskName")

$out += ""
$out += "=== daemon log tail ==="
if (Test-Path 'D:\openclaw_daemon.log') { $out += (Get-Content 'D:\openclaw_daemon.log' -Tail 15 -Encoding ascii -ErrorAction SilentlyContinue | Out-String) }

$out | Out-File D:\check_final.log -Encoding ascii
$out -join "`n"
