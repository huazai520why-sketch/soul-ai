#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把模拟器页面摆正回**聊天列表页**（并滚到顶）。

为什么需要它：
2026-09-29 21:26 轮，`soul_reply.py` 的 find() 点到人主页(UserHomeActivity)，
再点"搜索框"坐标落在了会话行/头像上 → 进了他资料页 → 搜索通道报「未检出搜索页特征 → 放弃」，
人找不到，**这一轮的发消息全废**。
Reply 内部虽有 `_goto_chat_list()`，但它只在 find() 开头跑一次，
且失败后会一路走到搜索兜底；外部没有一个"先把页面摆正"的入口。
⇒ 本脚本 = 每轮开工/找人失败后的**复位键**。

用法：
    bash soul.sh soul_goto.py            # 回到聊天列表 + 回顶
    bash soul.sh soul_goto.py --top      # 同上（默认）
    bash soul.sh soul_goto.py --shot     # 复位后顺带 dump 前 20 行 OCR
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import soul
    import soul_read as rd
    import soul_reply as R

    for i in range(3):
        ok = R._goto_chat_list()
        print("尝试 %d：_goto_chat_list() → %s" % (i + 1, "已在聊天列表" if ok else "仍不在"))
        if ok:
            try:
                R._scroll_top()
                print("已回顶")
            except Exception as e:
                print("回顶失败:%r" % (e,))
            break
        time.sleep(1.5)

    ok = R._on_chat_list()
    print("最终：%s | activity=%s" % ("✅ 聊天列表页" if ok else "❌ 未到聊天列表", soul.activity()))
    if "--shot" in argv and ok:
        soul.screenshot()
        for t, cx, cy in (rd.items() or [])[:20]:
            print("    y=%-5d x=%-5d %r" % (cy, cx, t))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
