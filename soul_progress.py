# -*- coding: utf-8 -*-
"""关系阶段分析：统计每个对象的对话轮数 / 阶段 / 意向信号

用户战略（2026-09-29）：
  · 所有聊天目的 = 吸引 → 引导暧昧 → **终极：线下见面**
  · 30~50 轮 = 熟悉 | 50~100 轮 = 推进关系 | 100+ 轮 = **必须暧昧**
  · **见面必须她来找我**（不是我去找她）

本脚本只做**只读统计**，不改任何东西。轮数口径：
  「一轮」= 一次交替（她发→我发 算 1 轮）；用双方发言的"段"数近似，
  比单纯数消息条数更贴近用户说的"轮"。
"""
import sys, io, os, sqlite3
from collections import OrderedDict

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import soul_im as I


def _base():
    """轮数基线（2026-09-29 用户定：之前的对话轮次不算）。
    读 E:/soul/.soul_turn_base.json，返回起始时间戳(ms)；0=算全历史。"""
    import json
    try:
        from soul_instance import state_path as _sp   # 2026-09-30 双实例
        _f = _sp(BASE, ".soul_turn_base.json")
    except Exception:
        _f = os.path.join(BASE, ".soul_turn_base.json")
    try:
        with open(_f, encoding="utf-8") as f:
            return int(json.load(f).get("base_ts") or 0)
    except Exception:
        return 0


def _base_str(ts):
    from datetime import datetime
    return datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M") if ts else "（未设，算全历史）"

STAGES = [
    (0,   30,  "初识",   "建立基本信任、找共同点、被记住"),
    (30,  50,  "熟悉",   "信息交换 + 情绪共鸣，进入熟人区"),
    (50,  100, "推进",   "情绪投资 + 独特性（你是特别的）"),
    (100, 9999,"暧昧",   "性张力 + 见面渴望；**必须已暧昧**"),
]


def stage_of(turns):
    for lo, hi, name, goal in STAGES:
        if lo <= turns < hi:
            return name, goal
    return "未知", ""


# ==================== 意向评分（判断该重点投入谁）====================
def score_of(t, last_ts):
    """按 SOUL_关系推进方法论.md §4 打分。分数越高越值得投入。"""
    s = 0
    why = []
    if t["theirs"] > t["mine"]:
        s += 3
        why.append("她更主动+3")
    if t["last_who"] == "her":
        s += 1
        why.append("她在等回复+1")
    if t["theirs"] >= t["mine"] * 1.3:
        s += 2
        why.append("她投入明显更多+2")
    if t["mine"] > t["theirs"] * 1.5:
        s -= 2
        why.append("我在单方面追-2")
    # 新鲜度
    if last_ts:
        try:
            from datetime import datetime
            lt = datetime.fromtimestamp(last_ts / 1000) if last_ts > 1e11 \
                else datetime.fromtimestamp(last_ts)
            hours = (datetime.now() - lt).total_seconds() / 3600
            if hours < 3:
                s += 2
                why.append("刚活跃+2")
            elif hours > 48:
                s -= 4
                why.append("超48h没动静-4")
        except Exception:
            pass
    return s, why


# ==================== 下一步动作建议 ====================
ADVICE = {
    "初识": "找 1~2 个真实共同点；回扣她说过的话。**别暧昧、别查户口**",
    "熟悉": "建立习惯：固定时段出现 + 专属梗；让她开始分享情绪而不只是事实",
    "推进": "轻暧昧试探（'要是你在就好了'）+ 若即若离；开始植入见面想象但**不安排**",
    "暧昧": "明确暧昧张力 + 制造'错过可惜'；她提见面时**不立刻答应**（'看你表现'）",
}
DIRECTION_NOTE = "⚠️ 方向铁律：任何见面话题落点都是「她来重庆」，绝不说'我去找你'"


def advice_for(turns, stage, sc):
    base = ADVICE.get(stage, "")
    if stage == "推进" and turns >= 95:
        return "🔥 即将破100轮 —— **下一轮必须进入暧昧**：" + ADVICE["暧昧"]
    if turn_near_100(turns):
        return "🔥 逼近100轮，本轮开始加入暧昧元素：" + ADVICE["暧昧"]
    if sc <= -2:
        return "❄️ 我在单方面追/她冷 → **先撤一步降温**，隔几小时再回，别连发"
    if sc >= 4:
        return "💚 高意向（她会主动）→ 加深独占感，回得慢一点，让她更投入"
    return base


def turn_near_100(turns):
    return 90 <= turns < 100


def turns_of(sid, c, since=0):
    """按 senderId 变化分段，统计交替段数（≈轮数）
    since: 只统计 localTime >= since 的消息（轮数基线，2026-09-29 起用）"""
    sql = ("SELECT senderId, text, msgContent, localTime FROM chatmsg "
           "WHERE sessionId=?")
    args = [sid]
    if since:
        sql += " AND localTime>=?"
        args.append(since)
    sql += " ORDER BY localTime ASC"
    rows = c.execute(sql, args).fetchall()
    segs, last = 0, None
    mine = theirs = 0
    last_t = None
    for sender, text, content, lt in rows:
        if I._is_sys(text, content):
            continue
        who = "me" if str(sender) == str(I.ME) else "her"
        if who != last:
            segs += 1
            last = who
        if who == "me":
            mine += 1
        else:
            theirs += 1
        last_t = lt
    return {"turns": segs, "mine": mine, "theirs": theirs,
            "total": mine + theirs, "last": last_t, "last_who": last}


def main():
    out = main2()          # 统一走带评分的构建逻辑，避免两套口径打架

    print(f"[轮数基线] {_base_str(_base())}  ← 此前的对话不计（用户 2026-09-29 定）")
    print()
    print(f"{'轮数':>5} {'昵称':<14} {'我':>4} {'她':>4} {'分':>4}  {'阶段':<4} {'最后':<12} 信号")
    print("-" * 84)
    for turns, name, mine, theirs, stage, goal, sig, last, sc, why in out[:30]:
        print(f"{turns:>5} {name[:14]:<14} {mine:>4} {theirs:>4} {sc:>4}  {stage:<4} {last:<12} {sig}")
    print("-" * 84)
    dist = {}
    for r in out:
        dist[r[4]] = dist.get(r[4], 0) + 1
    print("阶段分布:", " | ".join(f"{k}:{v}人" for k, v in dist.items()))
    print(f"总计 {len(out)} 个真实对象")

    print()
    print("=" * 84)
    print("⭐ 重点推进对象（熟悉/推进阶段）—— 下一步该干什么")
    print("=" * 84)
    for turns, name, mine, theirs, stage, goal, sig, last, sc, why in out:
        if stage in ("推进", "熟悉"):
            adv = advice_for(turns, stage, sc)
            print(f"\n【{name}】{turns}轮（我{mine}/她{theirs}） 意向分 {sc:+d}  [{','.join(why) or '无'}]")
            print(f"  下一步: {adv}")
    print()
    print(DIRECTION_NOTE)


def main2():
    """重构版 main：带评分与建议"""
    I.pull()
    names = I.names()
    status = I._db_status()
    c = sqlite3.connect(I.IMDB)
    SINCE = _base()                    # ★ 轮数基线：只算这之后的消息
    rows = c.execute("SELECT sessionId, toUserId, timestamp FROM session "
                     "ORDER BY timestamp DESC").fetchall()
    out = []
    for sid, uid, ts in rows:
        name = names.get(str(uid), "")
        if not name or I._is_official(name):
            continue
        t = turns_of(sid, c, since=SINCE)
        if t["turns"] == 0:
            continue
        stage, goal = stage_of(t["turns"])
        sc, why = score_of(t, t["last"])
        sig = []
        if t["theirs"] > t["mine"] * 1.2:
            sig.append("她更主动")
        if t["last_who"] == "her":
            sig.append("待回")
        st = status.get(name)
        if st:
            sig.append(f"标记:{st}")
        out.append((t["turns"], name, t["mine"], t["theirs"], stage, goal,
                    ",".join(sig), I._fmt(t["last"]) if t["last"] else "", sc, why))
    c.close()
    out.sort(key=lambda z: -z[0])
    return out


if __name__ == "__main__":
    main()
