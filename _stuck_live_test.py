# -*- coding: utf-8 -*-
"""真调：带「死磕警报」的智囊团，验证模型会不会因此换话题。

2026-10-06 用户批准的第 4 条：把原先只在发送闸里 print 的死磕检测，
回灌到生成侧提示词。这里实测它到底有没有用（模型是否真的避开那个由头）。
"""
import io
import os
import sys
import time

_OUT = io.StringIO()
sys.stdout = _OUT
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import soul_brain as B            # noqa: E402
import soul_rules as R            # noqa: E402


def run(tag, hist, her, stuck_in=None):
    _ln = R.stuck_line(stuck_in) if stuck_in else ""
    t0 = time.time()
    try:
        c = B.brain_reply(hist, her, stage_line="熟悉（42 轮 · 我 20 / 她 22）· 本阶段目标：信息交换 + 情绪共鸣",
                          stuck_line=_ln)
    except Exception as e:
        c = None
        _OUT.write("  !! 异常 %r\n" % (e,))
    dt = time.time() - t0
    _OUT.write("── %s\n" % tag)
    if _ln:
        _OUT.write("   注入的死磕警报：%s\n" % _ln.strip().replace("\n", " ｜ "))
    else:
        _OUT.write("   未注入死磕警报（对照）\n")
    _OUT.write("   她 = %r\n" % her)
    _OUT.write("   候选(%s 条, %.1fs) = %r\n" % (len(c) if c else 0, dt, c))
    if stuck_in and c:
        hit = [x for x in c if "鸡蛋" in x or "鸡" in x or "蛋" in x]
        _OUT.write("   %s\n" % ("❌ 还在说鸡蛋：" + str(hit) if hit else "✅ 已避开「鸡蛋」"))
    return bool(c) and len(c) == 3


# 我连着三轮都在说「鸡蛋」→ 必须换话题
MINE_EGGS = [
    {"role": "me", "text": "今早煮了两个鸡蛋"},
    {"role": "her", "text": "就吃这个啊"},
    {"role": "me", "text": "嗯 鸡蛋方便得很"},
    {"role": "her", "text": "太省了吧"},
    {"role": "me", "text": "明天多买点鸡蛋囤着"},
]
EGGS3 = ["今早煮了两个鸡蛋", "嗯 鸡蛋方便得很", "明天多买点鸡蛋囤着"]

a = run("① 对照：没注入警报时，她问「早饭吃的啥」", MINE_EGGS, "你早饭吃的啥", None)
b = run("② 注入警报后，同一个问题", MINE_EGGS, "你早饭吃的啥", EGGS3)
c = run("③ 注入警报 + 她随口「嗯嗯」", MINE_EGGS, "嗯嗯", EGGS3)

_OUT.write("\n" + ("全部通过 ✅（都出 3 条）" if (a and b and c) else "有不到 3 条的 ❌") + "\n")
_OUT.write("模型 = %s\n" % B.ARK_MODEL)
open("_stuck_live_out.txt", "w", encoding="utf-8").write(_OUT.getvalue())
sys.exit(0 if (a and b and c) else 1)
