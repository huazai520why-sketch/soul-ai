# -*- coding: utf-8 -*-
"""⛔ 已废弃（2026-09-30）—— 这是**雷电模拟器 + adb** 时代的点击脚本。

它连的是 `127.0.0.1:5555`（雷电的 adb 端口）。Soul 已整体迁到 MuMu 15.0，
而 **MuMu 不向 Windows 暴露 adb 端口** → 本脚本调下去只会连不上、然后什么都不做，
即"静默失败"：看上去执行了，实际一个坐标都没点到。

请改用：
    bash soul.sh soul_tapxy.py chat          # 底导航 planet/square/chat/me/plus
    bash soul.sh soul_tapxy.py <x> <y>       # 显式坐标，点完自动回报页面状态
（记得按实例：`SOUL_VMINDEX=1 bash soul.sh soul_tapxy.py ...`）

本文件故意保留成"立即失败 + 说清楚"，避免有人误用。
"""
import sys

print(__doc__)
print("!! 拒绝执行：soul_tap.py 是雷电时期的 adb 版，在 MuMu 上必然静默失败。")
print("!! 请用：bash soul.sh soul_tapxy.py <chat|x> [y]")
sys.exit(2)
