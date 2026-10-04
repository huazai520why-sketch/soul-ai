import paramiko

HOST = "192.168.10.1"
PORT = 22
USER = "root"
PASS = "Jjh1314520."

cmd = (
    "echo '=== 当前 DHCP 租约 (/tmp/dhcp.leases) ==='; "
    "cat /tmp/dhcp.leases; "
    "echo; echo '=== 已有静态绑定 (uci dhcp host) ==='; "
    "uci show dhcp | grep -i host || echo '(无静态绑定)'; "
    "echo; echo '=== 新主机名猜测 (最近上线) ==='; "
    "cat /tmp/dhcp.leases | awk '{print $4}' | sort -u"
)

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
try:
    ssh.connect(HOST, port=PORT, username=USER, password=PASS, timeout=15)
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    print(out)
    if err.strip():
        print("STDERR:", err)
finally:
    ssh.close()
