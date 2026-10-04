import json, urllib.request, urllib.parse, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
CTRL = "http://192.168.10.1:9090"; SEC = "U511PhZ1"
GRP = "大机场 Big Airport"
TARGET = "[ss][1x] [D] [直连] [SS] 日本 3"

def api(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(CTRL + path, data=data, method=method,
        headers={"Authorization": "Bearer " + SEC, "Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=15).read().decode()

enc = urllib.parse.quote(GRP, safe="")
before = json.loads(api(f"/proxies/{enc}")).get("now")
print("切换前选中:", before)
try:
    api(f"/proxies/{enc}", "PUT", {"name": TARGET})
except urllib.error.HTTPError as e:
    print("PUT HTTPError:", e.code, e.read().decode()[:200])
after = json.loads(api(f"/proxies/{enc}")).get("now")
print("切换后选中:", after)
