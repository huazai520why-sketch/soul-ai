import paramiko, sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HOST = "192.168.10.1"; USER = "root"; PWD = "Jjh1314520."

def run(cli, c, timeout=60):
    _, out, err = cli.exec_command(c, timeout=timeout)
    return out.read().decode("utf-8", "replace"), err.read().decode("utf-8", "replace")

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(HOST, username=USER, password=PWD, timeout=15,
            look_for_keys=False, allow_agent=False)

print("=" * 70)
print("### A 进程 & 日志位置")
o, e = run(cli, "ps w | grep -iE 'clash|mihomo' | grep -v grep; echo '---'; "
                "ls -la /tmp/openclash.log* /etc/openclash/*.log 2>/dev/null; echo '---外部控制---'; "
                "grep -oE 'external-controller[^,]*' /etc/openclash/config/*.yaml 2>/dev/null | head; "
                "which curl")
print(o.rstrip())
if e.strip(): print("[stderr]", e.strip()[:300])

print("=" * 70)
print("### B 触发请求 + 抓新日志")
cmd = (
    "L=$(ls -t /tmp/openclash.log* 2>/dev/null | head -1); echo \"logfile=$L\"; "
    "S=$(wc -l < \"$L\" 2>/dev/null || echo 0); "
    "echo '>> 发起 curl github via 127.0.0.1:7890'; "
    "curl -x http://127.0.0.1:7890 -s -o /dev/null -w 'rc=%{http_code} t=%{time_total}\\n' --max-time 12 https://github.com 2>&1; "
    "echo '>> 发起 curl google'; "
    "curl -x http://127.0.0.1:7890 -s -o /dev/null -w 'rc=%{http_code} t=%{time_total}\\n' --max-time 12 https://www.google.com 2>&1; "
    "sleep 1; echo '>> 新增日志:'; tail -n +$((S+1)) \"$L\" 2>/dev/null | tail -50"
)
o, e = run(cli, cmd, timeout=60)
print(o.rstrip())
if e.strip(): print("[stderr]", e.strip()[:400])

print("=" * 70)
print("### C 该节点 delay 复测")
o, e = run(cli, "curl -s -H 'Authorization: Bearer U511PhZ1' "
                "'http://127.0.0.1:9090/proxies/%5Bss%5D%5B1x%5D%20%5BD%5D%20%5B%E7%9B%B4%E8%BF%9E%5D%20%5BSS%5D%20%E6%97%A5%E6%9C%AC%203/delay?timeout=6000&url=https%3A%2F%2Fgithub.com' 2>&1; echo; "
                "curl -s -H 'Authorization: Bearer U511PhZ1' 'http://127.0.0.1:9090/proxies/%5Bss%5D%5B1x%5D%20%5BD%5D%20%5B%E7%9B%B4%E8%BF%9E%5D%20%5BSS%5D%20%E6%97%A5%E6%9C%AC%203/delay?timeout=6000&url=http%3A%2F%2Fwww.gstatic.com%2Fgenerate_204' 2>&1")
print(o.rstrip())
if e.strip(): print("[stderr]", e.strip()[:300])

cli.close()
