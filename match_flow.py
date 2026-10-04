# -*- coding: utf-8 -*-
"""灵魂匹配流程（不截图版）：卡片文字一律从 dump 取
（2026-09-25 21:28 用户指示：对话用数据库/dump，不截图）

用法:
  python match_flow.py start   # 回主框架→星球→点「灵魂匹配」→等撮合→打印对方卡片
  python match_flow.py next    # 点「匹配下一个」连续匹配→打印新人卡片
  python match_flow.py card    # 只打印当前会话卡片
"""
import sys, io, time
sys.path.insert(0, r"E:\soul")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import appbot_demo as A
import soul
import soul_read as rd

KEYS = ("匹配度", "引力签", "星座", "共同点", "礼仪", "瞬间")
NOT_NICK = ("关注", "匹配", "分钟", "小时", "天前", "查看主页", "邀请通话")


def card():
    """从 dump 提取匹配卡：昵称 + 引力签/星座/共同点等文字行"""
    its = rd.items()
    if not its:
        print("!! dump 为空")
        return None
    nick = None
    for t, x, y in its:
        if y < 170 and 1 <= len(t) <= 16 and not any(k in t for k in NOT_NICK):
            nick = t
            break
    print("昵称:", nick)
    for t, x, y in its:
        if any(k in t for k in KEYS):
            print("  卡片:", t[:70])
    return nick


def ensure_main():
    if not soul.on_main():
        if not A.open_soul():
            print("!! 回不到主框架")
            return False
    return True


def wait_conversation(timeout=16):
    for _ in range(timeout // 2):
        time.sleep(2)
        if "Conversation" in A.activity():
            return True
    return False


def tap_match_btn():
    """点「开始匹配」（2026-09-25 提速：2次 safe_tap → dump坐标兜底 → 星球关键词已确认直接点验证坐标。
    旧版5次重试 ~15s 纯耗时；星球页动画致 dump 常失败是常态，走兜底很快）
    ⚠️ 星球页新版布局：「灵魂匹配/语音匹配」是 y=296 标签，「开始匹配」(y=461) 才是动作按钮"""
    for _ in range(2):
        if A.safe_tap(text="开始匹配"):
            return True
        time.sleep(1.5)
    its = rd.items()
    hit = [(t, x, y) for t, x, y in its if t.startswith("开始匹配")]
    if hit:
        t, x, y = hit[0]
        print("safe_tap 失败，用已确认 dump 坐标：", (x, y))
        A.tap(x, y)
        return True
    planet = any(any(k in t for k in ("灵魂匹配", "语音匹配", "星球"))
                 for t, _, _ in its)
    if planet:
        print("已确认在星球页但按钮节点缺失，点验证坐标 (123,461)")
        A.tap(123, 461)
        return True
    print("!! 确认不到星球页，当前页面文本：")
    for t, x, y in its[:25]:
        print("   y=%d %s" % (y, t[:40]))
    return False


def start():
    A.connect()
    if not ensure_main():
        return
    A.tap(*A.TAB_PLANET)          # (78,1231) 星球
    time.sleep(5)
    if not tap_match_btn():
        return
    print("已点「开始匹配」，等撮合…")
    if not wait_conversation():
        print("!! 16s 未进匹配会话，activity:", A.activity())
        A.screenshot("shots/match_fail_%d.png" % int(time.time()))
        return
    time.sleep(2)
    card()


def send_next(texts):
    """⭐ 在当前匹配会话发 texts，发完立即抓「匹配下一个」连击
    （按钮窗口短，发送和点击必须在同一命令内完成，不能隔着我构思文案的时间）"""
    from soul_send import send_msg
    for t in texts:
        send_msg(t)
        time.sleep(0.6)
    time.sleep(1.2)
    if A.safe_tap(text="匹配下一个"):
        print("已点「匹配下一个」，等撮合…")
        if wait_conversation():
            time.sleep(2)
            card()
            return True
        print("!! 撮合超时，activity:", A.activity())
        return False
    print("「匹配下一个」不在，回星球重开")
    start()
    return True


def next():
    A.connect()
    if A.safe_tap(text="匹配下一个"):
        print("已点「匹配下一个」，等撮合…")
        if wait_conversation():
            time.sleep(2)
            card()
        else:
            print("!! 撮合超时，activity:", A.activity())
    else:
        print("「匹配下一个」不在了（窗口短），请跑 start 重新匹配")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "start"
    if cmd == "start":
        start()
    elif cmd == "next":
        next()
    elif cmd == "send_next":
        send_next(sys.argv[2:])
    elif cmd == "card":
        A.connect()
        card()
