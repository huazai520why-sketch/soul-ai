import paramiko

host, port, user, pw = "192.168.10.1", 22, "root", "Jjh1314520."
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(host, port, user, pw, timeout=15)

def run(c):
    stdin, stdout, stderr = ssh.exec_command(c, timeout=40)
    return stdout.read().decode(errors="replace") + stderr.read().decode(errors="replace")

print("=== OpenClash config 目录 ===")
print(run("ls -la /etc/openclash/config/ 2>/dev/null"))
print("\n=== github 是否写进规则? (全量配置搜 github) ===")
print(run("grep -rni 'github' /etc/openclash/config/ 2>/dev/null | head -40"))
print("\n=== 运行中的配置 /etc/openclash/config.yaml 里的 github 规则 ===")
print(run("grep -ni 'github' /etc/openclash/config.yaml 2>/dev/null | head -40"))
print("\n=== 默认代理组 / 规则段 (grep default & rule) ===")
print(run("grep -niE '^default|default:|proxy-groups|rules:' /etc/openclash/config.yaml 2>/dev/null | head -40"))
ssh.close()
