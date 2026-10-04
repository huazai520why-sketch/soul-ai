# -*- coding: utf-8 -*-
"""Soul 每日复盘器（2026-09-29 用户要求："每天总结复盘，看有没有按终极目标推进"）

定位：**只产出事实，不做判断**。
  脚本能规则化检查的（方向违规、字数超标、敷衍回复、她提见面…）→ 这里做，确保不漏。
  话术好不好、下一步怎么推 → 交给 AI 读着办，写进报告的「待分析」区。

用法：
  python soul_review.py                  # 复盘今天
  python soul_review.py 2026-09-28       # 复盘指定日期
  python soul_review.py yesterday        # 复盘昨天
  python soul_review.py --json           # 只输出 JSON（给自动化读）

产出：D:\\AI\\pl\\reports\\复盘_YYYY-MM-DD.md
"""
import os, sys, io, json, re, time, sqlite3
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    except Exception:
        pass

try:  # 2026-09-30 双实例：复盘报告与轮次基线都按实例分开（否则实例1 的复盘会覆盖主号当天的）
    from soul_instance import state_path as _sp, vm_index as _vmi
except Exception:
    def _sp(base, name):
        return os.path.join(base, name)

    def _vmi():
        return 0

REPORT_DIR = os.path.join(BASE, "reports") if _vmi() <= 0 else os.path.join(BASE, "reports", "vm%d" % _vmi())
BASE_F = _sp(BASE, ".soul_turn_base.json")


# ============ 轮数基线（2026-09-29 用户定：之前的对话轮次不算） ============
def base_ts():
    """返回轮数起算时间戳(ms)。0 = 不设基线（算全历史）"""
    try:
        with open(BASE_F, encoding="utf-8") as f:
            return int(json.load(f).get("base_ts") or 0)
    except Exception:
        return 0


def base_str():
    t = base_ts()
    return datetime.fromtimestamp(t / 1000).strftime("%Y-%m-%d %H:%M") if t else "（未设，算全历史）"


def set_base(when=None):
    """重设基线。when: None=现在；'today'=今天00:00；'YYYY-MM-DD HH:MM'"""
    if when is None:
        ts = int(time.time() * 1000)
    elif when == "today":
        ts = int(datetime.now().replace(hour=0, minute=0, second=0,
                                        microsecond=0).timestamp() * 1000)
    else:
        ts = int(datetime.strptime(when, "%Y-%m-%d %H:%M").timestamp() * 1000)
    rec = {"base_ts": ts,
           "base_at": datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M:%S"),
           "note": "轮数从此时间点起算；此前的消息仅作历史参考，不计入轮数/阶段判定。"}
    with open(BASE_F, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    return rec


# ============ 终极目标相关：规则库 ============
# ⛔ 方向违规：把"她来找我"变成"我去找她" —— 用户明确的铁律
DIRECTION_BAD = [
    "我去找你", "我来找你", "我过去找你", "我去你那", "我过来找你", "我去看你",
    "我飞过去", "我买票去", "我请假去", "我过去看你", "我去重庆外", "我来见你",
    # ↓ 2026-09-29 复盘补：蓝朋友呀（厦门）用变体绕过词表未被抓到
    #   （"那我得赶在搬之前来"/"机票提前订没那么贵"/"真来我带你吃小面"）
    #   这些话=我买票去她城市，方向已反转，必须能扫出来
    "我得赶在", "机票", "我订票", "订机票", "我买票", "我带你", "我带你去",
    "我来玩", "我去玩", "我过来玩", "我飞你那", "我飞过去找",
]
# ⛔ 我主动邀约（该由她提；我提=降低身位。出现要标注，不一定算错但要看上下文）
I_INVITE = ["见个面", "见一面", "什么时候见", "约一下", "出来吃饭", "一起吃个饭",
            "来找我吧", "你过来", "来重庆玩"]
# ★ 她主动的见面信号（最高价值，出现必须重点跟进）
HER_MEET = ["来重庆", "去重庆", "找你", "见你", "见面", "见一面", "什么时候见",
            "过来玩", "来玩", "过去玩", "过去找你", "我来找你", "我过去"]
# ★ 她问我的个人信息（兴趣信号）
HER_ASK_ME = ["你多大", "你几岁", "你做什么", "你做什么工作", "你是干什么", "你住哪",
              "你在哪", "你单身", "你有对象", "你一个人", "你叫什么", "你家乡"]
# ⚠️ 否定 / 第三方语境标记（2026-09-30 修 HER_MEET 误判用）
#   实测反例「请勿查户口」：她原话是**吐槽别人**——
#     "反正就是各种查户口式的聊天" / "还有一整要照片的 约见面的" /
#     "正常的人不会给陌生人发照片吧"
#   纯关键词匹配会把它标成「她提见面·最高价值信号」，**实际含义完全相反**（她反感硬推）。
#   命中这些标记 → 该条降级为「疑似」，不计入 her_meet，单列给人工核实。
#   ⚠️ 词表故意保守：像「别」「烦」这种 substring 会误伤（特别/告别/麻烦），一律不收。
MEET_SUSPECT = [
    # 否定 / 反感
    "不会", "不想", "不用", "不要", "反感", "讨厌", "拒绝", "恶心",
    "骗子", "骗人", "举报", "算了吧", "怎么可能", "谁要", "无聊",
    # 第三方 / 泛指（不是说的自己和我）
    "别人", "有人", "有些人", "陌生人", "其他人", "那些", "他们", "人家",
    "群里", "遇到过", "碰到过", "一概", "各种", "一整",
]
# ⛔ 敷衍回复（单条就是低价值；连发更糟）
LAZY = ["嗯", "哦", "嗯嗯", "哦哦", "好的", "好", "哈哈", "是吗", "在", "在吗", "然后呢",
        "然后", "所以", "怎么了", "额", "呃", "。。", "……"]
# ⛔ 越界（红线：性暗示/评论外貌 → 会被举报掉权重）
RISKY = ["色", "身材", "性感", "胸", "腿好看", "包养", "约炮", "睡觉", "抱你", "亲",
         "我好想", "欲罢不能"]

MAXLEN = 25          # 单条字数上限（用户明确要求 ≤20 字，留 5 字余量）
TURNS_STAGES = [(0, 30, "初识"), (30, 50, "熟悉"), (50, 100, "推进"), (100, 10**9, "暧昧")]


def stage_of(turns):
    for lo, hi, name in TURNS_STAGES:
        if lo <= turns < hi:
            return name
    return "?"


def _day_range(datestr):
    """返回 [起, 止) 的毫秒时间戳"""
    d0 = datetime.strptime(datestr, "%Y-%m-%d")
    d1 = d0 + timedelta(days=1)
    return int(d0.timestamp() * 1000), int(d1.timestamp() * 1000)


def _resolve_date(arg):
    if not arg or arg == "today":
        return datetime.now().strftime("%Y-%m-%d")
    if arg == "yesterday":
        return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    return arg


def analyze(datestr):
    import soul_im as I
    from soul_db import _load as load_notes

    if not I.pull():
        return {"ok": False, "why": "拉库失败（数据不可信，复盘中止——不要当成'当天没消息'）"}

    names = load_notes()
    lo, hi = _day_range(datestr)
    c = sqlite3.connect(I.IMDB)

    # 会话 → 昵称
    uid2name = I.names()
    sid2name = {}
    for sid, uid in c.execute("SELECT sessionId, toUserId FROM session"):
        nm = uid2name.get(str(uid), "")
        if nm:
            sid2name[sid] = nm

    def nm_of(sid):
        n = sid2name.get(sid) or ""
        if not n:
            for k, v in uid2name.items():
                if k and k in str(sid):
                    return v
        return n

    # 当天全部消息
    rows = c.execute(
        "SELECT sessionId, senderId, text, msgContent, msgType, localTime "
        "FROM chatmsg WHERE localTime>=? AND localTime<? ORDER BY localTime", (lo, hi)
    ).fetchall()

    per = {}          # name -> 统计
    my_bad = []       # 违规/低质
    her_good = []     # 她的积极信号
    for sid, sender, text, content, mtype, lt in rows:
        name = nm_of(sid) or f"(未知{str(sid)[:8]})"
        mine = str(sender) == str(I.ME)
        txt = (text or "").strip()
        # 非文字消息（图/语音/表情）用内容特征判断
        kind = None
        mc = str(content or "")
        if mtype and mtype not in (0, 1):
            if "image" in mc.lower() or "imgUrl" in mc or "picUrl" in mc:
                kind = "图片"
            elif "voice" in mc.lower() or "audioUrl" in mc:
                kind = "语音"
        p = per.setdefault(name, {
            "name": name, "mine": 0, "her": 0, "my_msgs": [], "her_msgs": [],
            "first": lt, "last": lt, "last_who": None, "her_kinds": [],
        })
        p["last"] = lt
        p["last_who"] = "me" if mine else "her"
        if mine:
            p["mine"] += 1
            if txt:
                p["my_msgs"].append((txt, lt))
                for k in DIRECTION_BAD:
                    if k in txt:
                        my_bad.append({"name": name, "type": "方向违规", "sev": "高",
                                       "text": txt, "hit": k, "ts": lt})
                for k in RISKY:
                    if k in txt:
                        my_bad.append({"name": name, "type": "越界词", "sev": "高",
                                       "text": txt, "hit": k, "ts": lt})
                for k in I_INVITE:
                    if k in txt:
                        my_bad.append({"name": name, "type": "我主动邀约", "sev": "中",
                                       "text": txt, "hit": k, "ts": lt})
                if len(txt) > MAXLEN:
                    my_bad.append({"name": name, "type": "超长", "sev": "低",
                                   "text": txt, "hit": f"{len(txt)}字", "ts": lt})
                if txt.strip("。．.!！?？~～ ") in LAZY:
                    my_bad.append({"name": name, "type": "敷衍回复", "sev": "中",
                                   "text": txt, "hit": "低信息量", "ts": lt})
        else:
            p["her"] += 1
            if kind:
                p["her_kinds"].append(kind)
            if txt:
                p["her_msgs"].append((txt, lt))
                # ★ 她的信号扫描**已移到下面「带上下文核验」那一趟**（2026-09-30）
                #   原因：纯关键词匹配会把"她吐槽别人约见面"误判成"她提见面"，
                #   必须回看同会话前后几条才能判。这里只做统计，不做判定。

    # ── 轮数（交替段）统计 —— **从基线起算** ──
    # ⚠️ 口径：轮 = senderId 变化的段数（一来一回算 1 轮），**不是消息条数**。
    #    与 soul_progress.turns_of 保持一致，否则阶段判断会错档。
    # ⚠️ 2026-09-29 用户定：「之前的对话轮次不算」→ 只统计 localTime >= 基线 的消息。
    _b = base_ts()
    hist = {}
    last_who = {}
    _sql = "SELECT sessionId, senderId, text, msgContent, msgType, localTime FROM chatmsg"
    _args = ()
    if _b:
        _sql += " WHERE localTime >= ?"
        _args = (_b,)
    for sid, sender, text, content, mtype, lt in c.execute(_sql, _args):
        n = nm_of(sid)
        if not n:
            continue
        kind = None
        mc = str(content or "")
        if mtype and mtype not in (0, 1):
            if "image" in mc.lower() or "imgUrl" in mc:
                kind = "图片"
            elif "voice" in mc.lower() or "audioUrl" in mc:
                kind = "语音"
        if kind is None and not (text or "").strip():
            continue                      # 空消息不算（避免干扰轮数）
        h = hist.setdefault(n, {"mine": 0, "her": 0, "segs": 0, "last": 0})
        who = "me" if str(sender) == str(I.ME) else "her"
        if last_who.get(n) != who:
            h["segs"] += 1
            last_who[n] = who
        if who == "me":
            h["mine"] += 1
        else:
            h["her"] += 1
        if lt and lt > h["last"]:
            h["last"] = int(lt)

    # ── ★ 她的信号：**带上下文核验**（2026-09-30 大修）──
    # ⚠️ 原实现是纯关键词匹配（`if k in t`），没有否定/第三方语境过滤 →
    #    把「请勿查户口」吐槽别人（"还有一整要照片的 约见面的 / 正常的人不会给陌生人发照片吧"）
    #    标成了「她提见面·最高价值」信号，**实际含义正好相反**（她反感要照片和见面）。
    #    现在：每条命中都回看**同会话窗口**（前 1 条 + 后 2 条，且间隔 ≤10min，防跨天误连），
    #    窗口内出现 MEET_SUSPECT 标记 → 降级为「疑似·需人工核实」，**不计入** her_meet。
    #    命中都会带上 `ctx`（上下文原文），报告里直接给出来，省得再回查。
    seq = c.execute("SELECT sessionId, senderId, text, localTime FROM chatmsg "
                    "WHERE text IS NOT NULL AND text != '' ORDER BY localTime").fetchall()
    by_sid = {}
    for sid, sender, text, lt in seq:
        by_sid.setdefault(sid, []).append((str(sender), str(text), int(lt or 0)))

    funnel = {}       # name -> {"her_meet":bool, "i_bad_dir":bool, ...}
    meet_suspect = []   # 疑似误判（第三方/否定语境），单列给人工核实
    for sid, arr in by_sid.items():
        n = nm_of(sid)
        if not n:
            continue
        f = funnel.setdefault(n, {"her_meet": False, "i_bad_dir": False,
                                  "her_meet_txt": "", "meet_ctx": "", "meet_sus": False})
        for i, (sender, text, lt) in enumerate(arr):
            if sender == str(I.ME):
                if any(k in text for k in DIRECTION_BAD):
                    f["i_bad_dir"] = True
                continue
            hit = next((k for k in HER_MEET if k in text), None)
            if hit:
                ctx = []
                j = i - 1
                if j >= 0 and lt - arr[j][2] <= 600000:
                    ctx.append(arr[j][1])
                j = i + 1
                while j < len(arr) and len(ctx) < 3:
                    if arr[j][2] - lt <= 600000:
                        ctx.append(arr[j][1])
                    j += 1
                window = (text + " ｜ " + " ｜ ".join(ctx)) if ctx else text
                sus = any(m in window for m in MEET_SUSPECT)
                if sus:
                    f["meet_sus"] = True
                    if not f["meet_ctx"]:
                        f["meet_ctx"] = window[:70]
                    if lo <= lt < hi:
                        meet_suspect.append({"name": n, "hit": hit, "text": text,
                                             "ctx": window, "ts": lt})
                else:
                    f["her_meet"] = True
                    if not f["her_meet_txt"]:
                        f["her_meet_txt"] = text[:26]
                        f["meet_ctx"] = window[:70]
                    if lo <= lt < hi:
                        her_good.append({"name": n, "type": "她提见面", "sev": "最高",
                                         "text": text, "hit": hit, "ts": lt, "ctx": window[:70]})
                continue
            if lo <= lt < hi:
                ak = next((k for k in HER_ASK_ME if k in text), None)
                if ak:
                    her_good.append({"name": n, "type": "她问我的事", "sev": "高",
                                     "text": text, "hit": ak, "ts": lt, "ctx": ""})
    c.close()

    # ── ★ 投入度：她**主动打探我**几次（温度必须用它，不能用平均字数）──
    # ⚠️ 2026-09-30 订正：原判据「她均字数 ≥8 = 热」**已被实测证伪** ——
    #    「风止遇你」均 11.2 字看着最热，但她从头到尾只主动问过我 1 次；
    #    写长句只说明**愿意聊那个话题**，不等于**对我这个人有兴趣**。
    #    现在温度 = 她主动问次数（主）+ 均字数（次），并直接标注「够格推进」与否。
    ask_map, rdy_map = {}, {}
    try:
        import soul_stats as _S
        for _sid, _e in _S.engage_map().items():
            _n = sid2name.get(_sid)
            if _n:
                ask_map[_n] = _e
        rdy_map = {u["_name"]: (u, _S.ready(u)) for u in _S.enriched()}
    except Exception as _e:
        print(f"[warn] 投入度指标读取失败（不影响事实区）: {_e!r}", file=sys.stderr)

    # ── 汇总 ──
    items = []
    for name, p in per.items():
        if name.startswith("(未知"):
            continue
        h = hist.get(name, {"mine": 0, "her": 0, "segs": 0})
        turns = h["segs"]                     # ★ 轮 = 交替段，不是消息条数
        st = stage_of(turns)
        rec = names.get(name, {})
        her_txts = [t for t, _ in p["her_msgs"]]
        her_lens = [len(t) for t in her_txts]
        her_avg = round(sum(her_lens) / len(her_lens), 1) if her_lens else 0
        her_lazy = sum(1 for t in her_txts
                       if t.strip("。．.!！?？~～ ") in LAZY or len(t.strip()) <= 2)
        # ★ 全历史投入度（engage_map 是全库口径，不受"当天"限制）
        _e = ask_map.get(name) or {}
        _ask = _e.get("ask", 0)
        _avg_all = _e.get("avg", 0.0)
        _u, _rdy = rdy_map.get(name, (None, (False, [])))
        # 温度判定：先看她问了几次，再看她肯写多长
        _warm = ("—" if p["her"] == 0 else
                 ("热" if (_ask >= 2 and _avg_all >= 6) else
                  ("冷" if (_ask == 0 and (_avg_all <= 4 or
                                           her_lazy >= max(1, len(her_lens) * 0.6))) else "中")))
        items.append({
            "name": name, "today_mine": p["mine"], "today_her": p["her"],
            "turns": turns, "stage": st,
            "stage_mark": rec.get("stage"), "status": rec.get("status", ""),
            "last_who": p["last_who"], "last": p["last"],
            "her_kinds": p["her_kinds"],
            "her_more": p["her"] > p["mine"],
            "her_avg_len": her_avg, "her_lazy": her_lazy,
            "her_ask": _ask, "her_avg_all": _avg_all,
            "ready": bool(_rdy[0]), "not_ready": _rdy[1][:2],
            "her_warm": _warm,
            "my_msgs": p["my_msgs"][-8:], "her_msgs": p["her_msgs"][-8:],
            "her_meet": funnel.get(name, {}).get("her_meet", False),
            "her_meet_txt": funnel.get(name, {}).get("her_meet_txt", ""),
            "meet_ctx": funnel.get(name, {}).get("meet_ctx", ""),
            "meet_sus": funnel.get(name, {}).get("meet_sus", False),
            "i_bad_dir": funnel.get(name, {}).get("i_bad_dir", False),
        })
    items.sort(key=lambda z: (-z["turns"], -(z["today_mine"] + z["today_her"])))

    # ── ★ 话术重复度：我对不同人说过的同一句话（模板化群发检测）──
    # 按标点切片段，统计每个片段(≥4字)覆盖了几个不同对象
    frag_owner = {}
    for p in per.values():
        if p["name"].startswith("(未知"):
            continue
        for txt, _ in p["my_msgs"]:
            for frag in re.split(r"[，,。.！!？?；;、\s~～]+", txt):
                frag = frag.strip()
                if len(frag) < 4:
                    continue
                frag_owner.setdefault(frag, set()).add(p["name"])
    dup = sorted([{"frag": f, "n": len(o), "who": sorted(o)}
                  for f, o in frag_owner.items() if len(o) >= 3],
                 key=lambda z: -z["n"])[:15]

    # ── 天气/模板类高频话题（对多人重复的同一现实信息，最伤真实感）──
    WEATHER = ["下雨", "落雨", "雨停", "凉快", "热不热", "转凉", "降温", "闷"]
    weather_hits = {}
    for p in per.values():
        if p["name"].startswith("(未知"):
            continue
        for txt, _ in p["my_msgs"]:
            if any(w in txt for w in WEATHER):
                weather_hits.setdefault(p["name"], []).append(txt[:30])

    # 停滞对象（基线后有过互动，但**≥24h 没有新消息**）
    # ⚠️ 2026-09-29 基线重置后，原来的「历史轮数≥30」判据会永远为空，改用"断联时长"。
    #    早期间隔自然为空（刚重置没数据），24h 后自动开始生效 —— 这是预期行为。
    today_names = set(per.keys())
    now_ms = int(time.time() * 1000)
    stalled = []
    for nm, h in hist.items():
        if nm in today_names:
            continue
        gap_h = (now_ms - h["last"]) / 3600000.0 if h["last"] else 999
        if gap_h < 24:
            continue
        rec = names.get(nm, {})
        if (rec.get("status") or "") in ("stopped", "skipped", "gift"):
            continue                      # 已放弃/目标不符的不算停滞
        stalled.append({
            "name": nm, "turns": h["segs"], "stage": stage_of(h["segs"]),
            "gap_h": round(gap_h, 1),
            "her_meet": funnel.get(nm, {}).get("her_meet", False),
            "her_meet_txt": funnel.get(nm, {}).get("her_meet_txt", ""),
            "status": rec.get("status", ""),
        })
    stalled.sort(key=lambda z: -z["gap_h"])

    tot_mine = sum(i["today_mine"] for i in items)
    tot_her = sum(i["today_her"] for i in items)

    return {
        "ok": True, "date": datestr,
        "tot_mine": tot_mine, "tot_her": tot_her,
        "people": len(items), "touched": len([i for i in items if i["today_mine"] or i["today_her"]]),
        "items": items, "my_bad": my_bad, "her_good": her_good, "stalled": stalled,
        "dup": dup, "weather": {k: v for k, v in weather_hits.items() if v},
        "cold": [{"name": i["name"], "turns": i["turns"], "avg": i["her_avg_len"],
                  "ask": i["her_ask"], "lazy": i["her_lazy"],
                  "today": f"{i['today_mine']}/{i['today_her']}"}
                 for i in items if i["her_warm"] == "冷" and i["turns"] >= 1],
        "her_meet_people": [i["name"] for i in items if i["her_meet"]],
        "meet_suspect": meet_suspect[:12],
        "ready_people": [{"name": i["name"], "ask": i["her_ask"],
                          "avg": i["her_avg_all"], "turns": i["turns"]}
                         for i in items if i["ready"]],
    }


def _hhmm(ts):
    return datetime.fromtimestamp(ts / 1000).strftime("%H:%M")


def render(r):
    """产出 markdown 报告：事实区（脚本算）+ 待分析区（留给 AI/人）"""
    if not r.get("ok"):
        return f"# 复盘 {r.get('date')}\n\n❌ **{r.get('why')}**\n"
    d = r["date"]
    L = []
    L.append(f"# Soul 聊天复盘 · {d}")
    L.append("")
    L.append(f"> 生成时间 {datetime.now().strftime('%Y-%m-%d %H:%M')}　"
             f"数据源：Soul IM 库（直读，非界面）")
    _bs = base_str()
    L.append(f"> **轮数基线：{_bs}** ← 此前的对话轮次不计（用户 2026-09-29 定）")
    L.append("")

    # ── 总览 ──
    L.append("## 一、当天总览（按**日期**统计，与轮数基线无关）")
    L.append("")
    L.append("| 指标 | 值 |")
    L.append("|---|---|")
    L.append(f"| 我发出 | **{r['tot_mine']}** 条 |")
    L.append(f"| 对方发来 | **{r['tot_her']}** 条 |")
    L.append(f"| 互动过的人 | {r['touched']} 个 |")
    L.append(f"| 收到过见面信号的人 | **{len(r['her_meet_people'])}** 个"
             + (f"（{'、'.join(r['her_meet_people'])}）" if r["her_meet_people"] else "") + " |")
    if r.get("meet_suspect"):
        L.append(f"| ⚠️ 见面信号**疑似误判** | {len(r['meet_suspect'])} 条（第三方/否定语境，见第五节） |")
    _rdy = r.get("ready_people") or []
    L.append(f"| ✅ 够格推进的人 | **{len(_rdy)}** 个"
             + (f"（{'、'.join(x['name'] for x in _rdy)}）" if _rdy else "（空 → 本阶段不向任何人推进）") + " |")
    L.append("")

    # ── 铁律检查 ──
    L.append("## 二、铁律检查（自动扫描）")
    L.append("")
    if r["my_bad"]:
        high = [b for b in r["my_bad"] if b["sev"] == "高"]
        if high:
            L.append("### ⛔ 高危（必须改）")
            L.append("")
            L.append("| 对象 | 类型 | 命中 | 原话 | 时间 |")
            L.append("|---|---|---|---|---|")
            for b in high:
                L.append(f"| {b['name'][:12]} | **{b['type']}** | `{b['hit']}` | {b['text'][:24]} | {_hhmm(b['ts'])} |")
            L.append("")
        mid = [b for b in r["my_bad"] if b["sev"] == "中"]
        if mid:
            L.append("### ⚠️ 中危（看上下文）")
            L.append("")
            L.append("| 对象 | 类型 | 命中 | 原话 | 时间 |")
            L.append("|---|---|---|---|---|")
            for b in mid[:20]:
                L.append(f"| {b['name'][:12]} | {b['type']} | `{b['hit']}` | {b['text'][:24]} | {_hhmm(b['ts'])} |")
            L.append("")
        low = [b for b in r["my_bad"] if b["sev"] == "低"]
        if low:
            L.append(f"### 超长消息（> {MAXLEN} 字）共 {len(low)} 条")
            L.append("")
            for b in low[:10]:
                L.append(f"- {b['name'][:12]}（{b['hit']}）：{b['text'][:40]}")
            L.append("")
    else:
        L.append("✅ **未发现违规**（无方向违规 / 越界词 / 敷衍回复 / 超长）")
        L.append("")

    # ── 话术重复度 ──
    if r.get("dup"):
        L.append("## 三、话术重复度（模板化群发检测）")
        L.append("")
        L.append("> 同一句话对 **3 个以上对象**说过 → 会被感觉成群发，真实感归零。这是最伤的一类问题。")
        L.append("")
        L.append("| 重复片段 | 对几人说过 | 对象 |")
        L.append("|---|---|---|")
        for u in r["dup"]:
            L.append(f"| `{u['frag'][:20]}` | **{u['n']}** | {'、'.join(x[:8] for x in u['who'][:6])} |")
        L.append("")
    if r.get("weather"):
        L.append("### 「天气」类话题重复（对多人说同一现实信息）")
        L.append("")
        for who, txts in r["weather"].items():
            L.append(f"- **{who[:12]}**：{' / '.join(txts)}")
        L.append("")

    # ── 她的回复质量 ──
    if r.get("cold"):
        L.append("## 四、热度下滑 ⚠️（她在敷衍 —— 别按轮数推）")
        L.append("")
        L.append("> ⚠️ **判据已订正（2026-09-30）**：旧版用「她平均字数」，**已证伪** ——")
        L.append("> 「风止遇你」均 11.2 字看着最热，实际只主动问过我 1 次。")
        L.append("> 现在主判据是 **「她主动问过我几次」**（= 对**我这个人**有没有兴趣），均字数只作参考。")
        L.append("")
        L.append("| 轮数 | 昵称 | 她主动问 | 她均字数 | 敷衍条数 | 今天我/她 |")
        L.append("|---|---|---|---|---|---|")
        for x in r["cold"]:
            L.append(f"| {x['turns']} | {x['name'][:12]} | **{x.get('ask', 0)}** | {x['avg']} | "
                     f"{x['lazy']} | {x['today']} |")
        L.append("")
        L.append("→ 这些对象**不要继续按轮数推暧昧**（她对我这个人零打探），要么换话题重燃，要么降温。")
        L.append("")

    # ── 她的信号 ──
    L.append("## 五、她发来的积极信号")
    L.append("")
    if r["her_good"]:
        L.append("> 每条都带**上下文原文**（`原话 ｜ 前后文`）—— 请自己读一遍再下结论，别只看命中的那半句。")
        L.append("")
        L.append("| 对象 | 信号 | 命中 | 上下文原文 | 时间 |")
        L.append("|---|---|---|---|---|")
        for g in r["her_good"][:20]:
            mark = "★" if g["sev"] == "最高" else ""
            L.append(f"| {g['name'][:12]} | {mark}{g['type']} | `{g['hit']}` | "
                     f"{(g.get('ctx') or g['text'])[:56]} | {_hhmm(g['ts'])} |")
        L.append("")
    else:
        L.append("（今天没抓到明显信号）")
        L.append("")
    if r.get("meet_suspect"):
        L.append("### ⚠️ 见面信号·**疑似误判**（第三方/否定语境，已从上方剔除）")
        L.append("")
        L.append("> 这些命中落在了 MEET_SUSPECT 词上（别人/有人/不会/反感…）。**不要当成邀约**——")
        L.append("> 实测这类多半是她在**吐槽别人**，含义相反。要跟就按「她反感什么」来跟，别当邀约。")
        L.append("")
        L.append("| 对象 | 命中 | 上下文原文 | 时间 |")
        L.append("|---|---|---|---|")
        for g in r["meet_suspect"]:
            L.append(f"| {g['name'][:12]} | `{g['hit']}` | {g['ctx'][:56]} | {_hhmm(g['ts'])} |")
        L.append("")

    # ── 重点对象 ──
    L.append("## 六、对象进度（**新计数**，基线后有互动的都列出）")
    L.append("")
    L.append("> `她问` = 全历史她**主动打探我**的次数（投入度主指标）；`均字` 仅供参考（已证伪为温度判据）。")
    L.append("> `够格` = 是否达到推进门槛（她问 ≥2 · 均字 ≥6 · 她发 ≥20 · 72h 内有互动），"
             "与每轮开局 `soul_auto.py` 同一口径。")
    L.append("")
    L.append("| 轮 | 昵称 | 今我/她 | 她问 | 均字 | 够格 | 阶段 | 见面信号 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for it in r["items"]:
        if it["turns"] < 1 and (it["today_mine"] + it["today_her"]) == 0:
            continue
        meet = ("★她提过" if it["her_meet"] else
                ("⚠️疑似" if it.get("meet_sus") else
                 ("⛔我说过'我去'" if it["i_bad_dir"] else "")))
        rd = "✅" if it.get("ready") else ""
        tag = it["name"][:14]
        if it["status"] in ("stopped", "skipped", "gift"):
            tag += f" `{it['status']}`"
        L.append(f"| {it['turns']} | {tag} | {it['today_mine']}/{it['today_her']} | "
                 f"{it.get('her_ask', 0)} | {it['her_avg_len']} | {rd} | {it['stage']} | {meet} |")
    L.append("")

    # ── 停滞 ──
    if r["stalled"]:
        L.append("## 七、停滞对象（基线后互动过，但 ≥24h 无新消息）")
        L.append("")
        L.append("| 断联 | 昵称 | 新计数轮 | 阶段 | 见面信号 |")
        L.append("|---|---|---|---|---|")
        for it in r["stalled"]:
            gap = f"{it.get('gap_h', 0):.0f}h" if it.get("gap_h", 0) < 999 else "—"
            L.append(f"| {gap} | {it['name'][:14]} | {it['turns']} | {it['stage']} | "
                     f"{(it['her_meet_txt'] or '')[:20]} |")
        L.append("")
        L.append("> 注：基线刚重置，前期本表会偏空；24h 后才开始反映真实断联。")
        L.append("")

    # ── 待分析 ──
    L.append("## 八、待分析（话术与推进，需读对话后判断）")
    L.append("")
    L.append("> ① 有没有接住她的情绪 ② 是不是我在单方面输出 ③ 该不该推、怎么推 ④ 有没有拖太久该收")
    L.append("> **分两档**（2026-09-30）：今天消息 ≥6 条的给完整原文；1~5 条轻触的压成一行，")
    L.append("> 避免几十个人的短句把版面撑满（旧版这里占全报告 64%）。")
    L.append("")
    _full = [it for it in r["items"] if it["today_mine"] + it["today_her"] >= 6]
    _lite = [it for it in r["items"] if 0 < it["today_mine"] + it["today_her"] < 6]
    for it in _full:
        _kd = ""
        if it["her_kinds"]:
            _kc = {}
            for k in it["her_kinds"]:
                _kc[k] = _kc.get(k, 0) + 1
            _kd = " · 收到 " + " ".join(f"{k}×{v}" for k, v in _kc.items())
        _rd = " · ✅够格推进" if it.get("ready") else ""
        L.append(f"### {it['name']}（{it['turns']}轮 · {it['stage']} · 今天 {it['today_mine']}/{it['today_her']}"
                 f" · 她主动问{it.get('her_ask', 0)}次{_rd}{_kd}）")
        L.append("")
        merged = sorted([(t, "我", ts) for t, ts in it["my_msgs"]] +
                        [(t, "她", ts) for t, ts in it["her_msgs"]], key=lambda z: z[2])
        for txt, who, ts in merged[-10:]:
            L.append(f"- `{_hhmm(ts)}` **{who}**：{txt}")
        L.append("")
    if _lite:
        L.append(f"### 轻触（今天 1~5 条，共 {len(_lite)} 人 —— 多数是首批搭讪/收尾，逐条看价值低）")
        L.append("")
        for it in _lite:
            merged = sorted([(t, "我", ts) for t, ts in it["my_msgs"]] +
                            [(t, "她", ts) for t, ts in it["her_msgs"]], key=lambda z: z[2])
            tail = " ｜ ".join(f"{w}:{t[:16]}" for t, w, _ in merged[-2:])
            L.append(f"- **{it['name'][:14]}**（{it['turns']}轮·今{it['today_mine']}/{it['today_her']}"
                     f"·她问{it.get('her_ask', 0)}）：{tail}")
        L.append("")

    L.append("---")
    L.append("")
    L.append("## 九、结论区（由分析填充）")
    L.append("")
    L.append("### 是否在按终极目标推进")
    L.append("")
    L.append("_（填：方向对不对、有没有人接近'她主动要来'、有没有人卡死该放弃）_")
    L.append("")
    L.append("### 话术需要调整的地方")
    L.append("")
    L.append("_（填：哪类话该少说、哪类该多说，配具体例子）_")
    L.append("")
    L.append("### 明天重点")
    L.append("")
    L.append("_（填：3 个以内具体动作）_")
    L.append("")
    return "\n".join(L)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    as_json = "--json" in sys.argv

    if "--reset-base" in sys.argv:
        when = args[0] if args else None          # 空=现在；today=今天0点；"YYYY-MM-DD HH:MM"
        print(json.dumps(set_base(when), ensure_ascii=False, indent=1))
        return
    if "--base" in sys.argv:
        print(f"轮数基线: {base_str()}  ({base_ts()})")
        return

    datestr = _resolve_date(args[0] if args else None)

    r = analyze(datestr)
    os.makedirs(REPORT_DIR, exist_ok=True)
    path = os.path.join(REPORT_DIR, f"复盘_{datestr}.md")
    md = render(r)
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)

    if as_json:
        slim = {k: v for k, v in r.items() if k != "items"}
        slim["items"] = [{kk: vv for kk, vv in it.items() if kk not in ("my_msgs", "her_msgs")}
                         for it in r.get("items", [])][:25]
        print(json.dumps(slim, ensure_ascii=False, indent=1))
    else:
        print(md)
        print(f"\n>>> 报告已写入: {path}")


if __name__ == "__main__":
    main()
