import json, subprocess, sys

with open(r"E:\soul\clash_proxies.json", encoding="utf-8") as f:
    data = json.load(f)

proxies = data["proxies"]

# 列出所有 Selector / URLTest / Fallback 分组及其当前选中
print("=== 选择器分组(当前选中) ===")
for name, p in proxies.items():
    t = p.get("type")
    if t in ("Selector", "URLTest", "Fallback"):
        now = p.get("now")
        opts = p.get("all", [])
        print(f"[{t}] {name!r}  now={now!r}  options={len(opts)}")

# 找出最可能的主分组(含日本/SS 节点的 Selector)
print("\n=== 含海外节点的分组候选 ===")
for name, p in proxies.items():
    if p.get("type") == "Selector":
        opts = p.get("all", [])
        overseas = [o for o in opts if any(k in o for k in ("日本","美国","香港","韩国","新加坡","SS","节点","01","02"))]
        if overseas:
            print(f"{name!r}: {overseas[:10]}")
