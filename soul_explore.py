# -*- coding: utf-8 -*-
"""Soul App 探索辅助：重连 adb + dump 界面 + 列出可点击元素。
用法:
  python soul_explore.py home            # 返回键一次 + dump
  python soul_explore.py dump            # dump 当前界面并打印可点击
  python soul_explore.py tap <文字>      # 点击指定文字节点中心
  python soul_explore.py back            # 按返回键
  python soul_explore.py save <文件名>   # dump 并存到 E:/soul/<文件名>.xml
"""
import subprocess, time, re, sys, xml.etree.ElementTree as ET

# ⭐ 无黑窗（2026-09-30）：adb.exe 是控制台程序，不带此标志每次调用都闪黑窗。
_NW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

ADB = r"D:\leidian\LDPlayer14\adb.exe"
DEV = "127.0.0.1:5555"
OUT = r"E:\soul\ui_tmp.xml"

def adb(args, timeout=30):
    r = subprocess.run([ADB, "-s", DEV] + args, capture_output=True, text=True, timeout=timeout, creationflags=_NW)
    return r.stdout.strip()

def reconnect():
    adb(["disconnect"]); time.sleep(0.5)
    adb(["connect", DEV]); time.sleep(1.5)

def dump(path=OUT):
    reconnect()
    adb(["shell", "uiautomator", "dump", "/sdcard/ui.xml"])
    adb(["pull", "/sdcard/ui.xml", path])
    return path

def clickables(path):
    tree = ET.parse(path); root = tree.getroot()
    rows = []
    for n in root.iter("node"):
        t = (n.get("text") or "").strip()
        rid = n.get("resource-id") or ""
        click = n.get("clickable") or ""
        b = n.get("bounds") or ""
        cls = (n.get("class") or "").split(".")[-1]
        if click == "true" or (t and rid):
            rows.append((t, rid, b, cls))
    return rows

def tap_text(target):
    p = dump()
    tree = ET.parse(p); root = tree.getroot()
    for n in root.iter("node"):
        if (n.get("text") or "").strip() == target:
            m = re.search(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', n.get("bounds", ""))
            if m:
                x = (int(m.group(1)) + int(m.group(3))) // 2
                y = (int(m.group(2)) + int(m.group(4))) // 2
                adb(["shell", "input", "tap", str(x), str(y)])
                print(f"tapped [{target}] @ {x},{y}")
                return True
    print(f"未找到文字节点: {target}")
    return False

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "dump"
    if cmd == "home":
        reconnect(); adb(["shell", "input", "keyevent", "4"]); time.sleep(1.5)
        p = dump()
        for t, rid, b, cls in clickables(p):
            print(f"[{cls}] {t}  {rid}  {b}")
    elif cmd == "dump":
        p = dump()
        for t, rid, b, cls in clickables(p):
            print(f"[{cls}] {t}  {rid}  {b}")
    elif cmd == "tap":
        tap_text(sys.argv[2])
    elif cmd == "back":
        reconnect(); adb(["shell", "input", "keyevent", "4"])
        print("back")
    elif cmd == "tapxy":
        x, y = sys.argv[2], sys.argv[3]
        reconnect()
        adb(["shell", "input", "tap", x, y])
        print(f"tapped {x},{y}")
    elif cmd == "save":
        name = sys.argv[2] if len(sys.argv) > 2 else "ui_save"
        p = dump(rf"E:\soul\{name}.xml")
        print(f"saved -> {p}")
