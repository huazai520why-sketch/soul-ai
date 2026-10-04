# -*- coding: utf-8 -*-
"""读「我」主页，确认当前实例登录的是哪个账号（只读：切 tab + 截图 + OCR）。

用法：
    bash soul.sh soul_whoami.py            # 实例0
    SOUL_VMINDEX=1 bash soul.sh soul_whoami.py
"""
import io
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

import soul                # noqa: E402
import soul_read as rd     # noqa: E402


def main():
    vm = getattr(soul, "VMI", 0)
    print("=" * 62)
    print(f"实例 SOUL_VMINDEX = {vm}   截图 = {soul.SHOT}")
    print("=" * 62)

    soul.ensure_foreground()
    time.sleep(0.6)

    # 切到「我」
    soul.tap(*soul.TAB_ME)
    time.sleep(2.5)
    soul.screenshot(force=True)
    time.sleep(0.4)

    items = rd.items() or []
    print(f"--- 「我」页可见文字（{len(items)} 项）---")
    for t, cx, cy in items:
        if t and t.strip():
            print(f"   y={cy:<5} x={cx:<5} {t}")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
