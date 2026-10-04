# -*- coding: utf-8 -*-
"""session1 专用：拉起 MuMu 管理器 + 实例 vm=1（必须在 session1，见 guard 注释）。"""
import subprocess, time, io
CLI = r"D:\MuMuPlayer\nx_main\mumu-cli.exe"
NX = r"D:\MuMuPlayer\nx_main\MuMuNxMain.exe"
NO = 0x08000000
DET = 0x00000008
lg = io.open(r"E:\soul\_probe\startvm.log", "w", encoding="utf-8", buffering=1)


def p(*a):
    lg.write(" ".join(str(x) for x in a) + "\n")
    lg.flush()


def run(a, t=60):
    try:
        r = subprocess.run(a, capture_output=True, timeout=t, creationflags=NO)
        return r.stdout.decode("utf-8", "ignore")
    except Exception as e:
        return "ERR %r" % (e,)


def has(img):
    return img.lower() in run(["tasklist", "/FI", "IMAGENAME eq %s" % img, "/FO", "CSV", "/NH"], 15).lower()


p("start", time.strftime("%H:%M:%S"))
if not has("MuMuNxMain.exe"):
    p("MuMuNxMain 不在 → launch")
    subprocess.Popen([NX], creationflags=DET | NO, close_fds=True)
    for _ in range(20):
        time.sleep(3)
        if has("MuMuNxMain.exe"):
            break
    time.sleep(8)
p("MuMuNxMain ok:", has("MuMuNxMain.exe"))
p("control launch vm=1:", run([CLI, "control", "-v", "1", "launch"], 90)[:200])
for i in range(45):
    time.sleep(2)
    if '"is_process_started": true' in run([CLI, "info", "-v", "1"], 25):
        p("vm1 started after %ds" % (2 * (i + 1)))
        break
else:
    p("vm1 TIMEOUT")
p("done", time.strftime("%H:%M:%S"))
lg.close()
