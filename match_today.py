# -*- coding: utf-8 -*-
"""灵魂匹配（安全版）：开始匹配 → 进聊天窗 → 读引力签查「已婚」→ 发带上下文的开场。
用法: python match_today.py [次数] [开场1 开场2 ...]
开场若不带参数则用当天上下文默认的 2 条。
"""
import sys, time, os, base64, xml.etree.ElementTree as ET
import appbot_demo as a

# 当天上下文（2026-09-27 周日早上 · 中秋最后一天 · 綦江雾23~30℃ · 国庆还有4天）
DEFAULT_GREETS = [
    "周日大清早还醒着 我这边雾蒙蒙的 你呢",
    "中秋最后一天了 你今儿打算咋过 我这边雾散了刚起",
]

def dump_text():
    a.dump()
    p = a._rd.XML
    if not os.path.exists(p):
        return ""
    try:
        root = ET.parse(p).getroot()
        return " ".join((n.get("text") or "") for n in root.iter("node"))
    except Exception:
        return ""

def do_one(greet):
    a.connect()
    if not a.open_soul():
        print("!! open_soul 失败，停"); return False
    a.tap(*a.TAB_PLANET); time.sleep(2.0)
    a.tap(*a.MATCH_START); time.sleep(1.0)
    print("[match] 已点开始匹配，等 10s 撮合...")
    time.sleep(10)
    act = a.activity()
    if "Conversation" not in act:
        print("[match] 非聊天窗(%s)，跳过" % act); return False
    if a.modal_present():
        print("[match] 弹窗，停手"); return False
    txt = dump_text()
    if "已婚" in txt:
        print("[match] 引力签含「已婚」，跳过不发言"); return False
    if any(k in txt for k in ("不处对象", "不找对象", "单纯聊天不找对象", "不谈恋爱", "只聊天")):
        print("[match] 引力签含非交友意向，跳过"); return False
    # 打印资料区，便于复核（不引用签里写，避免客服感）
    print("[match] 资料区前80字:", txt[:80].replace("\n", " "))
    if a.send_message(greet):
        print("[match] 已打招呼:", greet); return True
    print("[match] send_message 被护栏拦下"); return False

def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else len(DEFAULT_GREETS)
    greets = sys.argv[2:] if len(sys.argv) > 2 else DEFAULT_GREETS
    ok = 0
    for i in range(n):
        g = greets[i % len(greets)]
        print("===== 第 %d/%d 次匹配 =====" % (i + 1, n))
        if do_one(g):
            ok += 1
        a.adb("shell", "am", "start", "-n", a.MAIN, "--activity-clear-top"); time.sleep(3)
    print("===== 完成：成功打招呼 %d/%d =====" % (ok, n))

if __name__ == "__main__":
    main()
