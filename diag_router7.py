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

print("### 节点服务器 IP ping (ICMP)")
cmd = (
    "for ip in 44.250.146.93 35.81.167.10 13.158.7.205 44.234.8.21; do "
    "  echo \"== $ip ==\"; ping -c 3 -W 2 $ip 2>&1 | tail -3; "
    "done"
)
o, _ = run(cli, cmd, 60); print(o.rstrip())

print("\n### 对照 IP ping")
cmd2 = (
    "echo '== github 20.205.243.166 =='; ping -c 3 -W 2 20.205.243.166 2>&1 | tail -3; "
    "echo '== 1.1.1.1 =='; ping -c 3 -W 2 1.1.1.1 2>&1 | tail -3; "
    "echo '== 8.8.8.8 =='; ping -c 3 -W 2 8.8.8.8 2>&1 | tail -3; "
    "echo '== AWS 通用 3.5.140.1 =='; ping -c 3 -W 2 3.5.140.1 2>&1 | tail -3"
)
o, _ = run(cli, cmd2, 60); print(o.rstrip())
cli.close()
