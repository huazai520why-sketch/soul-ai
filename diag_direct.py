import json, urllib.request, urllib.parse, sys, urllib.error
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
CTRL = "http://192.168.10.1:9090"; SEC = "U511PhZ1"

def api(p):
    req = urllib.request.Request(CTRL + p, headers={"Authorization": "Bearer " + SEC})
    return json.loads(urllib.request.urlopen(req, timeout=25).read().decode())

def delay(node, url, t=6000):
    q = urllib.parse.urlencode({"timeout": str(t), "url": url})
    try:
        return api(f"/proxies/{urllib.parse.quote(node, safe='')}/delay?" + q).get("delay")
    except urllib.error.HTTPError as e:
        try:
            return "X:" + (json.loads(e.read().decode()).get("message", "")[:26])
        except Exception:
            return f"HTTP{e.code}"
    except Exception as e:
        return "ERR:" + type(e).__name__

print("=== DIRECT 出站直连测试（绕过机场节点，直接走 WAN）===")
for url in ["http://www.gstatic.com/generate_204", "https://www.google.com",
            "https://github.com", "https://www.baidu.com"]:
    print(f"  DIRECT -> {url:42} = {delay('DIRECT', url)}")

print("\n=== 之前'可用'节点复测 ===")
for n in ["[ss][1x] [D] [直连] [SS] 日本 3",
          "[ss][1x] [直连] [SS] 日本 07",
          "[ss][1x] [直连] [SS] 新加坡 02"]:
    print(f"  {n:34} gh={delay(n,'https://github.com')}  204={delay(n,'http://www.gstatic.com/generate_204')}")

print("\n=== 当前分组选中 ===")
print("  ", api("/proxies/" + urllib.parse.quote("大机场 Big Airport", safe="")).get("now"))
