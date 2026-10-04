# -*- coding: utf-8 -*-
"""截图 LDPlayer 当前画面并拉到本地

用途：让 AI 用 Read 工具直接"看"模拟器画面（识图），不再只依赖 uiautomator 的 ui.xml。
注意：Read 工具只能读图片，请把输出路径放在工作目录下（默认 D:\\AI\\pl\\shot.png）。

用法:
    python soul_shot.py            # -> shot.png
    python soul_shot.py a.png      # -> a.png
"""
import os
import subprocess
import sys

# ⭐ 无黑窗（2026-09-30）：adb.exe 是控制台程序，不带此标志每次调用都闪黑窗。
_NW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

ADB = r"D:\leidian\LDPlayer14\adb.exe"
DEV = "127.0.0.1:5555"


def shot(out="shot.png", timeout=30):
    subprocess.run([ADB, "disconnect"], capture_output=True, timeout=15, creationflags=_NW)
    subprocess.run([ADB, "connect", DEV], capture_output=True, timeout=15, creationflags=_NW)
    subprocess.run([ADB, "-s", DEV, "shell", "screencap", "-p", "/sdcard/shot.png"],
                   capture_output=True, timeout=timeout, creationflags=_NW)
    r = subprocess.run([ADB, "-s", DEV, "pull", "/sdcard/shot.png", out],
                       capture_output=True, timeout=timeout, creationflags=_NW)
    if os.path.exists(out) and os.path.getsize(out) > 1000:
        print("截图成功: %s (%d bytes)" % (os.path.abspath(out), os.path.getsize(out)))
        return os.path.abspath(out)
    print("截图失败:", (r.stdout + r.stderr).decode("utf-8", "ignore").strip())
    return None


if __name__ == "__main__":
    shot(sys.argv[1] if len(sys.argv) > 1 else "shot.png")
