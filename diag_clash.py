import json, urllib.request, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
CTRL = "http://192.168.10.1:9090"
SEC = "U511PhZ1"

def api(p):
    req = urllib.request.Request(CTRL + p, headers={"Authorization": "Bearer " + SEC})
    return json.loads(urllib.request.urlopen(req, timeout=10).read().decode())

try:
    p = api("/proxies")
except Exception as e:
    print("ERR /proxies:", repr(e)); sys.exit(1)

proxies = p["proxies"]
print("=== 所有分组(Selector/URLTest/Fallback/LoadBalance) ===")
for name, val in proxies.items():
    if val.get("type") in ("Selector", "URLTest", "Fallback", "LoadBalance"):
        print(f"[{val['type']:10}] {name}  -> now={val.get('now')}")

print("\n=== GLOBAL 当前 ===")
g = proxies.get("GLOBAL")
if g:
    print("GLOBAL now =", g.get("now"))

print("\n=== github 相关分组明细 ===")
for name, val in proxies.items():
    if "机场" in name or "Big Airport" in name:
        print(f"分组: {name} | type={val.get('type')} | now={val.get('now')}")
        for n in (val.get("all") or [])[:40]:
            sub = proxies.get(n, {})
            hist = sub.get("history") or []
            d = hist[-1].get("delay") if hist else None
            print(f"   - {n:32} delay={d}")
