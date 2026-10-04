# -*- coding: utf-8 -*-
"""灵魂匹配批量跑（复用 appbot_demo 的防乱点护栏）。
用法: python match_run.py [次数] [问候语1 问候语2 ...]
只点星球→开始匹配（不碰充电宝等杂页）；结果可能是聊天窗或对方主页，
主页则补点「私聊」再发；每条都走 require_page 校验，异常就停手。
"""
import sys, time
import appbot_demo as a

DEFAULT_GREETS = [
    "哈喽 这会儿还没睡呢",
    "下班没 闲着唠两句",
    "嗨 刚下夜班 你歇着呢",
    "在干嘛呢 刷手机还是追剧",
    "这么晚还不睡 夜猫子啊",
]

def do_one(greet):
    a.connect()
    if not a.open_soul():
        print("!! open_soul 失败，停"); return False
    a.tap(*a.TAB_PLANET); time.sleep(1.5)
    a.tap(*a.MATCH_START); time.sleep(1.0)
    print("[match] 已点开始匹配，等 10s 撮合...")
    time.sleep(10)
    act = a.activity()
    if "Conversation" not in act:
        # 可能是对方主页 → 试补点「私聊」
        print("[match] 非聊天窗(%s)，试补点私聊" % act)
        if a.safe_tap(text="私聊"):
            time.sleep(3)
            act = a.activity()
    if "Conversation" in act:
        if a.modal_present():
            print("[match] 弹窗，停手"); return False
        if a.send_message(greet):
            print("[match] 已打招呼:", greet)
            return True
        print("[match] send_message 被护栏拦下")
        return False
    print("[match] 这次没进聊天窗，跳过")
    return False

def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else len(DEFAULT_GREETS)
    greets = sys.argv[2:] if len(sys.argv) > 2 else DEFAULT_GREETS
    ok = 0
    for i in range(n):
        g = greets[i % len(greets)]
        print("===== 第 %d/%d 次匹配 =====" % (i + 1, n))
        if do_one(g):
            ok += 1
        # 回到主框架，准备下一次
        a.adb("shell", "am", "start", "-n", a.MAIN, "--activity-clear-top"); time.sleep(3)
    print("===== 完成：成功打招呼 %d/%d =====" % (ok, n))

if __name__ == "__main__":
    main()
