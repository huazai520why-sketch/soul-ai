import paramiko, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOST = "192.168.10.1"; USER = "root"; PWD = "Jjh1314520."

t = 't(){ timeout 6 nc -w 4 "$1" "$2" </dev/null >/dev/null 2>&1 && echo "  OK   $1:$2" || echo "  FAIL $1:$2"; }; '

cmds = [
    ("TCP 对照 - 国内", t +
     "echo '-- 网关/本地 --'; t 192.168.10.1 53; t 127.0.0.1 53; "
     "echo '-- 国内公共 --'; t 223.5.5.5 53; t 223.5.5.5 443; t 119.29.29.29 53; t 114.114.114.114 53; t 218.201.17.2 53; t baidu.com 443"),

    ("TCP 对照 - 海外", t +
     "t 1.1.1.1 443; t 1.1.1.1 80; t 8.8.8.8 53; t 8.8.4.4 53; t 20.205.243.166 443; t 140.82.112.3 443"),

    ("TCP 对照 - 机场节点(AWS)", t +
     "t 44.250.146.93 20003; t 35.81.167.10 20003; t 44.234.8.21 20003"),

    ("ping 对照", "echo '-- 网关 --'; ping -c 3 -W 2 10.193.0.1 2>&1 | tail -3; "
     "echo '-- 阿里DNS --'; ping -c 3 -W 2 223.5.5.5 2>&1 | tail -3; "
     "echo '-- 1.1.1.1 --'; ping -c 3 -W 2 1.1.1.1 2>&1 | tail -3; "
     "echo '-- 8.8.8.8 --'; ping -c 3 -W 3 8.8.8.8 2>&1 | tail -3"),

    ("traceroute 海外 + 工具", "which traceroute mtr nc nslookup 2>&1; echo '-- trace 223.5.5.5 --'; traceroute -n -m 6 -w 2 223.5.5.5 2>&1 | head -8; echo '-- trace 1.1.1.1 --'; traceroute -n -m 8 -w 2 1.1.1.1 2>&1 | head -12"),
]

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
try:
    cli.connect(HOST, username=USER, password=PWD, timeout=15,
                look_for_keys=False, allow_agent=False)
except Exception as e:
    print("SSH FAIL:", repr(e)); sys.exit(1)

for label, c in cmds:
    print("\n" + "=" * 70)
    print("### " + label)
    try:
        _, out, err = cli.exec_command(c, timeout=70)
        o = out.read().decode("utf-8", "replace")
        e = err.read().decode("utf-8", "replace")
        print(o.rstrip())
        if e.strip():
            print("[stderr]", e.strip()[:400])
    except Exception as ex:
        print("ERR:", repr(ex))
cli.close()
