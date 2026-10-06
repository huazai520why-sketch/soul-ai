# -*- coding: utf-8 -*-
"""主动匹配 —— **第 3 优先级**，且**随时可被新消息抢占**

用户流程（2026-09-29 定，原话）：
  「按之前雷电的流程跑一遍 soul 聊天：已读未回、未读 → 完成了之后没有新消息就去匹配 3-5 个
    继续聊 → 还是没人回复就去评论广场十分钟 → 还是没有新消息再去匹配。
    **所有操作建立在没有新消息的情况下，有新消息优先处理，仅次于奇遇铃**」
  「**匹配过程中、广场评论过程中有新消息来，也要优先回复**」

→ 所以本模块是**抢占式**的：每个"原子步骤"之间都有检查点，
  一旦发现待回消息，**立刻停止匹配**并返回 `"pending"`，把控制权交回上层去回复。
  （不是"每轮检查一次"就完事 —— 那样匹配中的 20~40 秒里来的消息会被漏掉）

实测要点：
  · 匹配会话页**有「匹配下一个」按钮**(约 228,105)，连续匹配不必绕回星球页
  · 「灵魂匹配」入口在星球页 (120,298)；「开始匹配」(122,462)
  · 匹配到人后界面 = 普通会话页（匹配度/星座/礼仪分 + 输入框），可直接发开场白
  · **昵称读法**：会话页 y<200 那行（排除"关注/查看主页/在线"等），
    实测 OCR 会把它读成"点正在线"，实际昵称是"点正" —— 所以要做**尾部状态词截断**

用法：
  python soul_match.py 3            # 匹配 3 个，每个发一句开场白
  python soul_match.py 5 --dry      # 只匹配不发（调试）
"""
import sys, io, os, re, time, random

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
# ⚠️ 模块级**不要**包装 sys.stdout：被 import 时会二次包装并关掉原 stdout
#    （ValueError: I/O operation on closed file）。只在 __main__ 里包。

import soul
import soul_read as rd

NEXT_BTN = "匹配下一个"
MATCH_ENTRY = ("灵魂匹配", "开始匹配")
# ⭐ 2026-10-06：Soul 新版匹配成功落在**匹配卡片页**（共同点/引力签/星球/打招呼），
#   老特征词全 miss → match_once 误判 no_match → 匹配成功的人没发开场白被 ghost。
#   并入卡片页高频词（刻意避开「聊天」单词等泛词，防误判别的页面）。
SESSION_HINT = ("共同点", "引力签", "打招呼", "匹配度", "查看主页",
                "交换答案", "文明聊天", "礼仪分", "Ta的", "发消息")

# ⭐ 2026-10-06（用户：「匹配的时候 OCR 分析一下剩余次数」）
#   to_planet() 成功时**就地**留一份「星球页那一帧」的 OCR 结果，
#   上层要读「今日剩余 N 次」就直接复用，不必再截一次图、再识别一次。
_PLANET_LAST = {"items": None, "ts": 0.0}


def planet_quota(max_age=180.0):
    """上次 to_planet() 成功那帧的配额 → (灵魂剩余, 语音剩余, 用完弹层在不在)。

    sq/vq 为 None = 读不到 = **未知**，调用方**不可**当 0。
    没进过星球页 / 缓存过期 → 现读当前屏（⚠️ 此刻可能不在星球页）。
    """
    try:
        if _PLANET_LAST["items"] is not None and (time.time() - _PLANET_LAST["ts"]) <= max_age:
            return soul.soul_quota_left(_PLANET_LAST["items"])
    except Exception:
        pass
    return soul.soul_quota_left(None)

# ⛔ 2026-09-29 用户点名：「模板化」是最高级别的失败（"绝不模板化 —— 最重要"）。
#    旧实现是一个固定池 —— 实测把**同一句**「这个点还没睡呢」发给了「Un」和「离异带男娃」两个人，
#    正是复盘点名的群发模式。**池子已删除**。
#    现在：从匹配会话页 OCR 抓**她自己的**特征（引力签 / 共同点）拼开场白；
#    抓不到特征就**不发**（返回 None，宁可少发一个，也不发一句"换个人也成立"的话）。
SIGNS = ("白羊座", "金牛座", "双子座", "巨蟹座", "狮子座", "处女座",
         "天秤座", "天蝎座", "射手座", "摩羯座", "水瓶座", "双鱼座")
CITY_HINT = ("北京", "上海", "天津", "重庆", "广州", "深圳", "成都", "杭州", "武汉",
             "西安", "南京", "苏州", "长沙", "郑州", "青岛", "厦门", "福州", "昆明",
             "贵阳", "合肥", "济南", "沈阳", "哈尔滨", "长春", "大连", "南昌", "南宁",
             "海口", "三亚", "拉萨", "银川", "西宁", "兰州", "太原", "石家庄", "呼和浩特",
             "乌鲁木齐", "宁波", "无锡", "温州", "东莞", "佛山", "泉州", "珠海", "同城")


def _split_multi(s):
    return [x.strip() for x in re.split(r"[、,，/|\s]+", str(s or "")) if x.strip()]


def _stable_idx(s, n):
    """字符串 → 稳定的 [0,n) 下标（不用内置 hash：它每进程随机化，会导致同标签不同句式）"""
    return sum(ord(ch) * (i + 1) for i, ch in enumerate(s or "")) % max(1, n)


# 句式跨人查重窗口（天）。用户铁律：同一句式发给两个人 = 模板化 = 最高级别失败。
FRAME_DEDUP_DAYS = 3
_FRAME_CACHE = None


def on_ready_list(name):
    """她是否在 `soul_stats.ready_list()`（= soul_auto 输出的「✅可推进」）名单内。

    ⛔ 2026-09-30 **推进门槛硬闸**：地域锚点（重庆）**只对名单内的人用**，
       名单外给了是白费（对方没到推进阶段，抛地域只会显得自来熟）。
       读不到名单时**保守返回 False**（宁可不用锚点，不可越界）。
    """
    want = str(name or "").strip()
    if not want or want == "?":
        return False
    try:
        import soul_stats as S
        for u in S.ready_list():
            n = str(u.get("_name") or "").strip()
            if n and (n == want or want in n or n in want):
                return True
    except Exception as e:
        print(f"  !! 可推进名单读取失败（保守：不给地域锚点）: {e!r}")
        return False
    return False


def _recent_my_texts(days=3):
    """近 N 天**我发的**真人文本（跳过系统卡片 JSON）。进程内缓存一次。"""
    global _FRAME_CACHE
    if _FRAME_CACHE is not None:
        return _FRAME_CACHE
    out = []
    try:
        import sqlite3, time as _t
        import soul_im as I
        I.pull()
        c = sqlite3.connect(I.IMDB)
        cut = int(_t.time() * 1000) - days * 86400 * 1000
        for (t, mc) in c.execute(
                "SELECT text, msgContent FROM chatmsg WHERE senderId=? AND localTime>?",
                (str(I.ME), cut)):
            s = str(t or mc or "").strip()
            if s.startswith("{"):
                continue
            out.append(s)
        c.close()
    except Exception as e:
        print(f"  !! 近期发送查重读库失败（保守：跳过查重）: {e!r}")
    _FRAME_CACHE = out
    return out


def frame_used_recently(frame, days=FRAME_DEDUP_DAYS):
    """句式骨架在最近 N 天是否已用过（`{}` 当通配符）。

    ⛔ 2026-09-30 实测病灶：`{} 一看就是玩明白的人了` 在 02:00 发给了「季忆」、
       03:18 又发给了「紫色的麦苗」——**同一句式跨人复用 = 模板化**，
       是用户点名的头号失败（「绝不模板化 —— 最重要」）。
       原实现只用 `_stable_idx(seed)` 在句池里挑，池子小 ⇒ 跨人必然撞车。
    """
    try:
        head, tail = (frame.split("{}") + [""])[0], (frame.split("{}") + [""])[1]
        pat = re.compile(re.escape(head) + r".{0,10}" + re.escape(tail))
        for s in _recent_my_texts(days):
            if pat.search(s or ""):
                return True
    except Exception as e:
        print(f"  !! 句式查重异常（放行）: {e!r}")
        return False
    return False


def pick_frame(frames, seed_key, fmt):
    """从句池里挑一个**近 N 天没用过**的句式渲染。全用过 → None。"""
    n = len(frames)
    start = _stable_idx(seed_key, n)
    for off in range(n):
        fr = frames[(start + off) % n]
        if frame_used_recently(fr):
            continue
        text = fr.format(fmt)
        # ⛔ 2026-09-30 04:4x 实测病灶：`_recent_my_texts()` 在**进程内只查一次库就缓存**，
        #    而一轮匹配 3 个人跑在同一个进程里 → 第 2 个人看到的还是旧快照，
        #    于是「看你签上写{} 有意思」04:39 发给「0星辰」、04:40 又发给「娜依」
        #    —— 同一句式跨人复用 = 用户点名的头号失败（绝不模板化）。
        #    修法：选中后立刻把渲染文本塞回缓存，本进程内后续 pick 立即视为已用。
        try:
            _recent_my_texts().append(text)
        except Exception:
            pass
        return text
    print(f"  ⚠️ 句池里 {n} 个句式近 {FRAME_DEDUP_DAYS} 天都用过 → 不发（防模板化）")
    return None


def profile_opening(allow_city=False):
    """从当前匹配会话页抓她的特征，拼一句**只对她成立**的开场白；抓不到返回 None。

    页面上的可用素材（实测 OCR 原文）：
      「Ta的引力签：露营野餐、逛街shopping、美食聚餐」
      「你们的共同点：重庆、白羊座」

    allow_city: 只有「✅可推进」名单内的人才许用**地域锚点**（见 on_ready_list）。
    """
    try:
        import soul_read as rd
    except Exception as e:
        print(f"  !! 读屏失败，无法取特征: {e!r}")
        return None
    tags, common = [], []
    # ⭐ 2026-09-30 16:2x 修：引力签**会跨行**（长标签换行渲染），旧的逐行正则只拿到前半截
    #   （实测抓到「永远在」这种断句 → 拼出病句；更糟的是整行漏检 → tags=[] → 连续多个匹配"不发"）。
    #   现在：命中「引力签」后，把下面 y 邻近且**未遇到「星座/共同点」标签**的行拼进来，再切词。
    ys = [(t or "", cy) for t, _, cy in rd.items()]
    SKIP_JOIN = ("星座", "共同点", "匹配度", "查看主页", "在线", "关注", "引力签")
    for i, (t, cy) in enumerate(ys):
        m = re.search(r"引力签[：:]\s*(.+)", t)
        if not m:
            continue
        frag, base = m.group(1), cy
        for t2, cy2 in ys[i + 1:]:
            if any(k in t2 for k in SKIP_JOIN) or cy2 - base > 120:
                break
            if len(t2.strip()) >= 2:
                frag += "，" + t2.strip()
                base = cy2
        tags += _split_multi(frag)
    for t in ys:
        t = t[0]
        m = re.search(r"共同点[：:]\s*(.+)", t)
        if m:
            common += _split_multi(m.group(1))
    # ① 共同点里的**城市**（最强：同城自带理由，不牵强）
    #    ⚠️ 2026-09-29 修：原来把"引力签第一个"排在最前，结果抓到「传媒从业者」（职业标签），
    #    拼出「传媒从业者 这块你在行」—— 别扭、像面试。城市优先才对。
    #    ⚠️ 2026-10-05 修「空匹配」（用户拍板）：名单外的人共同点常有城市却直接跳过
    #    → 匹配到却发不出开场白。现在名单内用同城直球（CITY_FRAMES），
    #    名单外的城市/星座/意图类共同点走 ③ 的「共同点提及」句式，不再闲置。
    CITY_FRAMES = (
        "都在{} 还不认识一下",
        "{}的啊 那算半个邻居了",
        "同城{} 巧了",
        "{}的 隔得也不远嘛",
        "{}老乡 握个手",
        "你在{}的话 那约饭方便了",
    )
    if allow_city:
        for c in common:
            if c and not c.endswith("座") and len(c) <= 4:
                got = pick_frame(CITY_FRAMES, c, c)
                if got:
                    return got
    # ② 再用引力签里的**兴趣**（跳过职业/身份类标签；标签太长或太抽象就不用，免得拼出病句）
    BAD_TAG = ("从业", "创业", "职业", "工作", "老板", "员工", "自由职业",
               "学生", "打工人", "上班",
               # 2026-09-30 16:2x：匹配到「艳艳」，引力签首标签是**职业**「销售」
               #   → 拼出「销售 你一般什么时候玩」她只能回「不玩」。职业类一律不进开场白。
               "销售", "客服", "教师", "老师", "护士", "医生", "会计", "设计",
               "运营", "程序", "码农", "金融", "跑腿", "外卖", "司机")
    # ⭐ 2026-09-30：跨行拼接仍可能留下**断句残片**（如「永远在」「看完置」）→ 拼出来是病句。
    #   判据：以虚词/连词结尾的短标签一律丢（正常引力签不会以这些字收尾）。
    CUT_TAIL = ("在", "的", "和", "与", "或", "及", "是", "了", "着", "很", "都", "也")
    # ⭐ 2026-09-29 修「模板化」：原来只有一句固定后缀 `f"{tg} 你还常玩吗"`，
    #    实测三个匹配对象收到**同一句式**（美食 你还常玩吗 / 声控 你还常玩吗 / ktv歌王 你还常玩吗）
    #    → 正是用户点名禁止的"模板化"。现在按标签字符串选不同句式（同一标签恒定，跨人必不同）。
    TAG_FRAMES = (
        "{} 你是真爱这个",
        "看你签上写{} 有意思",
        "{} 这块你肯定有故事",
        "{} 这种爱好不容易坚持吧",
        "你也玩{}啊 那有的聊",
        "{} 一看就是玩明白的人了",
        # 2026-09-30 04:4x：6 句里 5 句在 3 天窗口内已用 → 连续匹配直接"不发"。
        # 扩充句池（每条都必须能套任意标签、≤20 字、不是替她下结论）。
        "{} 我倒是没怎么接触过",
        "{} 你是多久开始的",
        "{} 听着就挺花时间",
        "{} 这个我只能旁观",
        "{} 我猜你在上头没少花钱",
        "{} 你玩这个一般跟谁",
        # 2026-09-30 16:2x：一轮里连匹配 17 个，「12 句全在 3 天窗口内」→ 连续 5 个无法发。
        # 再扩 10 句（同样要求：套任意标签成立、≤20 字、不替她下结论、不丢球）。
        "{} 你一般什么时候玩",
        "{} 是怎么入坑的",
        "{} 我身边没人玩这个",
        "{} 看着挺上头",
        "{} 我完全不懂这个",
        "{} 感觉你有的聊",
        "{} 我好奇挺久了",
        "{} 这爱好挺费心思",
        "{} 你玩多久了",
        "{} 听着就挺有意思",
        # 2026-10-05（用户拍板「要改不然就是空匹配」）：句池 22→40。
        # 连续匹配时 3 天去重窗口耗尽 = 连续空匹配的主因。新增 18 句（同样 ≤20 字、
        # 套任意标签成立、不替她下结论、不丢球）。
        "{} 这块你肯定没少花心思",
        "{} 你玩得挺明白",
        "{} 听着像你的主场",
        "{} 这事你是怎么入的门",
        "{} 我也一直想试试",
        "{} 你平时跟谁一起玩",
        "{} 这爱好听起来费电",
        "{} 你时间都花这上面了",
        "{} 感觉你会的东西挺杂",
        "{} 你是专门练过吧",
        "{} 这我可接不住话",
        "{} 你聊这个我不困",
        "{} 看出来你是认真的",
        "{} 这个可以展开讲讲",
        "{} 我最近正想了解",
        "{} 你这爱好挺稀有",
        "{} 玩这个的一般都懂行",
        "{} 你有空给讲讲呗",
    )
    for tg in tags:
        if any(b in tg for b in BAD_TAG):
            continue
        if tg.endswith(CUT_TAIL) and len(tg) <= 6:
            continue                    # 跨行截断的残片
        if 1 < len(tg) <= 7:            # ⚠️ 2026-10-05：6→7 字（救回「每年至少一次旅行」这类 7 字兴趣）
            # 加共同点一起参与选句式 → 降低"同标签撞同一句"的概率
            seed = tg + "|" + ",".join(common)
            got = pick_frame(TAG_FRAMES, seed, tg)
            if got:
                return got
    # ③ 共同点素材（⭐ 2026-10-05 新增：名单外的城市/星座/意图类共同点不再闲置）
    #    原逻辑只有 allow_city 名单内才用城市，名单外直接跳过 → 共同点常被浪费。
    #    现在任何 ≤7 字共同点词都能套「共同点提及」句（比同城直球克制，不算越界）。
    COMMON_FRAMES = (
        "共同点写着{} 巧了",
        "{} 这点咱俩想到一块了",
        "看共同点带{} 有点意思",
        "{} 同频的人难得",
        "都有{} 那聊得来",
        "共同点里有{} 感觉是同类人",
        "{} 这个共同点挺少见",
        "{} 咱俩节奏一样",
    )
    for c in common:
        if not c or len(c) > 7:
            continue
        if c in tags:
            continue                    # 已在②用过该词 → 不重复用共同点句式
        if allow_city and not c.endswith("座") and len(c) <= 4:
            continue                    # 名单内城市词已走①（失败=句式用尽，不硬凑）
        got = pick_frame(COMMON_FRAMES, c, c)
        if got:
            return got
    # ④ 只剩职业/超长标签、且共同点也无可用词 → 个性化素材耗尽。
    #    ⚠️ 2026-10-05 用户铁律：匹配到人**必须发**（哪怕"你好"）→ 交给 fallback_opening 兜底。
    print(f"  ⚠️ 抓不到她的个性化特征（引力签={tags} 共同点={common}）→ 走兜底基础开场白")
    return None


# ==================== 匹配兜底开场白（用户铁律） ====================
# ⭐ 2026-10-05 用户拍板：**只要匹配到人就必须发消息**，哪怕"你好"也行——**仅限匹配场景**。
# 待回回复场景不受影响（soul_reply 有自己的话术池，不走这里）。
# 兜底池刻意朴素（不替她下结论、不丢球、不油腻）；仍走 3 天去重，避免连续多人收到同一句；
# 若 8 句 3 天内全用过 → **强制发稳定索引那句**（用户铁律优先于去重，绝不留空匹配）。
# ⭐ 2026-10-06 用户「爽感铁律」：兜底开场白不能是「你好呀」这种干巴巴招呼。
#   每条都要让她读起来**爽**（有画面／有笑点／有来回钩子／有落差，至少命中 2 个），
#   且刻意用**身份中立**措辞（不带城市/方言/职业）——本文件不走 soul_persona.rewrite，
#   带「重庆」类字样会漏到账号2（东北人设）。方言味交给 LLM 主通道去带。
FALLBACK_OPENINGS = (
    "刚下班 一身的班味儿",
    "刷到你这下 我瞌睡都醒了",
    "我这人嘴笨 但不会让你冷场",
    "先说好 合不合得来聊了才知道",
    "你也在刷这个 咱俩挺闲啊",
    "刚吃完 撑得不想动 来说两句",
    "难得主动一回 你看着办",
    "打个招呼 后面靠聊出来的",
)


def fallback_opening(name):
    n = len(FALLBACK_OPENINGS)
    start = _stable_idx(name or "?", n)
    for off in range(n):
        fr = FALLBACK_OPENINGS[(start + off) % n]
        if frame_used_recently(fr):
            continue
        try:
            _recent_my_texts().append(fr)
        except Exception:
            pass
        return fr
    forced = FALLBACK_OPENINGS[start % n]
    try:
        _recent_my_texts().append(forced)
    except Exception:
        pass
    return forced


# ==================== 抢占式检查点 ====================
_FORCE = False  # --force：跳过待回检查（被不可达对象卡死时手动用，勿常态开）


def pending_now():
    """当前待回消息（未读 + 已读未回）。用 soul_im 统一口径。"""
    try:
        import soul_im as I
        I.pull()
        return I.pending() or []
    except Exception as e:
        print(f"  !! 检查新消息失败（保守：当作无消息继续）: {e!r}")
        return []


def _reachable(name):
    """昵称是否可达：含 **CJK 基本区** 或 **ASCII 字母数字** 才能被 OCR/搜索命中。
    全 emoji / 全空白特殊字符（如 ￴￴￴、𓆡𓆝𓆟）→ soul_reply/搜索都找不到 →
    留在待回里会把匹配永远卡在 pending → 不算阻断（2026-10-01 实测教训）。
    ⚠️ 不能用 ch.isalnum()：Unicode 把 𓆡 这类古文字也算 letter → 会误判 reachable。"""
    for ch in (name or ""):
        if '一' <= ch <= '鿿':
            return True
        if ch.isascii() and ch.isalnum():
            return True
    return False


def interrupted():
    """有新消息吗？返回 (True, 列表) / (False, [])。
    --force 或 SOUL_MATCH_FORCE → 跳过待回检查；不可达昵称自动忽略（不算阻断）。"""
    if _FORCE or os.environ.get("SOUL_MATCH_FORCE"):
        return (False, [])
    rows = pending_now()
    blocked = [r for r in rows if not _reachable(r[1])]
    reach = [r for r in rows if _reachable(r[1])]
    if blocked:
        print("  ⓘ 忽略 %d 个不可达待回（全emoji/特殊昵称）: %s"
              % (len(blocked), ", ".join(repr(r[1])[:14] for r in blocked)))
    if reach:
        print(f"  ⚡ 检查点发现 {len(reach)} 个待回 → 中断当前操作，优先回消息")
    return (bool(reach), reach)


# ==================== 导航 ====================
def to_planet():
    """回到星球页（会话页→返回→星球tab）"""
    for _ in range(4):
        a = soul.activity()
        if "Conversation" in a:
            soul.tap(*soul.BACK_XY)
            time.sleep(2.2)
            continue
        # ⭐ 2026-10-06 新增：**非主框架的 soul 子页也必须返回**。
        #   实测 2026-10-06 05:00 用户报「上次就是这个页面 不动了」→ 截图实锤卡在
        #   「今日匹配Souler」记录页（**没有底导航**，只有一行日期）。
        #   它的 Activity 不是 Conversation → 旧逻辑直接 break → 后面 OCR 找不到
        #   「星球」标签、兜底 tap 坐标也无效（页面上根本没有底导航可点）
        #   → 连续 no_planet，匹配空转、也到不了唤醒。
        #   修法：只要是 soulapp 的 Activity 但**不是 MainActivity**（= 还在子页里），
        #   就按返回退一级；退 4 次仍不行，后面还有 am start 重启兜底。
        #   风险评估：在 soulapp 内按返回最多退到桌面 → 下轮 ensure_foreground() 会拉回。
        # ⭐ 2026-10-06 再修：上面这条用裸的 `"soulapp" in a` **漏掉了 RN 页面** ——
        #   实测 14:11 卡在「搜索韩梦慈的结果列表」页，activity 是
        #   `cn.soul.android.soul_rn_sdk.multiengine.RnContainerActivity`（**不含 soulapp**）
        #   → 两个分支都不命中 → 直接 break → 后面 OCR 找不到「星球」/底导航 → 死循环。
        #   soul.py 里早就有 `_is_soul_activity()`（判包名前缀 `cn.soul`，两种都覆盖），
        #   注释里甚至专门写过这个坑 —— 这里必须调它，别再手写子串判断。
        if soul._is_soul_activity(a) and "MainActivity" not in a:
            soul.tap(*soul.BACK_XY)
            time.sleep(2.2)
            continue
        if not soul._is_soul_activity(a):
            soul.ensure_foreground()
            continue
        break
    items = rd.items()
    if any(any(k in t for k in MATCH_ENTRY) for t, _, _ in items):
        _PLANET_LAST["items"], _PLANET_LAST["ts"] = items, time.time()
        return True
    hit = next(((cx, cy) for t, cx, cy in items if t.strip() == "星球" and cy > 1150), None)
    if not hit:
        soul.adb("shell", "am", "start", "-n",
                 "cn.soulapp.android/.component.startup.main.MainActivity")
        time.sleep(3.5)
        items = rd.items()
        hit = next(((cx, cy) for t, cx, cy in items if t.strip() == "星球" and cy > 1150), None)
    if not hit:
        # ⚠️ 2026-09-30：底导航「星球」标签**经常不被 OCR 检出**（实测 03:15 连续 3 轮 no_planet，
        #    而截图里星球 tab 明明在）→ 用已知底导航坐标兜底（soul.TAB_PLANET, 720x1280）。
        print("  · OCR 找不到「星球」标签 → 用底导航坐标兜底")
        soul.tap(*soul.TAB_PLANET)
        time.sleep(3.5)
        items = rd.items()
        if any(any(k in t for k in MATCH_ENTRY) for t, _, _ in items):
            _PLANET_LAST["items"], _PLANET_LAST["ts"] = items, time.time()
            return True
        return False
    soul.tap(hit[0], hit[1] - 34)
    time.sleep(3.5)
    # ⭐ 上面 tap 切到了星球 tab，屏幕已变 → 重读一帧再缓存（否则配额读到的是切页前的旧屏）
    try:
        items = rd.items()
        if any(any(k in t for k in MATCH_ENTRY) for t, _, _ in items):
            _PLANET_LAST["items"], _PLANET_LAST["ts"] = items, time.time()
    except Exception:
        pass
    return True


def in_session():
    texts = [t for t, _, _ in rd.items()]
    return any(any(k in t for k in SESSION_HINT) for t in texts)


def partner_name():
    """读会话页顶部昵称，并截掉粘连的状态词（"点正在线" → "点正"）

    ⭐ 2026-10-06 修（P1#8 昵称读成时间戳）：旧版返回**第一个**「cy<200 且 ≤16 字」的元素 →
      实测顶部噪声（时间戳「14:03」/ 状态词 / 纯标点）常排在昵称前面 → 把时间戳当昵称发开场白。
      现在：先滤掉时间戳/纯数字/纯标点（正则）、限定 `70<cy<200`（避开最顶状态栏），
      再**优先匹配设备库 `im.names()`**（真名唯一）；否则取 **y 最小**（最靠顶部=昵称行）。
    """
    items = rd.items()
    skip = ("关注", "返回", "查看主页", "分钟前", "刚刚", "匹配度")
    PURE_STATUS = ("在线", "离线", "刚刚", "分钟前", "Souler", "对方", "ta", "TA")
    _re_time = re.compile(r"^\d{1,2}[:：]\d{2}$")     # 时间戳 14:03 / 14：03
    _re_pure = re.compile(r"^[\d\W_]+$")              # 纯数字 / 纯标点 / 纯符号
    cands = []
    for t, cx, cy in items:
        s = (t or "").strip()
        if not s or any(k in s for k in skip):
            continue
        if not (70 < cy < 200) or len(s) > 16:
            continue
        for cut in ("在线", "刚刚", "分钟前"):
            i = s.find(cut)
            if i > 0:
                s = s[:i]
        s = s.strip()
        # ⚠️ 昵称行 OCR 整行丢失时只剩状态词（实测返回「在线」）→ 不能当昵称。
        if not s or s in PURE_STATUS:
            continue
        if _re_time.match(s) or _re_pure.match(s):
            continue
        cands.append((s, cy))
    if not cands:
        return None
    # ① 优先设备库真名（唯一可信）
    try:
        import soul_im as im
        dev = set(str(n).strip() for n in (im.names() or []) if str(n or "").strip())
    except Exception:
        dev = set()
    if dev:
        for s, cy in cands:
            if s in dev or any(s in d or d in s for d in dev):
                return s
    # ② 否则取 y 最小（最靠顶部 = 昵称行）
    cands.sort(key=lambda z: z[1])
    return cands[0][0]


# ==================== 单次匹配 ====================
def match_once(opening=None, dry=False, wait_match=30):
    """匹配一次并发开场白。返回 (昵称, 状态)
    状态: sent / dry / no_match / no_button / no_planet / interrupted_by_msg
    """
    # 更新全局锁心跳（跨实例互斥用）
    try:
        import soul_global_lock as _GL
        _GL.touch("match")
    except Exception:
        pass
    hit, _ = interrupted()
    if hit:
        return (None, "interrupted_by_msg")
    if not to_planet():
        return (None, "no_planet")

    items = rd.items()
    btn, kw = None, None
    # 优先「匹配下一个」（在匹配会话页时），否则回星球页点入口
    for cand in (NEXT_BTN,) + MATCH_ENTRY:
        b = next(((cx, cy) for t, cx, cy in items if cand in t), None)
        if b:
            btn, kw = b, cand
            break
    if not btn:
        return (None, "no_button")

    print(f"  点「{kw}」({btn[0]},{btn[1]})")
    _t0 = time.time()                       # 点匹配按钮前的时间锚（no_match 数据库兜底用）
    soul.tap(*btn)

    got = False
    for i in range(max(1, wait_match // 3)):
        time.sleep(3)
        # ⭐ 抢占点：等待匹配的每一小步都检查（这是最容易漏消息的窗口）
        h, rows = interrupted()
        if h:
            return (None, "interrupted_by_msg")
        # ⭐ 2026-10-06（用户口径：「匹配的时候 OCR 分析一下剩余次数」）
        #   **一次 OCR 同时判两件事**（不再分开调，省一次识别）：
        #     · 进会话页了吗？            → 匹配成功
        #     · 浮着「今日免费匹配机会已用完」吗？ → 额度用完，**立刻收手**
        #   旧行为：不管怎样都空等满 30s 才报 no_match，还要连着 2 轮才敢判"额度耗尽"
        #   （那是**猜**的）。现在第一次点到弹层就 100% 确定用完 —— 弹层文字是读出来的。
        try:
            _it = rd.items()
        except Exception:
            _it = []
        if any(any(k in t for k in SESSION_HINT) for t, _, _ in _it):
            got = True
            break
        _qk = getattr(soul, "QUOTA_KEYS", ())
        if _qk and any(any(k in (t or "") for k in _qk) for t, _, _ in _it):
            print("  ⛔ 点出「今日免费匹配机会已用完」弹层 → 额度确实用完（读出来的，不是猜的）→ 立刻收手")
            return (None, "no_quota")
    # ⭐ 2026-10-06 数据库兜底（修「匹配成功落在匹配卡片页 → OCR 特征全 miss → 误判 no_match
    #   → 匹配成功的人没发开场白被 ghost」）：
    #   OCR 认不出卡片页，但**匹配成功必在 IM 库落一条新会话**（系统卡片/招呼消息）。
    #   所以 OCR 说没中时，再拉一次库：若出现 _t0（点按钮前）之后**新出现**的会话
    #   （首条消息也在 _t0 之后 —— 排除老会话恰好来新消息，那种 interrupted() 会抓）
    #   → 其实匹配上了，取该会话昵称照常发开场白。整段异常必须按原样 no_match。
    _db_name = None
    if not got:
        try:
            time.sleep(3)                       # 给 App 落库留缓冲
            import sqlite3 as _sq
            import soul_im as im
            im.pull()
            c = _sq.connect("file:%s?mode=ro" % im.IMDB.replace("\\", "/"), uri=True, timeout=5)
            try:
                _sid = None
                for sid, mn, mx in c.execute(
                        "SELECT sessionId, MIN(localTime), MAX(localTime) FROM chatmsg "
                        "GROUP BY sessionId"):
                    try:
                        ts = float(mn or 0)     # 首条消息时间；localTime 有毫秒/秒两种口径
                    except Exception:
                        continue
                    if ts > 1e12:
                        ts /= 1000.0
                    if ts > _t0:
                        _sid = sid
                        break
                if _sid:
                    r = c.execute("SELECT toUserId FROM session WHERE sessionId=?",
                                  (_sid,)).fetchone()
                    _uid = str(r[0]) if r and r[0] else ""
                    _db_name = (im.names() or {}).get(_uid) if _uid else None
                    got = True
                    print(f"  ✅ OCR 未认出但库里出现新会话（{_db_name or _uid or _sid}）→ 按匹配成功走")
            finally:
                c.close()
        except Exception as _e:
            print(f"  ⚠️ no_match 数据库兜底失败（{_e!r}）→ 按原样判 no_match")
    if not got:
        return (None, "no_match")

    name = partner_name() or _db_name or "?"
    print(f"  匹配到「{name}」")
    if dry:
        return (name, "dry")

    msg = opening or profile_opening(allow_city=on_ready_list(name))
    if not msg:
        msg = fallback_opening(name)
        print(f"  ⚠️ 无个性化素材 → 兜底基础开场白「{msg}」（用户铁律：匹配到人必须发）")
    from soul_send import _input, verify_sent
    _input(soul.BOX_XY, msg)
    time.sleep(0.8)
    soul.tap(*soul.SEND_XY)
    time.sleep(1.5)
    ok = verify_sent(msg, timeout=12)
    print(f"  开场白: {msg} → {'✅' if ok else '❌'}")
    return (name, "sent" if ok else "send_failed")


def match_batch(n=3, dry=False):
    """匹配 n 个。**任一检查点发现新消息就立刻中断**。
    返回 (已匹配名单, 结束原因, 每次尝试的细分原因列表)
      why ∈ pending（被新消息打断）/ done（跑完 n 次）
      reasons = match_once 每次未成功的 res，如 ["no_planet","no_planet","no_planet"]

    ⭐ 2026-10-06 新增第 3 个返回值（细分原因）—— 修的 bug：
      daemon 只看「匹配到几个」，`0 个` 一律记成「额度/候选耗尽 → 转唤醒」。
      实测 00:34 连续 3 次 `no_planet`（**压根没进到星球页**，连匹配按钮都没点到），
      却被报成"额度耗尽"，白白放弃了本轮匹配。细分原因让上层能区分：
        · 导航类（no_planet / no_button）= 页面没摆正，**不是额度问题** → 应重试
        · no_match = 点了匹配但没出结果 → 才可能是额度/候选耗尽
    """
    done, reasons = [], []
    for k in range(1, n + 1):
        h, rows = interrupted()
        if h:
            return (done, "pending", reasons)
        print(f"[{k}/{n}] 匹配中…")
        name, res = match_once(dry=dry)
        if res == "interrupted_by_msg":
            return (done, "pending", reasons)
        if name:
            done.append(name)
        else:
            reasons.append(res)
            print(f"  未匹配到（{res}）")
        time.sleep(1.5)
    return (done, "done", reasons)


if __name__ == "__main__":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    except Exception:
        pass
    n = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 3
    dry = "--dry" in sys.argv
    if "--force" in sys.argv:
        globals()["_FORCE"] = True
        print("⚠️ --force：跳过待回检查（仅用于被不可达对象卡死时，勿常态用）")
    print(f"=== 匹配 {n} 个（dry={dry}）===")
    names, why, reasons = match_batch(n, dry=dry)
    print(f"--- 完成 {names} | 结束原因: {why} | 细分: {reasons} ---")
    if why == "pending":
        print("⚠️ 被新消息打断 → 上层应立刻去回复，完事再回到匹配")
