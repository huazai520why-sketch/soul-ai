import json, urllib.request, urllib.parse, urllib.error, sys, time, zipfile, os
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
CTRL = "http://192.168.10.1:9090"; SEC = "U511PhZ1"; GRP = "大机场 Big Airport"

def api(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(CTRL + path, data=data, method=method,
        headers={"Authorization": "Bearer " + SEC, "Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=15).read().decode()

enc = urllib.parse.quote(GRP, safe="")
orig = json.loads(api(f"/proxies/{enc}")).get("now")
api(f"/proxies/{enc}", "PUT", {"name": "DIRECT"})
print("临时 DIRECT (原:", orig, ")")

def fetch(u, tries=4, timeout=45):
    last = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read(), None
        except urllib.error.HTTPError as e:
            return None, f"HTTP{e.code}"
        except Exception as e:
            last = repr(e)[:80]; time.sleep(1.3)
    return None, last

CAND = [
    "https://cdn.jsdelivr.net/gh/senzhk/ADBKeyBoard@master/ADBKeyboard.apk",
    "https://gcore.jsdelivr.net/gh/senzhk/ADBKeyBoard@master/ADBKeyboard.apk",
    "https://ghproxy.net/https://github.com/senzhk/ADBKeyBoard/raw/master/ADBKeyboard.apk",
]
out = r"E:\soul\ADBKeyboard.apk"
ok = False
for u in CAND:
    if ok: break
    data, err = fetch(u)
    if not data:
        print("ERR", err, u[:60]); continue
    print(f"got {len(data)} B, head={data[:4]}, {u[:60]}")
    if data[:2] != b"PK":
        print("   not zip/apk"); continue
    open(out, "wb").write(data)
    try:
        z = zipfile.ZipFile(out)
        names = z.namelist()
        hm = any(n == "AndroidManifest.xml" for n in names)
        print("   zip entries =", len(names), "| AndroidManifest:", hm)
        if hm:
            ok = True
    except Exception as e:
        print("   zip invalid:", repr(e))

api(f"/proxies/{enc}", "PUT", {"name": orig})
print("已还原分组:", json.loads(api(f"/proxies/{enc}")).get("now"))
print("RESULT:", ("OK size=" + str(os.path.getsize(out))) if ok else "FAIL")
