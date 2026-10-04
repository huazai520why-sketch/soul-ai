import paramiko

host, port, user, pw = "192.168.10.1", 22, "root", "Jjh1314520."

cmds = [
    "echo '===== 系统时间/负载 ====='; date; uptime",
    "echo '===== 磁盘 ====='; df -h 2>/dev/null | head -20",
    "echo '===== 内存 ====='; free -m 2>/dev/null || cat /proc/meminfo | head -3",
    "echo '===== Docker 容器 ====='; docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' 2>&1",
    "echo '===== 青龙面板 5700 端口探测 ====='; curl -s -o /dev/null -w 'HTTP %{http_code}\n' --max-time 5 http://127.0.0.1:5700 2>&1 || echo 'curl失败/nc未装'",
    "echo '===== Freqtrade 进程 ====='; ps aux 2>/dev/null | grep -i freqtrade | grep -v grep | head",
    "echo '===== OpenWrt 版本 ====='; cat /etc/os-release 2>/dev/null | head -5; uname -a",
    "echo '===== 网络接口/IP ====='; ip -4 addr show 2>/dev/null | grep -E 'inet |^[0-9]' | head -20",
    "echo '===== 开机时长/温度 ====='; cat /proc/uptime 2>/dev/null; (sensors 2>/dev/null | head -5 || echo '无sensors')",
]

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
print(f"[*] 连接 {host}:{port} ...")
ssh.connect(host, port, user, pw, timeout=15)
print("[*] 已连接\n")

for c in cmds:
    try:
        stdin, stdout, stderr = ssh.exec_command(c, timeout=40)
        out = stdout.read().decode(errors="replace")
        err = stderr.read().decode(errors="replace")
        print(out)
        if err.strip():
            print("  [stderr]", err.strip())
        print()
    except Exception as e:
        print(f"  [命令执行异常] {e}\n")

ssh.close()
print("[*] 断开连接")
