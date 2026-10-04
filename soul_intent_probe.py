# -*- coding: utf-8 -*-
"""
soul_intent_probe.py —— **决定性验证**：soul:// scheme 是不是真的按 uid 路由到正确的人

做法：用两个不同人的 uid 各开一次会话页，看顶部 OCR 标题是不是各自对得上。
      对得上 = 路由确实生效，能一步直达任意会话（不用滚列表找人）。

只开页面，**绝不发消息**；测完逐层返回。

用法： python soul_intent_probe.py
"""
import os
import sys
import time
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import soul        # noqa: E402
import soul_read   # noqa: E402
from soul_intent import round_alive, go_home, top_text  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 两个对照目标（昵称, uid）
TARGETS = [
    ("委委佗佗", "359737367"),
    ("请勿查户口", "501605581"),
]


def run(uid):
    """用 soul:// scheme 打开会话页，返回 (activity, 顶部文本)"""
    cmd = (f'am start -a android.intent.action.VIEW '
           f'-d "soul://ul.soulapp.cn/chat/conversationActivity?userIdEcpt={uid}"')
    out = soul.sh(cmd, timeout=60)
    time.sleep(3.5)
    act = soul.activity() or ""
    top = top_text()
    return act, top, out


def main():
    print("=" * 68)
    print("soul_intent_probe —— 验证 soul:// 会话路由是否按 uid 生效")
    print("=" * 68)

    busy, why = round_alive()
    print(f"\n[安全闸] {why}")
    if busy:
        print("\n❌ 有活跃轮次在跑 → 拒绝操作模拟器")
        return

    print("\n[前提] 拉前台")
    soul.ensure_foreground()
    time.sleep(1.5)
    print("   activity =", soul.activity())

    res = []
    for nick, uid in TARGETS:
        print(f"\n--- 目标 {nick} (uid={uid}) ---")
        act, top, out = run(uid)
        print(f"   activity = {act}")
        print(f"   顶部 OCR = {top}")
        hit = nick[:3] in (top or "")
        print(f"   → {'✅ 标题对上了' if hit else '❌ 没对上'}")
        res.append((nick, uid, act, top, hit))
        go_home()
        time.sleep(1.5)

    print("\n" + "=" * 68)
    print("结论")
    print("=" * 68)
    n_hit = sum(1 for r in res if r[4])
    for nick, uid, act, top, hit in res:
        print(f"  {'✅' if hit else '❌'} {nick:12s} uid={uid}")
        print(f"       activity: {act[:66]}")
        print(f"       顶部    : {top[:66]}")
    print()
    if n_hit == len(res):
        print("🎉 **路由生效**：soul://chat/conversationActivity?userIdEcpt=<uid> 能一步直达她的会话")
        print("   ⇒ 明文 uid 即可，不需要加密的 userIdEcpt；可彻底替代「滚列表找人 + OCR 比对」")
    elif n_hit > 0:
        print(f"⚠️ 部分生效（{n_hit}/{len(res)}）—— 可能是 OCR 没读到标题，需人工复核截图")
    else:
        print("❌ 路由没生效：打开的会话与人不对（或顶部 OCR 读不到昵称）")

    print("\n[收尾] 回列表")
    go_home()
    print("   最终 activity =", soul.activity())


if __name__ == "__main__":
    main()
