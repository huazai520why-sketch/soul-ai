import json, urllib.request, subprocess, time, sys, os

controller = "http://192.168.10.1:9090"
secret = "U511PhZ1"
hdr = {"Authorization": "Bearer " + secret, "Content-Type": "application/json"}
proxy = "http://Clash:qUtKpafm@192.168.10.1:7890"

def put(group, name):
    req = urllib.request.Request(f"{controller}/proxies/{group}",
                                 data=json.dumps({"name": name}).encode(),
                                 headers=hdr, method="PUT")
    try:
        urllib.request.urlopen(req, timeout=10)
        return "ok"
    except Exception as e:
        return "ERR " + repr(e)[:60]

def test_github():
    r = subprocess.run(["curl.exe", "-x", proxy, "-sS", "-m", "25", "-o", "NUL",
                        "-w", "%{http_code}", "https://github.com"],
                       capture_output=True, text=True)
    return r.stdout.strip()

candidates = [
    "[ss][1x] [直连] [SS] 日本 01",
    "[ss][1x] [直连] [SS] 日本 02",
    "[ss][1x] [直连] [SS] 日本 03",
    "[ss][1x] [C] [直连] [SS] 新加坡 9",
    "[ss][1x] [D] [直连] [SS] 新加坡 9",
]
working = None
for n in candidates:
    print("-> GLOBAL =", n, put("GLOBAL", n))
    time.sleep(4)
    c = test_github()
    print("   github:", c)
    if c == "200":
        working = n
        break

if not working:
    print("ALL NODES FAILED")
    sys.exit(1)

print("WORKING NODE:", working)
url = "https://github.com/silentlexx/ADBKeyboard/releases/download/v2.0/ADBKeyboard.apk"
r = subprocess.run(["curl.exe", "-x", proxy, "-L", "-m", "120", "-o", "E:/soul/ADBKeyboard.apk", url],
                   capture_output=True, text=True)
print("download rc=", r.returncode, "err=", r.stderr.strip()[:150])
if os.path.exists(r"E:/soul/ADBKeyboard.apk"):
    print("size=", os.path.getsize(r"E:/soul/ADBKeyboard.apk"))

print("restore GLOBAL = DIRECT", put("GLOBAL", "DIRECT"))
