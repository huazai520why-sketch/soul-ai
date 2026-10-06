# -*- coding: utf-8 -*-
"""soul_stage.py — 关系阶段：**唯一来源**（轮数口径 + 阶段门槛 + 提示词注入行）

═══════════════════════════════════════════════════════════════════
为什么要有这个模块（2026-10-06 用户追问「8~12 轮是不是太少了」时核查发现）
═══════════════════════════════════════════════════════════════════
改之前，轮次/阶段口径散在 **3 处、互不一致**，而且**一处都没接进生成链路**：

  ① `soul_brain.py:66`（PERSONA 里）「每 8~12 轮升温一档（陌生→熟→暧昧）」
     → 3 档 × 8~12 轮 ⇒ 约 **24~36 轮就"暧昧"**；
     而「轮」在这里**根本没定义**（是守护轮次？还是对话交替？）；
     更致命的是提示词里**没注入当前轮数** ⇒ 模型既不知现在第几轮、也不知此人聊了多少轮，
     这句话**无法执行**，等于没写。
  ② `soul_progress.STAGES`（用户 2026-09-29 定稿，**唯一权威**）
     → 0-30 初识 / 30-50 熟悉 / 50-100 推进 / 100+ 暧昧。与 ① 差 **约 3 倍**。
  ③ `soul_review.TURNS_STAGES` —— 与 ② **逐项相同的拷贝**
     （`soul_review.py:226-228` 自己都写了「与 soul_progress.turns_of 保持一致，
       否则阶段判断会错档」——已知会分叉）。
  ④ `soul_db` 的 `stage` L0~L4 亲密度阶梯 —— 全项目**无调用方**（死代码，未处理）。

现在本模块是 **①②③ 的唯一定义处**；并且阶段会被 `soul_daemon` **注入提示词**，
让智囊团和本地模型都知道「她现在在哪个阶段、这一阶段的目标是什么」。

───────────────────────────────────────────────────────────────────
轮的口径（用户 2026-09-29 定，**勿改**）
───────────────────────────────────────────────────────────────────
  · 「一轮」= **一次交替**（她发→我发），用 `senderId` 变化的**段数**近似，
    **不是消息条数**。
  · 只统计 `localTime >= 轮数基线` 的消息 —— 基线之前的对话**不算**
    （用户原话：「之前的对话轮次不算」）。
  · 数据源 = `soul_im.IMDB`（`im_data.db`）—— 与 `soul_progress` / `soul_review`
    两份报告**同一个库**，保证「报告里看到的轮数」= 「模型看到的阶段」。

本模块**只读**，不写任何文件；全部 **fail-open**（拿不到就返回空串/None，
绝不让调用方的回复链路挂掉）。
"""
import io
import json
import os
import sqlite3

BASE = os.path.dirname(os.path.abspath(__file__))

# ══════════════════════ 阶段门槛（用户 2026-09-29 定稿）══════════════════════
# (下限轮、上限轮、阶段名、该阶段目标)  —— 左闭右开
STAGES = [
    (0,     30,       "初识", "建立基本信任、找共同点、被记住"),
    (30,    50,       "熟悉", "信息交换 + 情绪共鸣，进入熟人区"),
    (50,    100,      "推进", "情绪投资 + 独特性（你是特别的）"),
    (100,   10 ** 9,  "暧昧", "性张力 + 见面渴望；必须已暧昧"),
]


def stage_of(turns):
    """轮数 → (阶段名, 该阶段目标)。非法轮数 → ("?", "")。"""
    try:
        t = int(turns)
    except Exception:
        return "?", ""
    for lo, hi, name, goal in STAGES:
        if lo <= t < hi:
            return name, goal
    return "?", ""


# 名字的裸版本 —— 给只需要阶段名的老调用方（soul_review 等）
def stage_name(turns):
    return stage_of(turns)[0]


# ══════════════════════ 轮数基线 ══════════════════════
def _base_file():
    """轮数基线文件路径（按实例隔离，双开不串号）。"""
    try:
        from soul_instance import state_path as _sp
        return _sp(BASE, ".soul_turn_base.json")
    except Exception:
        return os.path.join(BASE, ".soul_turn_base.json")


def base_ts():
    """轮数基线时间戳(ms)；0 = 算全历史（未设基线）。"""
    try:
        with io.open(_base_file(), encoding="utf-8") as f:
            return int(json.load(f).get("base_ts") or 0)
    except Exception:
        return 0


def base_str(ts=None):
    """基线的人话表示，给报告用。"""
    ts = base_ts() if ts is None else ts
    if not ts:
        return "（未设，算全历史）"
    try:
        from datetime import datetime
        return datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "?"


# ══════════════════════ 轮数统计（原 soul_progress.turns_of 迁移过来）══════════
def turns_of(sid, c, since=0):
    """按 senderId 变化分段，统计交替段数（≈轮数）。

    sid  : sessionId
    c    : sqlite3 连接（表 chatmsg）
    since: 只统计 localTime >= since 的消息（轮数基线）
    返回 {turns, mine, theirs, total, last, last_who}；异常 → 全 0 的字典。
    """
    empty = {"turns": 0, "mine": 0, "theirs": 0, "total": 0,
             "last": None, "last_who": None}
    if not sid:
        return empty
    try:
        import soul_im as I          # 懒加载：避免本模块被 import 时带出一堆副作用
        _me, _is_sys = I.ME, I._is_sys
    except Exception:
        return empty
    try:
        sql = ("SELECT senderId, text, msgContent, localTime FROM chatmsg "
               "WHERE sessionId=?")
        args = [sid]
        if since:
            sql += " AND localTime>=?"
            args.append(since)
        sql += " ORDER BY localTime ASC"
        rows = c.execute(sql, args).fetchall()
    except Exception:
        return empty
    segs, last = 0, None
    mine = theirs = 0
    last_t = None
    for sender, text, content, lt in rows:
        try:
            if _is_sys(text, content):
                continue
        except Exception:
            pass
        who = "me" if str(sender) == str(_me) else "her"
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


def _connect(sid=None):
    """连「官方口径」库（im_data.db）；失败回退守护累积库。返回 (conn, 是否官方)。"""
    try:
        import soul_im as I
        return sqlite3.connect(I.IMDB), True
    except Exception:
        pass
    try:
        return sqlite3.connect(os.path.join(BASE, "soul_memory.db")), False
    except Exception:
        return None, False


def stats_for_sid(sid):
    """sid → 轮数统计字典（含 since=基线）；拿不到 → None。"""
    c, _ = _connect(sid)
    if c is None:
        return None
    try:
        return turns_of(sid, c, since=base_ts())
    except Exception:
        return None
    finally:
        try:
            c.close()
        except Exception:
            pass


# ══════════════════════ 提示词注入行 ══════════════════════
# ⭐ 2026-10-06（用户「接进链路」）：把 `soul_db` 的**亲密度阶梯 L0~L4** 也接进来。
#   它原来是**死代码**（`set_stage` / `stage_map` 全项目无调用方），但内容是真有用的
#   —— 每档给出**具体该聊什么**（用户 2026-09-26 定：「别在一个话题上尬聊，往自己节奏上带」）：
#     L0 破冰｜她说过的具体细节 + 我一句状态（只描述不评价）
#     L1 共鸣｜找共同处境（都累/都一个人/都熬夜），把『我』摆进去
#     L2 私人化｜生活细节互换：吃啥、住哪、几点睡、家里几口人
#     L3 轻暧昧｜带称呼『你呀』、夸具体行为不夸外貌、开两人懂的玩笑、埋『以后一起』钩子
#     L4 邀约｜给具体时间地点 + 低压力（被拒退回 L3，不追问）
#   ⚠️ **冲突处理**：L4 原文「给具体时间地点」（= 我主动约）与用户铁律
#      「见面是她主导来见我，绝不是我去倒贴」直接冲突 → 注入时**叠加铁律约束行**，
#      不改动 `soul_db` 里的用户原文（原文保留，约束在注入层给，且对所有档位生效）。
#   唯一来源仍是 `soul_db.STAGES` —— 这里**只引用，不复制**。
_L_LEVELS = None

# 轮数 → 初始 L 档（**仅在 soul_db 里没记录时**使用）
_L_BANDS = [(0, 15, 0), (15, 30, 1), (30, 50, 2), (50, 100, 3), (100, 10 ** 9, 4)]


def levels():
    """{0..4: 'L0 破冰｜…'} —— 直接引用 `soul_db.STAGES`；拿不到 → {}。"""
    global _L_LEVELS
    if _L_LEVELS is None:
        try:
            import soul_db as _db
            _L_LEVELS = dict(_db.STAGES)
        except Exception:
            _L_LEVELS = {}
    return _L_LEVELS


def level_from_turns(turns):
    """轮数 → 派生 L 档；非法 → None。"""
    try:
        t = int(turns)
    except Exception:
        return None
    for lo, hi, lv in _L_BANDS:
        if lo <= t < hi:
            return lv
    return None


def level_of(name, turns):
    """她当前的 L 档 → (档位, 来源)；拿不到 → (None, "")。

    优先级：`soul_db` 里**有记录**（可被人工/脚本 `set_stage` 改写）> 按轮数派生。
    """
    if name:
        try:
            import soul_db as _db
            v = _db.stage_map().get(name)
            if v is not None:
                return int(v), "db"
        except Exception:
            pass
    lv = level_from_turns(turns)
    return (lv, "auto") if lv is not None else (None, "")


def level_line(name, turns):
    """亲密度档位 → 注入用的一行（含行动清单 + 铁律约束）；拿不到 → ''。"""
    lv, src = level_of(name, turns)
    if lv is None:
        return ""
    txt = levels().get(int(lv), "")
    if not txt:
        return ""
    return ("亲密度 L%d%s ｜ 本档行动清单：%s\n"
            "⚠️ 每轮最多推进一级，她不接就退回一级、不要硬顶；"
            "铁律不变：**见面必须她主导，绝不自己约、不倒贴**。"
            % (lv, "（已记录）" if src == "db" else "（按轮数推定）", txt))


def turn_line(sid, name=""):
    """给提示词用的**阶段摘要**；拿不到 → ""（fail-open）。

    例（sid 有数据时）：
      `熟悉（41 轮 · 我 15 / 她 26）· 本阶段目标：信息交换 + 情绪共鸣，进入熟人区`
      `亲密度 L2（按轮数推定） ｜ 本档行动清单：L2 私人化｜生活细节细分：…`
      `⚠️ 每轮最多推进一级…`

    ⚠️ 为什么要注入：2026-10-06 核查发现，提示词里原先只写了「每 8~12 轮升温一档」，
    但**没有任何地方告诉模型现在是第几轮** ⇒ 该规则无法执行。
    """
    s = stats_for_sid(sid)
    if not s:
        return ""
    turns = int(s.get("turns") or 0)
    if turns <= 0:
        return ""
    name_s, goal = stage_of(turns)
    if name_s == "?":
        return ""
    lines = ["%s（%d 轮 · 我 %d / 她 %d）· 本阶段目标：%s"
             % (name_s, turns, int(s.get("mine") or 0), int(s.get("theirs") or 0), goal)]
    _ll = level_line(name, turns)
    if _ll:
        lines.append(_ll)
    return "\n".join(lines)


if __name__ == "__main__":
    # 手动核对用：python soul_stage.py [sid]
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("轮数基线:", base_str())
    print("阶段表（轮数）:")
    for lo, hi, nm, gl in STAGES:
        print("  %4d~%-4s %s  ｜ %s" % (lo, ("%d" % hi) if hi < 10 ** 9 else "∞", nm, gl))
    print("亲密度阶梯（内容，来源 soul_db.STAGES）:")
    for lv in sorted(levels()):
        print("  L%d  %s" % (lv, levels()[lv]))
    print("轮数→L 派生:", [(lo, hi, "L%d" % lv) for lo, hi, lv in _L_BANDS])
    if len(sys.argv) > 1:
        sid = sys.argv[1]
        nm = sys.argv[2] if len(sys.argv) > 2 else ""
        st = stats_for_sid(sid)
        print("sid=%s name=%r → %s" % (sid, nm, st))
        print("注入行:\n%s" % (turn_line(sid, nm) or "(空)"))
