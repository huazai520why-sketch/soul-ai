import json, urllib.request, urllib.parse, sys, urllib.error
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
CTRL = "http://192.168.10.1:9090"
SEC = "U511PhZ1"

def api(p):
    req = urllib.request.Request(CTRL + p, headers={"Authorization": "Bearer " + SEC})
    return json.loads(urllib.request.urlopen(req, timeout=20).read().decode())

def delay(node, url, timeout=6000):
    enc = urllib.parse.quote(node, safe="")
    q = urllib.parse.urlencode({"timeout": str(timeout), "url": url})
    try:
        r = api(f"/proxies/{enc}/delay?" + q)
        return r.get("delay", "?")
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode())
            return "X(" + str(body.get("message", e.code))[:22] + ")"
        except Exception:
            return f"HTTP{e.code}"
    except Exception as e:
        return "ERR:" + type(e).__name__

p = api("/proxies")["proxies"]
grp = p["大机场 Big Airport"]
nodes = [n for n in grp["all"] if n not in ("自动选择", "故障转移", "DIRECT")]

tests = [
    ("204", "http://www.gstatic.com/generate_204"),
    ("ggl", "https://www.google.com"),
    ("gh ", "https://github.com"),
]
print(f"当前分组选中: {grp.get('now')}")
print(f"{'节点':<32} " + " ".join(f"{lbl:>14}" for lbl, _ in tests))
print("-" * 92)
ok = {}
for n in nodes:
    line = f"{n:<32} "
    for lbl, url in tests:
        d = delay(n, url)
        line += f"{str(d):>14} "
        ok.setdefault(lbl, 0)
        if isinstance(d, int) and d > 0:
            ok[lbl] += 1
    print(line)
print("-" * 92)
print("可用节点计数:", ok)
