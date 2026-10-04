import paramiko, sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HOST = "192.168.10.1"; USER = "root"; PWD = "Jjh1314520."

def run(cli, c, timeout=90):
    _, out, err = cli.exec_command(c, timeout=timeout)
    return out.read().decode("utf-8", "replace"), err.read().decode("utf-8", "replace")

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(HOST, username=USER, password=PWD, timeout=15,
            look_for_keys=False, allow_agent=False)

print("### 1. 重拨前 WAN")
o, _ = run(cli, "ifstatus wan | grep -E '\"up\"|\"address\"|uptime' | head -8; echo '--旧DNS--'; cat /tmp/resolv.conf.d/resolv.conf.auto")
print(o.rstrip())

print("\n### 2. 设置 WAN DNS = 腾讯+阿里, 关闭 peer DNS")
o, e = run(cli, "uci -q delete network.wan.dns; "
                "uci add_list network.wan.dns='223.5.5.5'; "
                "uci add_list network.wan.dns='119.29.29.29'; "
                "uci set network.wan.peerdns='0'; "
                "uci commit network; echo '--after--'; uci show network.wan | grep -E 'dns|peerdns'")
print(o.rstrip())
if e.strip(): print("[stderr]", e.strip()[:300])

print("\n### 3. 重新拨号")
o, e = run(cli, "ifdown wan; sleep 3; ifup wan; echo 'redial issued'", timeout=60)
print(o.rstrip())
if e.strip(): print("[stderr]", e.strip()[:300])

print("\n### 4. 等待 WAN 重新上线 (最多 45s)")
for i in range(9):
    time.sleep(5)
    o, _ = run(cli, "ifstatus wan 2>/dev/null | grep -E '\"up\": true' || echo DOWN")
    up = "true" in o
    if up:
        print(f"  [{5*(i+1)}s] 已上线")
        break
    print(f"  [{5*(i+1)}s] 等待中...")
o, _ = run(cli, "ifstatus wan | grep -E '\"address\"|uptime' | head -6; echo '--新resolv.auto--'; cat /tmp/resolv.conf.d/resolv.conf.auto; echo '--公网出口--'; curl -s4 --connect-timeout 8 http://ip.3322.net 2>/dev/null || echo '(取出口IP超时)'")
print(o.rstrip())
cli.close()
