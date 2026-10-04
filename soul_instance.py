# -*- coding: utf-8 -*-
"""多实例隔离 —— 让同一套脚本可以同时操作多个 MuMu 实例且互不串台。

背景（2026-09-30 用户需求）：
  应用分身（com.netease.mumu.cloner）**做不到隔离** —— 实测 `screencap -d 5`
  在分身屏直接失败（Status: -2），整条读屏链路只能靠 Windows 侧窗口截图
  （winshot.py / PrintWindow），而一个 MuMu 窗口同一时刻只呈现一块屏
  → 同时只能"看见"一个 Soul，闭环断裂。
  正解是**独立实例**（mumu-cli clone）：各自独立的安卓系统 + 独立 Windows 窗口。

但脚本里有若干**全局单例状态文件**（锁、截图、窗口句柄、新消息 flag、数据库），
两个实例同时跑就会互相覆盖 / 互堵 → 串台级事故。本模块统一解决。

用法
----
    from soul_instance import state_path, vm_index

    LOCK_F = state_path(BASE, ".soul_auto.lock")
    # 实例 0 → /d/AI/pl/.soul_auto.lock       （**保持原名，向后兼容**）
    # 实例 1 → /d/AI/pl/.soul_auto.1.lock

约定（铁律）
-----------
  · **实例 0 一律返回原文件名** —— 不设 SOUL_VMINDEX 时行为与改造前**分毫不变**，
    现有自动化、watcher、历史残留文件全部不受影响。
  · 实例 N>0 才加 `.<N>` 后缀，且在**扩展名之前**（`.soul_newmsg.1.json`），
    这样文件类型仍可被正常识别。
  · 本模块**不得 import soul**（避免循环依赖），只用标准库。

环境变量
--------
  SOUL_VMINDEX   实例号，默认 "0"。非法值（非数字/负数）一律回退 0（fail-safe）。
"""
import os
import re

DEFAULT_INDEX = 0
ENV_KEY = "SOUL_VMINDEX"


def vm_index():
    """当前实例号。非法值回退 0（宁可按老路径跑，也不要造出一个乱七八糟的新路径）。"""
    raw = os.environ.get(ENV_KEY, "").strip()
    if not raw:
        return DEFAULT_INDEX
    if not re.fullmatch(r"\d+", raw):
        return DEFAULT_INDEX
    try:
        v = int(raw)
    except ValueError:
        return DEFAULT_INDEX
    return v if v > 0 else DEFAULT_INDEX


def state_path(base_dir, name, idx=None):
    """把全局状态文件名按实例隔离。

    state_path("/d/AI/pl", "wshot.png")        → 实例0: wshot.png        / 实例1: wshot.1.png
    state_path("/d/AI/pl", ".soul_auto.lock")  → 实例0: .soul_auto.lock  / 实例1: .soul_auto.1.lock
    state_path("/d/AI/pl", ".winshot_hwnd")    → 实例0: .winshot_hwnd    / 实例1: .winshot_hwnd.1
    """
    i = vm_index() if idx is None else idx
    if i <= 0:
        return os.path.join(base_dir, name)
    root, ext = os.path.splitext(name)
    return os.path.join(base_dir, f"{root}.{i}{ext}")


def instance_tag(idx=None):
    """实例标签，用于日志/报告里标明来源。实例0 返回空串（不污染既有输出）。"""
    i = vm_index() if idx is None else idx
    return "" if i <= 0 else f"[vm{i}]"


def ensure_dirs(*paths):
    """确保父目录存在（多实例首次运行时会新建带后缀的文件，父目录通常已在）。"""
    for p in paths:
        d = os.path.dirname(os.path.abspath(p))
        if d and not os.path.isdir(d):
            try:
                os.makedirs(d, exist_ok=True)
            except OSError:
                pass


if __name__ == "__main__":
    try:
        import sys
        sys.stdout = __import__("io").TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    except Exception:
        pass
    print(f"SOUL_VMINDEX = {os.environ.get(ENV_KEY, '(未设置)')}")
    print(f"vm_index()   = {vm_index()}")
    print("状态文件映射：")
    for n in (".soul_auto.lock", "wshot.png", ".winshot_hwnd",
              ".soul_newmsg.json", "im_data.db", "chat_im.db", ".soul_turn_base.json"):
        print(f"  {n:24s} → {os.path.basename(state_path(os.path.dirname(os.path.abspath(__file__)), n))}")
