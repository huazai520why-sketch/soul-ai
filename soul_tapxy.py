# -*- coding: utf-8 -*-
"""按坐标/底导航点击（MuMu / mumu-cli 版），点完**自动回报页面状态**。

为什么另起一个：`soul_tap.py` 是雷电时期的 adb 版（连 127.0.0.1:5555），
MuMu 上早就不存在该端口 → 调它等于静默失败。已把那个文件标死。

用法（按实例）：
    bash soul.sh soul_tapxy.py 501 1263          # 显式坐标
    bash soul.sh soul_tapxy.py chat              # 底导航：聊天（用 soul.TAB_CHAT）
    bash soul.sh soul_tapxy.py square|planet|me|plus
    SOUL_VMINDEX=1 bash soul.sh soul_tapxy.py 360 522   # 账号2 点"立即私聊"

点完不会假装成功：会打印 activity + 是否聊天列表 + 屏幕前 8 行 OCR，供人核对。
"""
import io
import sys

sys.path.insert(0, r"E:\soul")
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

import soul                    # noqa: E402
import soul_read as rd         # noqa: E402

TABS = {
    "planet": soul.TAB_PLANET,
    "square": soul.TAB_SQUARE,
    "plus": soul.TAB_PLUS,
    "chat": soul.TAB_CHAT,
    "me": soul.TAB_ME,
}


def _report(tag):
    print("---- 点后页面 ----", tag)
    try:
        print("activity =", soul.activity())
    except Exception as e:
        print("activity = ERR", repr(e))
    try:
        import soul_reply as R
        print("在聊天列表页 =", R._on_chat_list())
    except Exception as e:
        print("在聊天列表页 = ERR", repr(e))
    try:
        soul.screenshot()
        for t, cx, cy in (rd.items() or [])[:8]:
            if t:
                print(f"   y={cy:<5} x={cx:<5} {t}")
    except Exception as e:
        print("OCR = ERR", repr(e))


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    if argv[0] == "back":
        fn = getattr(soul, "tap_back_arrow", None)
        if fn is None:
            print("!! soul.tap_back_arrow() 不存在 → 拒绝盲点坐标")
            return 1
        print("点返回箭头（soul.tap_back_arrow()）")
        if not soul.ensure_foreground():
            print("!! 前台拉起失败 → 不点")
            return 1
        fn()
        import time
        time.sleep(1.6)
        _report("back")
        return 0

    if argv[0] in TABS:
        x, y = TABS[argv[0]]
        print(f"底导航 {argv[0]} → {x},{y}")
    elif len(argv) >= 2 and argv[0].lstrip("-").isdigit():
        x, y = int(argv[0]), int(argv[1])
        print(f"坐标 → {x},{y}")
    else:
        print(__doc__)
        return 2

    if not soul.ensure_foreground():
        print("!! 前台拉起失败 → 不点（拒绝盲点）")
        return 1
    soul.tap(x, y)
    import time
    time.sleep(1.6)
    _report(f"{x},{y}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
