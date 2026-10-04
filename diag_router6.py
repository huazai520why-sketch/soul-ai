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

print("### 节点服务器 TCP 连通性(connect 计时)")
cmd = (
    "for s in 'ss.003.node-for-bigairport.win:20003' 'ss.009.node-for-bigairport.win:20003' "
    "'ss.033.node-for-bigairport.win:29005' '13.158.7.205:29005' '44.250.146.93:20003'; do "
    "  echo \"== $s ==\"; "
    "  curl -sS -o /dev/null --connect-timeout 6 "
    "-w '  http=%{http_code} connect=%{time_connect}s total=%{time_total}s\n' \"http://$s/\" 2>&1 | head -2; "
    "done"
)
o, e = run(cli, cmd, timeout=90)
print(o.rstrip())

print("\n### 对照 - 国内/海外 TCP")
cmd2 = (
    "curl -sS -o /dev/null --connect-timeout 6 -w 'baidu   http=%{http_code} connect=%{time_connect}s\n' http://www.baidu.com/ 2>&1; "
    "curl -sS -o /dev/null --connect-timeout 6 -w 'aliDNS  http=%{http_code} connect=%{time_connect}s\n' http://223.5.5.5/ 2>&1; "
    "curl -sS -o /dev/null --connect-timeout 6 -w 'gh-IP   http=%{http_code} connect=%{time_connect}s\n' https://20.205.243.166/ 2>&1; "
    "curl -sS -o /dev/null --connect-timeout 6 -w 'cf-IP   http=%{http_code} connect=%{time_connect}s\n' https://1.1.1.1/ 2>&1; "
    "curl -sS -o /dev/null --connect-timeout 6 -w 'aws-44  http=%{http_code} connect=%{time_connect}s\n' https://44.250.146.93/ 2>&1"
)
o, e = run(cli, cmd2, timeout=60)
print(o.rstrip())

cli.close()
