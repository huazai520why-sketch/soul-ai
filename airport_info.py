import urllib.request, json, ssl, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MIRRORS = ["https://bigairport-mirror.com", "https://bigairport1.forum",
           "https://bigairport1.xyz", "https://bigairport1.cyou",
           "https://bigairport1.baby", "https://xn--mesr8b36x.com"]
EMAIL = "747502848@qq.com"; PWD = "jjh1314520"

ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE

def req(url, data=None, headers=None, method=None):
    h = {"User-Agent": "Mozilla/5.0"}
    if data is not None:
        h["Content-Type"] = "application/json"
    if headers: h.update(headers)
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(url, data=body, headers=h, method=method)
    return urllib.request.urlopen(r, timeout=20, context=ctx)

base = None; auth = None
for m in MIRRORS:
    try:
        r = req(m + "/api/v1/passport/auth/login", {"email": EMAIL, "password": PWD})
        j = json.loads(r.read().decode())
        auth = (j.get("data") or {}).get("auth_data")
        print("登录取到 auth_data:", bool(auth), "| mirror:", m)
        base = m
        break
    except Exception as e:
        print("login fail", m, "->", repr(e)[:130])

if not base:
    sys.exit(1)

def get(path):
    with req(base + path, headers={"Authorization": auth}) as r:
        return json.loads(r.read().decode())

print("\n=== /user/info ===")
try:
    info = get("/api/v1/user/info")
    d = info.get("data") or {}
    for k in ("email", "balance", "commission", "transfer_enable", "u", "d",
              "expired_at", "plan_id", "banned", "is_admin", "device_limit", "created_at"):
        if k in d:
            print(f"  {k} = {d[k]}")
    if "transfer_enable" in d:
        tot = d["transfer_enable"]; used = (d.get("u", 0) + d.get("d", 0))
        print(f"  已用 {used/1e9:.2f} GB / 总量 {tot/1e9:.2f} GB ({used/tot*100:.1f}%)")
except Exception as e:
    print("  ERR", repr(e))

print("\n=== /user/getSubscribe ===")
try:
    s = get("/api/v1/user/getSubscribe")
    d = s.get("data") or {}
    print("  subscribe_url =", d.get("subscribe_url"))
except Exception as e:
    print("  ERR", repr(e))

print("\n=== /user/plan/fetch ===")
try:
    p = get("/api/v1/user/plan/fetch")
    for pl in (p.get("data") or [])[:5]:
        print("  plan:", json.dumps(pl, ensure_ascii=False)[:200])
except Exception as e:
    print("  ERR", repr(e))
