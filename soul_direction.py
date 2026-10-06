# -*- coding: utf-8 -*-
"""方向审计：对话有没有朝「她来重庆」推进？

用户 2026-09-29 点名要查的两件事之一：「有没有朝我预期方向聊」。
原来的复盘只统计**违规**（说了"我去找你"这种反向话），但**沉默也是问题**——
聊了 20 轮一次重庆都没提过，等于原地踏步，也是没在推进。

本脚本输出三类：
  ⛔ 反向违规：说了"我去找你 / 我去你那"（方向反了，最严重）
  🔴 长聊无锚：真人对话 ≥N 条 但**我一次重庆锚点都没提过** → 该补
  ✅ 已带锚点：提过重庆/小面/火锅/来玩/带你吃/避坑 等

用法：
  python soul_direction.py            # 全部
  python soul_direction.py 8          # 只列"真人消息 ≥8 条且无锚点"的
"""
import sys, io, os, sqlite3

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
# ⚠️ 2026-09-29 修：模块级重设 sys.stdout 会关掉原 stdout，被别人 import 时后续 print 全挂。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ⭐ 2026-10-06 用户口径「单一数据源」：这三组红线名单**只在 `soul_rules.py` 定义一次**，
#   报告层（本文件）与提示词层（soul_brain / soul_daemon）引用同一份 —— 保证
#   「报告判违规」与「提示词明令禁写」永远是同一个名单（此前是两份手工拷贝，迟早分叉）。
#   · REVERSE / WEAK 另在 soul_rules 里组装成 `FORBID` 注入提示词（模型终于看得见红线）。
#   · ANCHORS 保持实例0 的重庆锚点（与改造前逐字一致，不改行为）。
from soul_rules import ANCHORS, REVERSE, WEAK   # noqa: F401


def scan():
    import soul_im as IM
    IM.pull()
    nm = IM.names()
    st = IM._db_status()
    c = sqlite3.connect(IM.IMDB)
    rows = c.execute("SELECT sessionId,toUserId FROM session").fetchall()
    out = []
    for sid, uid in rows:
        name = nm.get(str(uid), str(uid))
        if IM._is_official(name):
            continue
        msgs = c.execute("SELECT senderId,text,msgContent,localTime FROM chatmsg "
                         "WHERE sessionId=? ORDER BY localTime ASC", (sid,)).fetchall()
        real = [(s, t, lt) for s, t, ct, lt in msgs if not IM._is_sys(t, ct)]
        mine = [t for s, t, lt in real if str(s) == IM.ME and t]
        hers = [t for s, t, lt in real if str(s) != IM.ME and t]
        if not mine:
            continue
        joined = " ".join(mine)
        a = [k for k in ANCHORS if k in joined]
        r = [k for k in REVERSE if k in joined]
        w = [k for k in WEAK if k in joined]
        out.append({
            "name": name, "status": st.get(name), "mine": len(mine), "hers": len(hers),
            "total": len(real), "anchors": a, "reverse": r, "weak": w,
            "last": real[-1][2] if real else 0,
            "last_who": "她" if real and str(real[-1][0]) != IM.ME else "我",
        })
    c.close()
    return out


def main(min_real=8):
    import soul_im as IM
    rows = scan()
    rev = [x for x in rows if x["reverse"]]
    cold = [x for x in rows if not x["anchors"] and x["total"] >= min_real
            and x["mine"] >= 4]
    warm = [x for x in rows if x["anchors"]]
    cold.sort(key=lambda z: -z["total"])
    warm.sort(key=lambda z: -len(z["anchors"]))

    print("=" * 70)
    print(f"⛔ 方向违规（说了'我去找你'这类）: {len(rev)} 个")
    for x in rev:
        print(f"   {x['name']}  → 命中 {x['reverse']}")
    print()
    print(f"🔴 长聊但一次重庆锚点都没提（真人消息≥{min_real}）: {len(cold)} 个 —— 该补方向")
    print(f"   {'昵称':<22}{'真人条数':>7}{'我':>5}{'她':>5}  状态")
    for x in cold[:25]:
        print(f"   {x['name']:<22}{x['total']:>7}{x['mine']:>5}{x['hers']:>5}  {x['status']}")
    print()
    print(f"✅ 已带重庆锚点: {len(warm)} 个")
    for x in warm[:20]:
        print(f"   {x['name']:<22} 锚点={x['anchors'][:4]}"
              + (f"  ⚠️自贬={x['weak']}" if x["weak"] else ""))
    print()
    print(f"⚠️ 自贬/撤退表达（'那就算了'这类替她关门）: "
          f"{len([x for x in rows if x['weak']])} 个")
    for x in rows:
        if x["weak"]:
            print(f"   {x['name']}  → {x['weak']}")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8
    main(n)
