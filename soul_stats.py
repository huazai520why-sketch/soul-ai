# -*- coding: utf-8 -*-
"""Soul 关系档案分析 —— 开采 im_user_bean 里**一直没被用过**的关系字段。

现状：soul_im.py 只从 im_user_bean 取了 `userId + signature`（当昵称用）。
      剩下的 intimacy(轮次/心动/等级) / comeFrom(来源) / blocked(拉黑)
      / soulmate / heartBeatUrl 全部闲置 —— 本模块专门开采这一层。

数据源（本地直读，零 token、零 UI 操作）：
    D:\\AI\\pl\\chat_im.db  → im_user_bean（聊天对象档案）
    D:\\AI\\pl\\im_data.db  → chatmsg / session（消息、最后发言）
    数据新鲜度由调用方负责：`soul_monitor.check_pending()` / `soul_im.pull()` 都会先拉库。

作为模块（soul_auto.py 在用，别再重复写 SQL）：
    import soul_stats as S
    S.users()                 # 全部档案（含摊平的 _rounds/_heart/_grade/_name）
    S.activity(48)            # 近 48h 有消息的，按轮次排 → [{...}]
    S.revive(12, 72)          # 我最后发言后 12~72h 未回（活对话凉了，值得唤醒）
    S.sunk(72)                #  >72h 未回（首批搭讪沉底，别浪费配额）
    S.invest()                # 【投入度榜】按「她主动打探我」排 —— 谁真对我有兴趣
    S.ready(u)                # (是否够格推进, [未达标原因]) —— 门槛判定，踩刹车用
    S.profile("昵称")          # 单人完整档案 dict
    S.last_of(uid)            # 某人会话的最后一条 (senderId, ts, text, msgType)

命令行：
    python soul_stats.py              # 全量：温度榜 + 投入度榜 + 心动榜 + 来源 + 拉黑
    python soul_stats.py invest [N]   # ⭐ 投入度榜（她主动打探我几次）—— 挑重点只认这个
    python soul_stats.py ready        # ⭐ 够格推进的（通常个位数）
    python soul_stats.py rank [N]     # 关系温度榜（默认 30）
    python soul_stats.py heart        # 有过心动的
    python soul_stats.py cold [N]     # 冷场：分「活对话」与「沉底」两批
    python soul_stats.py revive       # 只列活对话里凉了的（12~72h）
    python soul_stats.py who 昵称      # 单人完整档案（含投入度 + 推进门槛）
    python soul_stats.py --json ...    # 任何子命令加 --json → 结构化输出
"""
import datetime
import json
import os
import re
import sqlite3
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
try:  # 2026-09-30 双实例：本库文件按实例分开（否则实例1 统计到主号的会话）
    from soul_instance import state_path as _sp
except Exception:
    def _sp(base, name):
        return os.path.join(base, name)

CHATDB = _sp(BASE, "chat_im.db")
IMDB = _sp(BASE, "im_data.db")
try:  # 2026-09-30 双实例：身份按实例取，别再写死主号 uid
    from soul_im import ME
except Exception:
    ME = "96691646"                  # 我自己的 userId

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 消息类型 → 无正文时的占位显示
MSGTYPE = {2: "[图片]", 5: "[语音]", 6: "[视频]", 8: "[表情]",
           9: "[其他]", 27: "[卡片]", 32: "[礼物]", 35: "[系统]"}
SRC_LAB = {"MATCHING": "匹配", "LOVEBELL": "奇遇铃", "STAR": "星球",
           "CONTENT_SQUARE": "广场"}

# ---------------------------------------------------------------- 数据层
_UC = None      # users 缓存
_LC = None      # last_of 缓存


def users(force=False):
    """全部聊天对象档案。intimacy JSON 摊平成 _rounds/_heart/_heartNow/_grade。"""
    global _UC
    if _UC is not None and not force:
        return _UC
    c = sqlite3.connect(CHATDB)
    cols = [x[1] for x in c.execute("PRAGMA table_info(im_user_bean)")]
    out = []
    for r in c.execute("select * from im_user_bean"):
        d = dict(zip(cols, r))
        try:
            iv = json.loads(d["intimacy"]) if d.get("intimacy") and d["intimacy"] != "null" else {}
        except Exception:
            iv = {}
        d["_rounds"] = iv.get("roundCount", 0) or 0
        d["_heart"] = iv.get("heartTotalCount", 0) or 0
        d["_heartNow"] = iv.get("heartCount", 0) or 0
        d["_grade"] = iv.get("grade", 0) or 0
        d["_name"] = (d.get("signature") or "").strip() or f"uid{d['userId']}"
        d["_src"] = SRC_LAB.get(d.get("comeFrom"), (d.get("comeFrom") or "")[:7])
        out.append(d)
    c.close()
    _UC = out
    return _UC


def _last_map(force=False):
    """{sessionId: (senderId, ts, text, msgType)} —— 每会话最后一条。"""
    global _LC
    if _LC is not None and not force:
        return _LC
    c = sqlite3.connect(IMDB)
    g = {}
    for sid, s, t, tx, mt in c.execute(
        "select sessionId, senderId, localTime, text, msgType from chatmsg order by localTime"
    ):
        g[sid] = (str(s), t, tx or "", mt)
    c.close()
    _LC = g
    return _LC


def last_of(uid, force=False):
    """某人的最后一条 → (senderId, ts, text, msgType)；无会话 → None"""
    return _last_map(force).get(f"96691646{uid}")


_MSC = None     # 消息统计缓存


def msg_stat(force=False):
    """{sessionId: [我发条数, 她发条数]} —— **当前可见**的互动量。

    ⚠️ 为什么需要它：`intimacy.roundCount` 是 **Soul 服务端累计的历史互动深度**
    （含已被清理的消息），**不等于本地可见的对话量**。实测反例（2026-09-30）：
      · 「辰」roundCount=82，但本地只有 20 条消息（历史多、本地留存少）
      · 「委委佗佗」roundCount=**0**，本地却有 71 条（正热聊，服务端还没计入）
    ⇒ **只用 roundCount 排序会把最热的人排到最后**。判当前热度必须用「她发言数」。
    """
    global _MSC
    if _MSC is not None and not force:
        return _MSC
    c = sqlite3.connect(IMDB)
    d = {}
    for sid, s in c.execute("select sessionId, senderId from chatmsg"):
        e = d.setdefault(sid, [0, 0])
        if str(s) == ME:
            e[0] += 1
        else:
            e[1] += 1
    c.close()
    _MSC = d
    return _MSC


# 对方「主动打探我」的近似判据（问号 / 第二人称 + 疑问指向）。
# ⚠️ 这是**下界**不是精确值：反讽、连续追问、纯表情都会被漏计。
#    但作为「她到底关不关心我」的横向对比指标，够用且比"字数"可靠得多。
_ASK = re.compile(
    r"[?？]"
    r"|你.{0,4}(哪|什么|怎么|干嘛|干嘛呢|多大|几岁|做什么|住|在吗|呢|喜欢|有没有)"
    r"|^(你|在干嘛|在吗|哪|几岁|多大|什么)"
)
_GAP_MS = 30 * 60 * 1000        # 间隔 >30min 视为「她新起一个话题波」

_EC = None      # engage_map 缓存


def engage_map(force=False):
    """{sessionId: {...}} —— 对方**真实投入度**的四项硬指标。

    ⚠️ 为什么不看「她平均字数」：实测（2026-09-30）「风止遇你」她均 11.2 字看着很热，
    但她从头到尾只主动问过我 1 次（"你是哪里的"）—— 写长句只说明**愿意聊话题**，
    不等于**对我这个人有兴趣**。判投入度必须看 `ask`（她主动打探我几次）。

    返回每会话：
      ask    : 她主动打探我的条数（见 _ASK，是下界）
      ntext  : 她带正文的条数（表情/图片不算）
      chars  : 她带正文的总字数
      avg    : 均字数 = chars/ntext
      opens  : 她**主动开话题**次数（她发的、且距上一条 >30min 的消息）
      burst  : 会话被切成的「话题波」总数（用来算 opens 占比）
    """
    global _EC
    if _EC is not None and not force:
        return _EC
    c = sqlite3.connect(IMDB)
    rows = c.execute(
        "select sessionId, senderId, localTime, text from chatmsg order by sessionId, localTime"
    ).fetchall()
    c.close()
    out = {}
    cur_sid, prev_ts = None, 0
    for sid, s, lt, tx in rows:
        if sid != cur_sid:
            cur_sid, prev_ts = sid, 0
        e = out.setdefault(sid, {"ask": 0, "ntext": 0, "chars": 0, "opens": 0, "burst": 0})
        if lt - prev_ts > _GAP_MS:
            e["burst"] += 1
            if str(s) != ME:
                e["opens"] += 1
        prev_ts = lt
        if str(s) != ME:
            t = tx or ""
            if t.strip():
                e["ntext"] += 1
                e["chars"] += len(t)
                if _ASK.search(t):
                    e["ask"] += 1
    for e in out.values():
        e["avg"] = round(e["chars"] / e["ntext"], 1) if e["ntext"] else 0.0
    _EC = out
    return _EC


def _ago(ts_ms):
    """距今小时数"""
    return (datetime.datetime.now().timestamp() * 1000 - ts_ms) / 3600000.0


def _ago_str(h):
    if h < 1:
        return f"{h*60:.0f}分钟前"
    if h < 48:
        return f"{h:.0f}h前"
    return f"{h/24:.0f}天前"


def _sys(d):
    """系统/官方号：uid 为负，或昵称明显是官方"""
    try:
        if int(d["userId"]) < 0:
            return True
    except Exception:
        return True
    n = d["_name"]
    return any(k in n for k in ("Soul小助手", "官方", "客服", "系统消息"))


def _enrich(u, last):
    """给档案挂上最后消息 + 本地消息量，供排序/展示"""
    g = last.get(f"96691646{u['userId']}")
    u["_last"] = g[1] if g else 0
    u["_lastwho"] = ("我" if g[0] == ME else "她") if g else "?"
    u["_lasttext"] = g[2] if g else ""
    u["_lasttype"] = g[3] if g else 0
    u["_ago"] = _ago(g[1]) if g else 1e9
    ms = msg_stat().get(f"96691646{u['userId']}", [0, 0])
    u["_my"], u["_her"] = ms[0], ms[1]
    u["_total"] = ms[0] + ms[1]
    e = engage_map().get(f"96691646{u['userId']}") or {}
    u["_ask"] = e.get("ask", 0)
    u["_avg"] = e.get("avg", 0.0)
    u["_opens"] = e.get("opens", 0)
    return u


def _txt(s):
    """无正文 → 用消息类型占位"""
    if s:
        return s
    return ""      # 类型占位在 _txt_of 里按 msgType 补


def _txt_of(text, mt):
    return text or MSGTYPE.get(mt, "[无正文]")


# ---------------------------------------------------------------- 业务视图
def activity(hours=48):
    """近 N 小时有消息的档案，按轮次降序。"""
    us = enriched()
    us = [u for u in us if u["_last"] and u["_ago"] <= hours]
    us.sort(key=lambda u: (-u["_her"], -u["_rounds"], -u["_heart"]))
    return us


def revive(hmin=12, hmax=72):
    """我最后发言后 hmin~hmax 小时未回 —— 活对话凉了，值得唤醒（按凉的时间降序）。"""
    last = _last_map()
    out = []
    for u in users():
        if _sys(u):
            continue
        _enrich(u, last)
        if u["_lastwho"] == "我" and hmin <= u["_ago"] <= hmax:
            out.append(u)
    # 她说过越多，越值得唤醒（0 轮/她 0 条的搭讪排在后面）
    out.sort(key=lambda u: (-u["_her"], -u["_ago"]))
    return out


def sunk(hours=72):
    """超 N 小时未回 —— 首批搭讪沉底批，别再花配额（按轮次降序，轮次高的才值得捞）。"""
    last = _last_map()
    out = []
    for u in users():
        if _sys(u):
            continue
        _enrich(u, last)
        if u["_lastwho"] == "我" and u["_ago"] > hours:
            out.append(u)
    out.sort(key=lambda u: (-u["_her"], -u["_rounds"]))
    return out


# 推进门槛（2026-09-30 实测标定，全部基于「她主动打探我」而非字数/条数）
READY_ASK = 2          # 她主动打探我 ≥2 次
READY_AVG = 6.0        # 她带正文均字数 ≥6
READY_HER = 20         # 她发过 ≥20 条
READY_AGO = 72         # 最后互动 ≤72h


def ready(u):
    """判断**是否够格推进** → (bool, [未达标原因])。

    「推进」在此的定义：**更直接地表达自己的意愿**（说清楚想认识她 / 提议一次具体、
    可拒绝的线下）。**不含**催促、施压、反复纠缠 —— 那些不是推进，是骚扰，本模块不提供。

    设计意图是**踩刹车而不是踩油门**：实测 242 个会话里只有 9 个达标，
    剩下的都该被拦住，省掉无效消耗。
    """
    bad = []
    if u["_ask"] < READY_ASK:
        bad.append(f'她只主动问过我 {u["_ask"]} 次(<{READY_ASK})')
    if u["_avg"] < READY_AVG:
        bad.append(f'她均 {u["_avg"]} 字(<{READY_AVG})')
    if u["_her"] < READY_HER:
        bad.append(f'她只发 {u["_her"]} 条(<{READY_HER})')
    if not u["_last"]:
        bad.append("无会话")
    elif u["_ago"] > READY_AGO:
        bad.append(f'已 {_ago_str(u["_ago"])} 没动静(>{READY_AGO}h)')
    return (not bad), bad


def enriched():
    """全部档案 + 已富化字段（_ask/_avg/_her/_last/_ago/...）。

    ⚠️ `users()` 返回的是**原始 bean**，没有 `_ask`/`_avg` —— 直接对它调 `ready()`
    会 `KeyError('_ask')`（2026-09-30 踩过）。要判门槛/排序一律走这个。
    """
    last = _last_map()
    out = []
    for u in users():
        if _sys(u):
            continue
        _enrich(u, last)
        out.append(u)
    return out


def invest(limit=None):
    """【投入度榜】按「她主动打探我」排 —— 谁真的对我这个人有兴趣。

    排序键：(ask, avg, her, -ago) —— 先看她问了我几次，再看她肯写多长。
    """
    us = [x for x in enriched() if x["_her"] > 0]
    us.sort(key=lambda u: (-u["_ask"], -u["_avg"], -u["_her"], u["_ago"]))
    return us[:limit] if limit else us


def ready_list():
    """已达推进门槛的人（按投入度排）"""
    return [u for u in invest() if ready(u)[0]]


def profile(kw):
    """按昵称（子串）或 uid 精确查档案 → dict；无 → None"""
    hit = [u for u in users() if kw == str(u["userId"]) or kw == u["_name"]]
    if not hit:
        hit = [u for u in users() if kw in u["_name"]]
    return _enrich(hit[0], _last_map()) if hit else None


def heart_list():
    last = _last_map()
    us = [_enrich(u, last) for u in users() if u["_heart"] > 0 or u["_heartNow"] > 0]
    us.sort(key=lambda u: (-u["_heart"], -u["_rounds"]))
    return us


def blocked_list():
    return ([u for u in users() if u["blocked"]],
            [u for u in users() if u["blockedByTarget"]])


def focus_map():
    """读 .soul_focus.json → {昵称: 配置}（读不到返回 {}）

    ⚠️ 2026-09-30 双实例：重点对象名单也必须按实例分开，
    否则账号2 会继承主号的"⭐重点对象"及其备注（实测漏过，已修）。
    """
    p = _sp(BASE, ".soul_focus.json")
    try:
        with open(p, encoding="utf-8") as f:
            return (json.load(f) or {}).get("focus", {}) or {}
    except Exception:
        return {}


def to_dict(u):
    """裁剪成瘦 dict（给 --json / auto 用）。未 enrich 的档案会自动补。"""
    if "_ask" not in u:
        u = _enrich(u, _last_map())
    r, why = ready(u)
    return {
        "name": u["_name"], "uid": str(u["userId"]),
        "rounds": u["_rounds"], "heart": u["_heart"], "heart_now": u["_heartNow"],
        "grade": u["_grade"], "src": u["_src"],
        "her_msgs": u["_her"], "my_msgs": u["_my"], "total_msgs": u["_total"],
        "her_ask": u["_ask"], "her_avg_chars": u["_avg"], "her_opens": u["_opens"],
        "ready": r, "not_ready_because": why,
        "last_ago_h": round(u["_ago"], 1) if u["_last"] else None,
        "last_who": u["_lastwho"],
        "last_text": _txt_of(u["_lasttext"], u["_lasttype"]),
        "follow": u.get("follow"), "followed": u.get("followed"),
        "blocked": u.get("blocked"), "blocked_by": u.get("blockedByTarget"),
    }


# ---------------------------------------------------------------- 打印层
def _p_rank(us, limit=30):
    print(f"\n【关系温度榜】按**她发言数**排（共 {len(us)} 人有最后消息记录）")
    print("  ⚠️ roundCount 是服务端累计历史（含已清理消息），不等于本地可见量；")
    print("     判当前热度看「她发」——委委佗佗 roundCount=0 却发了 38 条，本轮实测反例。")
    print(f'  {"她发":>4} {"我发":>4} {"轮次":>4} {"心动":>4}  {"昵称":<16} {"来源":<6} {"最后消息":<10} 谁')
    print("  " + "-" * 82)
    for u in us[:limit]:
        print(f'  {u["_her"]:>4} {u["_my"]:>4} {u["_rounds"]:>4} {u["_heart"]:>2}/{u["_heartNow"]:<1}  '
              f'{u["_name"][:16]:<16} {u["_src"]:<6} {_ago_str(u["_ago"]):<10} {u["_lastwho"]}')


def _p_invest(us, limit=30):
    ok = [u for u in us if ready(u)[0]]
    print(f"\n【投入度榜】按「她主动打探我」排 —— 共 {len(us)} 人聊过，其中 {len(ok)} 人达推进门槛")
    print("  判据：她问过我几次 > 她肯写多长 > 她发多少条。⚠️ 别再看「她均字数」——")
    print("        「风止遇你」均 11.2 字却只问过我 1 次，写长句≠对我有兴趣。")
    print(f'  {"她问":>4} {"均字":>5} {"她发":>4} {"我发":>4} {"轮":>4}  {"昵称":<16} {"最后":<9} 门槛')
    print("  " + "-" * 78)
    for u in us[:limit]:
        r, why = ready(u)
        tag = "✅可推进" if r else "✗" + (why[0][:14] if why else "")
        print(f'  {u["_ask"]:>4} {u["_avg"]:>5} {u["_her"]:>4} {u["_my"]:>4} {u["_rounds"]:>4}  '
              f'{u["_name"][:16]:<16} {_ago_str(u["_ago"]):<9} {tag}')


def _p_heart(us):
    print(f"\n【心动榜】累计/当前（共 {len(us)} 人有过心动）")
    for u in us:
        print(f'  {u["_heart"]:>3}/{u["_heartNow"]:<2}  {u["_name"][:18]:<18} '
              f'{u["_rounds"]:>3}轮  G{u["_grade"]}  {u["_src"]}')


def _p_source():
    from collections import Counter
    c = Counter(u["_src"] or "?" for u in users())
    print(f"\n【来源分布】共 {len(users())} 条档案")
    for k, n in c.most_common():
        print(f'  {k:<8} {n:>4} 条')


def _p_blocked(b1, b2):
    print(f"\n【拉黑】我拉黑 {len(b1)} 人｜对方拉黑我 {len(b2)} 人")
    for u in b1:
        print(f'  我拉黑 → {u["_name"]:<18} {u["_rounds"]}轮')
    for u in b2:
        print(f'  ⚠️ 拉黑我 → {u["_name"]:<18} {u["_rounds"]}轮')


def _p_revive(rs, show=30):
    print(f"\n【凉了·值得唤醒】我最后发言后 12~72h 未回（共 {len(rs)} 人，她说过越多越靠前）")
    for u in rs[:show]:
        t = _txt_of(u["_lasttext"], u["_lasttype"])
        print(f'  {_ago_str(u["_ago"]):<9} {u["_name"][:16]:<16} 她发{u["_her"]:>3} '
              f'轮{u["_rounds"]:>3} G{u["_grade"]} 我最后说: {t[:24]}')


def _p_cold(hours):
    live = revive(12, 72)
    dead = sunk(max(hours, 72))
    _p_revive(live)
    print(f"\n【沉底】超 {max(hours,72)}h 未回（共 {len(dead)} 人，只列她说过话的 —— 其余可放弃）")
    for u in dead[:12]:
        print(f'  {_ago_str(u["_ago"]):<9} {u["_name"][:16]:<16} 她发{u["_her"]:>3} '
              f'轮{u["_rounds"]:>3} 我最后说: {_txt_of(u["_lasttext"], u["_lasttype"])[:24]}')
    if len(dead) > 12:
        print(f'  …另有 {len(dead)-12} 人（多为 0~1 轮的首批搭讪，可整批放弃）')


def _p_who(u):
    print(f'\n=== {u["_name"]} ===')
    print(f'  userId      {u["userId"]}   userIdEcpt {u["userIdEcpt"]}')
    print(f'  来源        {u["_src"]}   轮次/心动 {u["_rounds"]} / {u["_heart"]}(累计) {u["_heartNow"]}(当前)  G{u["_grade"]}')
    print(f'  本地消息    共 {u["_total"]} 条（她 {u["_her"]} / 我 {u["_my"]}）')
    print(f'  关注        我→ta {u["follow"]}  ta→我 {u["followed"]}  mutual {u["mutualFollow"]}')
    print(f'  拉黑        我拉黑={u["blocked"]}  对方拉黑我={u["blockedByTarget"]}')
    print(f'  soulmate    {u["targetUserSoulmate"]} / {u["myUserSoulmate"]}')
    print(f'  心动记录    {u["heartBeatUrl"] or "无"}')
    r, why = ready(u)
    print(f'  投入度      她主动打探我 {u["_ask"]} 次 · 她均 {u["_avg"]} 字 · 她开话题 {u["_opens"]} 次')
    print(f'  推进门槛    {"✅ 已达标 —— 可以直接表达意愿/提议具体见面" if r else "✗ 未达： " + " / ".join(why)}')
    print(f'  最后消息    {u["_lastwho"]} {_ago_str(u["_ago"]) if u["_last"] else "无"} '
          f'{_txt_of(u["_lasttext"], u["_lasttype"])[:30]}')
    fl = focus_map()
    if u["_name"] in fl:
        print(f'  ⭐ 重点对象  [{fl[u["_name"]].get("level","重点")}] {fl[u["_name"]].get("reason","")}')


def main():
    args = [a for a in sys.argv[1:] if a != "--json"]
    as_json = "--json" in sys.argv
    mode = args[0] if args else "all"

    if as_json:
        if mode == "rank":
            data = [to_dict(u) for u in activity(1e9)]
        elif mode == "heart":
            data = [to_dict(u) for u in heart_list()]
        elif mode == "cold":
            data = {"live": [to_dict(u) for u in revive(12, 72)],
                    "sunk": [to_dict(u) for u in sunk(72)]}
        elif mode == "revive":
            data = [to_dict(u) for u in revive(12, 72)]
        elif mode == "invest":
            data = [to_dict(u) for u in invest()]
        elif mode == "ready":
            data = [to_dict(u) for u in ready_list()]
        elif mode == "who":
            u = profile(args[1])
            data = to_dict(u) if u else None
        else:
            b1, b2 = blocked_list()
            data = {"rank": [to_dict(u) for u in activity(1e9)][:40],
                    "heart": [to_dict(u) for u in heart_list()],
                    "blocked": {"by_me": [to_dict(u) for u in b1],
                                "by_target": [to_dict(u) for u in b2]}}
        print(json.dumps(data, ensure_ascii=False, indent=1))
        return

    if mode == "rank":
        _p_rank(activity(1e9), int(args[1]) if len(args) > 1 else 30)
    elif mode == "heart":
        _p_heart(heart_list())
    elif mode == "cold":
        _p_cold(int(args[1]) if len(args) > 1 else 72)
    elif mode == "revive":
        _p_revive(revive(12, 72))
    elif mode == "invest":
        _p_invest(invest(), int(args[1]) if len(args) > 1 else 30)
    elif mode == "ready":
        rs = ready_list()
        print(f"\n【够格推进】共 {len(rs)} 人（她主动打探我≥{READY_ASK} 且 均字≥{READY_AVG} "
              f"且 她发≥{READY_HER} 且 {READY_AGO}h 内有互动）")
        for u in rs:
            print(f'  {u["_ask"]:>3}次 均{u["_avg"]:>4}字 她发{u["_her"]:>3}  {u["_name"][:16]:<16} '
                  f'{_ago_str(u["_ago"])}')
        if not rs:
            print("  （一个都没有 —— 不是话术问题，是这批人本来就没兴趣）")
    elif mode == "who":
        u = profile(args[1])
        _p_who(u) if u else print(f"档案里没有匹配 '{args[1]}' 的人")
    else:
        _p_invest(invest())
        _p_rank(activity(1e9))
        _p_heart(heart_list())
        _p_source()
        _p_blocked(*blocked_list())


if __name__ == "__main__":
    main()
