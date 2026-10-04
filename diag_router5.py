import paramiko, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HOST = "192.168.10.1"; USER = "root"; PWD = "Jjh1314520."

def run(cli, c, timeout=90):
    _, out, err = cli.exec_command(c, timeout=timeout)
    return out.read().decode("utf-8", "replace"), err.read().decode("utf-8", "replace")

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(HOST, username=USER, password=PWD, timeout=15,
            look_for_keys=False, allow_agent=False)

print("=" * 70)
print("### 监听端口")
o, e = run(cli, "netstat -tlnp 2>/dev/null | grep -E '7890|7891|7893|9090|7874' ; echo '--- iptables nat 重定向 ---'; "
                "iptables -t nat -L -n 2>/dev/null | grep -E '789|REDIRECT|DNAT' | head -20")
print(o.rstrip())

print("=" * 70)
print("### 从路由器经 LAN 代理请求 + 抓日志")
cmd = (
    "L=/tmp/openclash.log; "
    "for P in 7890 7893; do "
    "  echo \"===== port $P =====\"; "
    "  S=$(wc -l < \"$L\"); "
    "  curl -x http://192.168.10.1:$P -s -o /dev/null -w \"  lan-github rc=%{http_code} t=%{time_total}\\n\" --max-time 12 https://github.com 2>&1; "
    "  curl -x http://192.168.10.1:$P -s -o /dev/null -w \"  lan-gstatic rc=%{http_code} t=%{time_total}\\n\" --max-time 12 http://www.gstatic.com/generate_204 2>&1; "
    "  sleep 1; echo '  --- 新日志 ---'; tail -n +$((S+1)) \"$L\" 2>/dev/null | grep -iE 'github|gstatic|error|reject|timeout|dns' | tail -25; "
    "done"
)
o, e = run(cli, cmd, timeout=90)
print(o.rstrip())
if e.strip(): print("[stderr]", e.strip()[:400])

cli.close()
