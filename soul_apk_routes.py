# -*- coding: utf-8 -*-
"""从 base.apk 的 dex 里挖 Soul 内部路由表 soul://..."""
import zipfile, re, sys, io, os

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

APK = r"D:\MuMuPlayer\vms\MuMuPlayer-15.0-0\private_shared\probe\base.apk"
STOPS = ('"', "'", ' ', ',', ';', '<', '>', ')', ']', '}')

def main():
    if not os.path.exists(APK):
        print("APK 不在:", APK, "→ 先跑 soul_probe_db.py 或用 mumu 拷出来")
        return
    z = zipfile.ZipFile(APK)
    dexes = sorted([n for n in z.namelist() if n.endswith('.dex')])
    found = {}
    for d in dexes:
        data = z.read(d)
        for m in re.finditer(rb'soul://', data):
            i = m.start()
            a = i
            while a > 0 and 32 <= data[a-1] <= 126:
                a -= 1
                if i - a > 50: break
            b = m.end()
            while b < len(data) and 32 <= data[b] <= 126:
                b += 1
                if b - m.end() > 200: break
            try:
                s = data[a:b].decode('ascii')
            except Exception:
                continue
            pos = s.find('soul://')
            core = s[pos:]
            for ch in STOPS:
                j = core.find(ch)
                if j > 0:
                    core = core[:j]
            if len(core) < 14:
                continue
            found.setdefault(core, set()).add(d)
        del data

    print("=== 内部路由 %d 条 ===" % len(found))
    # 按 path 归类
    groups = {}
    for s in found:
        rest = s[len('soul://'):]
        host = rest.split('/')[0]
        path = '/' + '/'.join(rest.split('/')[1:]).split('?')[0]
        groups.setdefault((host, path), set()).add(s)
    for (host, path), vals in sorted(groups.items()):
        ex = sorted(vals)[0]
        print("  %-58s (变体%d)" % (path[:58], len(vals)))
        if len(ex) > 90:
            print("      例: %s" % ex[:150])
    print()
    print("=== 含 chat/im/msg 的路由 ===")
    for s in sorted(found):
        low = s.lower()
        if any(k in low for k in ('chat', '/im', 'msg', 'send', 'conversation', 'session')):
            print("  ", s[:140])

if __name__ == "__main__":
    main()
