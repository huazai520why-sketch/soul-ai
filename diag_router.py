import paramiko, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOST = "192.168.10.1"
USER = "root"
PWD = "Jjh1314520."

cmds = [
    "echo '###1 WAN 状态'; ifstatus wan 2>/dev/null | grep -E '\"up\"|\"proto\"|\"ipv4-address\"|\"address\"|uptime|dns-server|\"nexthop\"' | head -30",
    "echo '###2 PPPoE 接口'; ifconfig 2>/dev/null | grep -A3 -E 'pppoe|ppp-wan' | head -20; echo '-- 公网IP --'; ubus call network.interface.wan status 2>/dev/null | grep -E 'address|uptime' | head -6",
    "echo '###3 resolv.conf'; cat /etc/resolv.conf 2>/dev/null",
    "echo '###4 dnsmasq/DNS 配置'; uci show dhcp 2>/dev/null | grep -iE 'dns|resolv|server' | head -20; echo '--network dns--'; uci show network 2>/dev/null | grep -iE 'dns|peerdns' | head -20",
    "echo '###5 本地解析 github'; nslookup github.com 127.0.0.1 2>&1 | head -20",
    "echo '###6 阿里DNS 解析 github'; nslookup github.com 223.5.5.5 2>&1 | head -20",
    "echo '###7 腾讯DNS 解析 github'; nslookup github.com 119.29.29.29 2>&1 | head -20",
    "echo '###8 ping github 直连'; ping -c 3 -W 2 github.com 2>&1 | tail -6",
    "echo '###9 curl github 直连'; curl -s -o /dev/null -w '%{http_code} %{time_total}s\\n' --max-time 12 https://github.com 2>&1",
    "echo '###10 openclash 状态'; /etc/init.d/openclash status 2>&1 | head -5; ps w | grep -c openclash",
    "echo '###11 openclash DNS 配置'; grep -n -iE 'nameserver|fallback|enhanced-mode|fake-ip|default-nameserver' /etc/openclash/config/*.yaml 2>/dev/null | head -40",
    "echo '###12 openclash enabled'; uci show openclash 2>/dev/null | grep -iE 'enable|dns|mode' | head -20",
    "echo '###13 机场节点 server 列表'; grep -oE 'server: [^ ]+' /etc/openclash/config/*.yaml 2>/dev/null | sort -u | head -30",
    "echo '###14 pppoe 日志'; logread 2>/dev/null | grep -iE 'pppoe|ppp|wan' | tail -15",
]

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
try:
    cli.connect(HOST, username=USER, password=PWD, timeout=15,
                look_for_keys=False, allow_agent=False)
except Exception as e:
    print("SSH FAIL:", repr(e)); sys.exit(1)

for c in cmds:
    print("\n" + "=" * 70)
    try:
        _, out, err = cli.exec_command(c, timeout=40)
        o = out.read().decode("utf-8", "replace")
        e = err.read().decode("utf-8", "replace")
        print(o.rstrip())
        if e.strip():
            print("[stderr]", e.strip()[:500])
    except Exception as ex:
        print("ERR:", repr(ex))
cli.close()
