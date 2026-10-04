# -*- coding: utf-8 -*-
"""先看页面，再动手 —— 一条命令报出**当前实例**的页面状态。

为什么要有它（2026-09-30 用户当场纠偏）：
    我在没确认当前页面的情况下就调 soul_goto.py 复位、并准备连发 12 条消息，
    结果复位失败（停在广场 tab）都不知道 → 等于对真人会话盲操作。
    铁律：**任何 tap/输入/发送之前，先跑本脚本确认页面。**

用法（记住按实例）：
    bash soul.sh soul_where.py                 # 实例0
    SOUL_VMINDEX=1 bash soul.sh soul_where.py  # 实例1

输出：实例号 / 窗口句柄 / activity / 是否聊天列表 / 是否奇遇铃 / 屏幕前若干行 OCR。
只读：只截图 + OCR + 读 activity，**不点、不输入、不发送**。
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

import soul                    # noqa: E402
import soul_read as rd         # noqa: E402

try:
    from soul_instance import vm_index
    VM = vm_index()
except Exception:
    VM = 0


def main():
    print("=" * 62)
    print(f"实例号 SOUL_VMINDEX = {VM}   （0=主号 / 1=账号2）")
    print(f"截图路径           = {soul.SHOT}")
    hwnd_f = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".winshot_hwnd")
    if VM > 0:
        hwnd_f = hwnd_f + "." + str(VM)
    if os.path.exists(hwnd_f):
        print(f"窗口句柄(缓存)     = {open(hwnd_f).read().strip()}")
    print(f"共享目录           = {soul.SHARED_WIN}")
    print("-" * 62)

    # 0) 分辨率校准（实测 `wm size`，坐标常量按屏幕比例重建）——开局只跑一次
    try:
        w, h = soul.calibrate()
        print(f"分辨率（实测）     = {w}x{h}   聊天tab={soul.TAB_CHAT} 发送={soul.SEND_XY}")
    except Exception as e:
        print("分辨率校准         = ERR", repr(e))

    # 1) activity —— 最硬的"这是哪个页面"的证据
    try:
        print("activity           =", soul.activity())
    except Exception as e:
        print("activity           = ERR", repr(e))

    # 2) 是否奇遇铃（会弹窗挡路）
    try:
        print("奇遇铃 is_love_bell =", soul.is_love_bell())
    except Exception as e:
        print("奇遇铃              = ERR", repr(e))

    # 3) 截图（只读，不点）
    shot_ok = False
    try:
        soul.screenshot()
        shot_ok = True
        print("截图               = ok")
    except Exception as e:
        print("截图               = ERR", repr(e))

    # 4) 是否在聊天列表页（复用 soul_reply 自带判据）
    try:
        import soul_reply as R
        print("在聊天列表页        =", R._on_chat_list())
    except Exception as e:
        print("在聊天列表页        = ERR", repr(e))

    # 5) 屏幕前若干行 OCR —— 人肉一眼就能看出在哪个页面
    if shot_ok:
        try:
            items = rd.items() or []
            print("-" * 62)
            print(f"屏幕可见文字（共 {len(items)} 项，坐标已是**换算后的点击坐标**）：")
            for t, cx, cy in items[:40]:
                if t:
                    print(f"   y={cy:<5} x={cx:<5} {t}")
            try:
                import soul_read as _rd
                print("-" * 62)
                print(f"坐标空间：截图 {_rd.SHOT_W}x{_rd.SHOT_H} → 设备 {_rd.DEV_W}x{_rd.DEV_H}"
                      f"（换算因子 y = {_rd.DEV_H / _rd.SHOT_H:.3f}）")
            except Exception:
                pass
        except Exception as e:
            print("OCR                = ERR", repr(e))
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
