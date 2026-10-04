import paramiko, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOST = "192.168.10.1"; USER = "root"; PWD = "Jjh1314520."

cmds = [
    "echo '###A dnsmasq 上游'; ls /tmp/resolv.conf.d/ 2>/dev/null; echo '--auto--'; cat /tmp/resolv.conf.d/resolv.conf.auto 2>/dev/null; echo '--dnsmasq server=--'; grep -rhE '^server=|^no-resolv' /tmp/dnsmasq.d/ 2>/dev/null | head -20; echo '--dnsmasq conf server--'; grep -nE 'server|no-resolv|resolv-file' /var/etc/dnsmasq.conf.* 2>/dev/null | head -20",

    "echo '###B node域名解析(阿里)'; nslookup ss.003.node-for-bigairport.win 223.5.5.5 2>&1 | tail -6; echo '--腾讯--'; nslookup ss.003.node-for-bigairport.win 119.29.29.29 2>&1 | tail -6; echo '--本地--'; nslookup ss.003.node-for-bigairport.win 127.0.0.1 2>&1 | tail -6",

    "echo '###C 镜像站解析'; nslookup bigairport-mirror.com 223.5.5.5 2>&1 | tail -6",

    "echo '###D node server:port'; grep -oE 'server: ss\\.[0-9]+\\.node-for-bigairport\\.win, port: [0-9]+' /etc/openclash/config/*.yaml 2>/dev/null | sort -u | head -30",

    "echo '###E 海外直连 TCP 测试'; t(){ timeout 5 nc -w 3 \"$1\" \"$2\" </dev/null >/dev/null 2>&1 && echo \"$1:$2 OK\" || echo \"$1:$2 FAIL\"; }; t 1.1.1.1 443; t 8.8.8.8 53; t 20.205.243.166 443; t 140.82.112.3 443; t 223.5.5.5 443",

    "echo '###F node直连 TCP 测试'; t(){ timeout 5 nc -w 3 \"$1\" \"$2\" </dev/null >/dev/null 2>&1 && echo \"$1:$2 OK\" || echo \"$1:$2 FAIL\"; }; for hp in $(grep -oE 'server: ss\\.[0-9]+\\.node-for-bigairport\\.win, port: [0-9]+' /etc/openclash/config/*.yaml 2>/dev/null | sed -E 's/server: //; s/, port: / /' | sort -u | head -6); do set -- $hp; t \"$1\" \"$2\"; done",

    "echo '###G 解析 node 域名真实IP'; for d in ss.003.node-for-bigairport.win ss.009.node-for-bigairport.win; do echo -n \"$d -> \"; nslookup $d 223.5.5.5 2>/dev/null | awk '/^Address/{print $NF}' | tail -1; done",
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
        _, out, err = cli.exec_command(c, timeout=45)
        o = out.read().decode("utf-8", "replace")
        e = err.read().decode("utf-8", "replace")
        print(o.rstrip())
        if e.strip():
            print("[stderr]", e.strip()[:400])
    except Exception as ex:
        print("ERR:", repr(ex))
cli.close()
