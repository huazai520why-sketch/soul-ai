# -*- coding: utf-8 -*-
"""把 MuMu 模拟器窗口从「最小化/隐藏」恢复到正常显示（只做窗口动作，不碰模拟器内部）。

为什么需要：winshot.py 走 PrintWindow 抓窗口句柄，**最小化时抓到全黑**，
整轮 OCR 全废（2026-10-01 重启模拟器后复现：截图 0 项文字）。
mumu-cli 的 show_window 对"最小化到任务栏"不一定生效 → 这里用 Win32 ShowWindow(SW_RESTORE=9)
配合 GWL_STYLE 判断，只恢复被最小化的窗口，不动正常窗口。

用法：
    bash soul.sh win_restore.py            # 恢复当前实例（读 SOUL_VMINDEX）
    bash soul.sh win_restore.py 0          # 恢复指定实例号
    bash soul.sh win_restore.py all        # 两个都恢复
"""
import ctypes
import sys

u32 = ctypes.windll.user32
GWL_STYLE = -16
WS_MINIMIZE = 0x20000000
SW_RESTORE = 9
SW_SHOW = 5


def _hwnds(target):
    """target: '0'/'1'/'all' → 返回 [(vm_index, hwnd)]"""
    out = []
    want = target if target in ("0", "1", "all") else "all"
    for p in psutil_proc():
        pass
    return out


def psutil_proc():
    import subprocess
    raw = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-Process MuMuNxDevice -ErrorAction SilentlyContinue | "
         "Select-Object MainWindowHandle,MainWindowTitle | ConvertTo-Csv -NoTypeInformation"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout
    rows = []
    for i, line in enumerate(raw.splitlines()):
        if i < 1 or not line.strip():
            continue
        parts = line.split(",")
        try:
            hwnd = int(parts[0].strip().strip('"'))
        except Exception:
            hwnd = 0
        title = parts[1].strip().strip('"') if len(parts) > 1 else ""
        rows.append((hwnd, title))
    return rows


def is_minimized(hwnd):
    return bool(u32.GetWindowLongW(hwnd, GWL_STYLE) & WS_MINIMIZE)


def main(argv):
    target = argv[0] if argv else "all"
    fixed = []
    for hwnd, title in psutil_proc():
        if target not in ("all",) and "设备-1" not in title and target == "1":
            if "设备-1" not in title and target == "0":
                continue
        if target == "0" and "设备-1" in title:
            continue
        if is_minimized(hwnd):
            u32.ShowWindow(hwnd, SW_RESTORE)
            fixed.append((hwnd, title))
            print(f"  恢复：{title} hwnd=0x{hwnd:X} → SW_RESTORE")
        else:
            print(f"  已是正常：{title} hwnd=0x{hwnd:X}")
    if fixed:
        print(f"✅ 共恢复 {len(fixed)} 个窗口")
        return 0
    print("（没有需要恢复的窗口，或窗口都不在最小化状态）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
