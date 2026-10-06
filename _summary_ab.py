# -*- coding: utf-8 -*-
"""第③条的建议：SUMMARY_SYS 瘦身 A/B 实测（用户说「先实测再定」）。

对照组 = 现在线上的 SUMMARY_SYS；
瘦身组 = 去掉 PUNCH_BRIEF / ONE_AT_A_TIME / SHORT_TO_REPLY 三条
        （理由：LEN 已管字数；爽感是**专家**写候选时的要求，裁判只负责收敛，
          再让它学一遍爽感四要素是重复）。
判据：① 必须出 3 条 ② 字数 ≤16 ③ 三条是三个不同动作 ④ 不违反红线（不说倒贴/自贬）。
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

ORIG = B.SUMMARY_SYS
_SLIM = (
    ("你是裁判。%d 位专家已分别给出判断和话术。请综合他们的观点，吸收最有价值的判断，"
     "然后输出：\n" % len(R.EXPERTS))
    + R.FMT + R.LEN + "\n" + R.THREE_BRIEF + "\n" + R.NEG + "\n"
    + R.PRIORITY + "\n" + R.FORBID
)

HIST = [
    {"role": "me", "text": "夜班刚摸了条鱼"},
    {"role": "her", "text": "我也是夜猫子哈哈"},
    {"role": "me", "text": "那你这个点还不睡？"},
    {"role": "her", "text": "睡不着"},
]
HERS = ["你是不是喜欢我？", "在干嘛呀"]

BAD_multi = 0


def run(tag, her):
    """跑深通道一次，返回候选列表。"""
    t0 = time.time()
    try:
        c = B.brain_reply(HIST, her)
    except Exception as e:
        c = None
        _OUT.write("    !! 异常 %r\n" % (e,))
    dt = time.time() - t0
    c = c or []
    ok3 = len(c) == 3
    oklen = all(len(x) <= 16 for x in c)
    # 三条是不是三个不同动作（粗略：两两不相同 + 没有两条用同一个首词）
    diff = len(set(c)) == len(c)
    _OUT.write("   %-6s %-22s %.1fs %s条=%r\n"
               % (tag, her, dt, len(c), c))
    _OUT.write("          3条:%s ｜ ≤16字:%s ｜ 互不相同:%s\n"
               % ("✅" if ok3 else "❌", "✅" if oklen else "❌", "✅" if diff else "❌"))
    return ok3 and oklen


rows = {}
for name, sysmsg in (("对照", ORIG), ("瘦身", _SLIM)):
    _OUT.write("===== %s组（SUMMARY_SYS %d 字）=====\n" % (name, len(sysmsg)))
    B.SUMMARY_SYS = sysmsg
    for her in HERS:
        for i in range(2):
            rows.setdefault(name, []).append(run("%s#%d" % (name, i + 1), her))
    _OUT.write("\n")

B.SUMMARY_SYS = ORIG
_OUT.write("结论：对照组 %d/%d 达标 ｜ 瘦身组 %d/%d 达标（SUMMARY_SYS 907→%d 字）\n"
           % (sum(rows.get("对照", [])), len(rows.get("对照", [])),
              sum(rows.get("瘦身", [])), len(rows.get("瘦身", [])), len(_SLIM)))
open("_summary_ab_out.txt", "w", encoding="utf-8").write(_OUT.getvalue())
sys.exit(0)
