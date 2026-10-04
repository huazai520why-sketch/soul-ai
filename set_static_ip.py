import paramiko

HOST = "192.168.10.1"
PORT = 22
USER = "root"
PASS = "Jjh1314520."

cmds = [
    # 幂等：先确保段存在并设置字段
    "uci set dhcp.je5902j=host",
    "uci set dhcp.je5902j.name='DESKTOP-JE5902J'",
    "uci set dhcp.je5902j.mac='00:E0:4F:19:54:7D'",
    "uci set dhcp.je5902j.ip='192.168.10.165'",
    "uci set dhcp.je5902j.enabled='1'",
    "uci commit dhcp",
    "/etc/init.d/dnsmasq restart",
    "echo '--- 验证 dhcp host 绑定 ---'",
    "uci show dhcp | grep -E 'je5902j|host\\[0\\]'",
]

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
try:
    ssh.connect(HOST, port=PORT, username=USER, password=PASS, timeout=15)
    stdin, stdout, stderr = ssh.exec_command("; ".join(cmds), timeout=40)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    print(out)
    if err.strip():
        print("STDERR:", err)
finally:
    ssh.close()
