#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Soul 轮次 SOP 取阅器（2026-09-29）

背景：自动化 prompt 里塞了 250 行规则，太长且每次都要重复传。
现在规则沉淀到 SOUL_轮次SOP.md，prompt 只留骨架，需要时一句话取回。

用法（一律走 soul.sh，不弹黑窗）：
    bash soul.sh soul_sop.py            # 打印完整 SOP
    bash soul.sh soul_sop.py --brief    # 只打印极简清单（每轮照着走）
"""
import io
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
SOP = os.path.join(BASE, "SOUL_轮次SOP.md")

BRIEF = """Soul 轮次极简清单
0  ⭐ 说话要像我本人：D:\\AI\\pl\\SOUL_我的画像.md（重庆綦江人/厂里看机器/晚上折腾AI/短句+自嘲/不装富不鸡汤）
1  bash soul.sh soul_auto.py          判锁+时段+待回；输出「⛔尚未结束」→ 立刻收工，别碰模拟器
2  bash soul.sh soul_watcher.py check 新消息（毫秒级）
3  读 reports/最新 复盘 的「九、结论区」→ 按它执行
4  回消息四步：读完整对话 → 重扫新消息 → 想清"该接什么" → 才发
5  第5闸：同一名词连续3条=死磕 → 下一条必须落到重庆细节；本轮≥1/3消息带锚点
6  铁律：≤20字/条（本人中位8字，写≤12最像）、一次2~3条、不模板化、她给顾虑≠拒绝（只降她的成本，绝不说"我去找你"）
7  全天同一循环：奇遇铃 > 回消息 > 匹配 > 广场评论 > 等回复（不再分白天夜里）
8  收尾：soul_report.py 轮次报告总结 → soul_direction.py 8（🔴零锚点必须下降）
   → soul_clear_unread.py 清红点 → soul_auto.py release
   判活：bash soul.sh _last_sends.py（我方最后发送距今<3分钟=上一轮还活着→跳过；心跳不可信）
   发消息：bash soul.sh soul_reply.py "昵称" "内容"（被闸拦是保护，别绕）
   ⛔ 发完必核原文：bash soul.sh soul_im.py chat "昵称"（脚本的成功/失败两个方向都错过）
   环境：soul_where.py 末行「截图 WxH」—— 宽<500px 就是 OCR 红线，用 mumu-cli layout_window 改回 541x993
   完整规则：bash soul.sh soul_sop.py
"""


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if "--brief" in sys.argv:
        print(BRIEF)
        return 0

    if not os.path.exists(SOP):
        print(f"❌ SOP 文件不存在：{SOP}")
        return 1
    with io.open(SOP, encoding="utf-8") as f:
        print(f.read())
    return 0


if __name__ == "__main__":
    sys.exit(main())
