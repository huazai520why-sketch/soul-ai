#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""查某天我发出的消息有没有重复（=自动化在群发模板话）。

用法：
    bash soul.sh soul_dupcheck.py            # 今天
    bash soul.sh soul_dupcheck.py 2026-10-01 # 指定日

为什么需要：用户铁律要求「发现自动化在批量发模板话 → 必须指出」。
soul_review.py 的报告按人分列，同一句话发给两个人时肉眼极难发现，
必须按**文案聚合**才能看出来。2026-10-02 首次用它抓到
「我这边还热 你倒是躲对了」18:09/18:10 分别发给风止遇你与委委佗佗。

判据：同一天同一句文案出现 ≥2 次 = 群发，一律要改（换个人发也成立就是废话）。
"""
import sys, os, time, sqlite3, collections, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import soul_im as I

d = sys.argv[1] if len(sys.argv) > 1 else time.strftime("%Y-%m-%d")
d0 = datetime.datetime.strptime(d, "%Y-%m-%d")
lo = int(d0.timestamp() * 1000)
hi = int((d0 + datetime.timedelta(days=1)).timestamp() * 1000)

c = sqlite3.connect(I.IMDB)
uid2name = I.names()
sid2name = {}
for sid, uid in c.execute("SELECT sessionId, toUserId FROM session"):
    nm = uid2name.get(str(uid), "")
    if nm:
        sid2name[sid] = nm

# 只看**我发的文字**；tagAuthGuide 等系统卡片不计（那是 Soul 平台推的，不是我写的）
rows = c.execute(
    "SELECT sessionId, senderId, text, localTime FROM chatmsg "
    "WHERE localTime>=? AND localTime<? AND senderId=? ORDER BY localTime",
    (lo, hi, str(I.ME))).fetchall()

groups = collections.defaultdict(list)
for sid, s, txt, lt in rows:
    t = (txt or "").strip()
    if not t or t.startswith("{"):
        continue
    groups[t].append((sid2name.get(sid) or str(sid)[:8],
                      time.strftime("%H:%M", time.localtime(lt / 1000))))

dups = sorted([(t, v) for t, v in groups.items() if len(v) > 1], key=lambda x: -len(x[1]))
print("[%s] 我发文字 %d 条 / 去重 %d 条 / 重复文案 %d 组" % (
    d, sum(len(v) for v in groups.values()), len(groups), len(dups)))
if dups:
    print("---- 群发嫌疑（同一句发给多人）----")
    for t, v in dups:
        print("  x%d  %r" % (len(v), t))
        for n, ts in v:
            print("        %s @%s" % (n, ts))
else:
    print("✅ 今天没有发现同一句发给多人")
