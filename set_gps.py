# -*- coding: utf-8 -*-
"""设置 LDPlayer 模拟器 GPS 到指定坐标（模拟器重启后会重置，需重跑本脚本）

背景：雷电模拟器默认 GPS 是北京天安门(39.915,116.404)，导致 Soul 等 App 全推北京内容。
     ldconsole.exe locate 命令在 14.0.26.1 上无效（ro.allow.mock.location=0），
     改用 Android 14 官方 cmd location test-provider 机制注入。

用法:
    python set_gps.py                # 默认重庆主城
    python set_gps.py 29.5630 106.5516   # 纬度 经度

注入后需 force-stop + 重启目标 App 才会重新定位。
"""
import subprocess
import sys

# ⭐ 无黑窗（2026-09-30）：adb.exe 是控制台程序，不带此标志每次调用都闪黑窗。
_NW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

ADB = r"D:\leidian\LDPlayer14\adb.exe"
DEV = "127.0.0.1:5555"
DEFAULT_LAT, DEFAULT_LNG = "29.5630", "106.5516"   # 重庆


def sh(*args, timeout=30):
    r = subprocess.run([ADB, "-s", DEV, "shell"] + [str(a) for a in args],
                       capture_output=True, timeout=timeout, creationflags=_NW)
    return (r.stdout + r.stderr).decode("utf-8", "ignore").strip()


def set_gps(lat=DEFAULT_LAT, lng=DEFAULT_LNG, app="cn.soulapp.android"):
    subprocess.run([ADB, "disconnect"], capture_output=True, timeout=15, creationflags=_NW)
    subprocess.run([ADB, "connect", DEV], capture_output=True, timeout=15, creationflags=_NW)

    # shell 用户拿到 mock 权限
    sh("appops", "set", "com.android.shell", "android:mock_location", "allow")

    for p in ("gps", "network", "fused"):
        sh("cmd", "location", "providers", "add-test-provider", p)
        sh("cmd", "location", "providers", "set-test-provider-enabled", p, "true")
        r = sh("cmd", "location", "providers", "set-test-provider-location", p,
               "--location", "%s,%s" % (lat, lng))
        print("[%s] %s" % (p, r or "ok"))

    out = sh("dumpsys", "location")
    for line in out.splitlines():
        if "last location" in line and "mock" in line:
            print("  验证:", line.strip())

    if app:
        sh("am", "force-stop", app)
        import time
        time.sleep(2)
        sh("am", "start", "-n", app + "/.component.startup.main.MainActivity")
        print("已重启", app)
    return True


if __name__ == "__main__":
    lat = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_LAT
    lng = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_LNG
    print("设置 GPS ->", lat + "," + lng)
    set_gps(lat, lng)
