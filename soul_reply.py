# -*- coding: utf-8 -*-
"""一行命令回复：自动在聊天列表定位「昵称」→ 进入会话 → 发送 → 返回列表 → 写入数据库

用法:
  python soul_reply.py "昵称" "内容1" ["内容2"] [--wait 30]   # 自动定位发送；--wait 发完静默等 N 秒看回复
  python soul_reply.py --scan                                # 看当前聊天列表可点的人（昵称 + 坐标）

为什么不用「直接写数据库」发送：
  IM 消息必须经过服务端（有 msgId / serverTime / 协议签名），写本地库只会让本地显示一条假消息、
  对方收不到，还会被 App 覆盖。**发送必须走 UI**——本脚本就是把 UI 流程封装成一条命令。
"""
import sys, io, time, sqlite3, os, re

sys.path.insert(0, r"E:\soul")
import soul
import soul_read as rd
import soul_im as im
from soul_send import send_msg, check_len, vis_len
import soul_db as db

PKG = "cn.soulapp.android"
ACT = "cn.soulapp.android/.component.startup.main.MainActivity"

# ⭐ 回复场景更严的单条上限（2026-09-28 用户当面纠正："聊天的时候不要长篇大论"）
#   技能原文：一次 2~3 条，每条 ≤20 字居多，别写成小作文 / 超过 20 字的句子禁用。
#   回复（话题已开）不需要开场那种信息量，25 字已给足余量；超过→拒发，改写后再发。
REPLY_MAXLEN = int(os.environ.get("SOUL_MAXLEN_REPLY", "25"))

# ⭐ 2026-10-04 观测补丁：pythonw 下 daemon 的 stdout 落不到文件（实测 stdout.1.txt 19:06 后
#   就不再更新，daemon 却活着）→ soul_reply/soul_send/soul 里所有过程 print 全部不可见，
#   发送链路一断就两眼一抹黑（42-50s"静默"其实只是不可观测）。
#   在本模块 import 时把 sys.stdout 接到自己的 trace 文件（append）：谁 print 都有据可查。
#   交互式 CLI（真 tty）不劫持；kill switch：SOUL_TRACE=0。
if os.environ.get("SOUL_TRACE", "1") != "0":
    try:
        import threading as _th
        _TRACE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_uimap",
                                   "daemon",
                                   "stdout_trace.%s.log" % os.environ.get("SOUL_VMINDEX", "0"))

        class _TraceTee(io.TextIOBase):
            def __init__(self):
                self._lk = _th.Lock()

            def write(self, s):
                with self._lk:
                    try:
                        with io.open(_TRACE_PATH, "a", encoding="utf-8",
                                     errors="replace") as f:
                            f.write(s)
                    except Exception:
                        pass
                return len(s)

            def flush(self):
                pass

            def isatty(self):
                return False

            def writable(self):
                return True

        _so = sys.stdout
        try:
            _interactive = _so is not None and _so.isatty()
        except Exception:
            _interactive = False
        if not _interactive:
            sys.stdout = _TraceTee()
            print("  [trace] stdout 已接管 → %s" % _TRACE_PATH)
    except Exception:
        pass


def scan():
    soul.connect()
    for t, cx, cy in rd.items():
        print(f"y={cy:<5} x={cx:<5} {t}")


# ⭐ 2026-09-28 新增两个环境开关（默认行为完全不变，零风险）：
#   SOUL_FIND_PAGES=N   找人多翻 N 屏（默认 6）。列表里真人会话已 150+，冷场 ≥48h 的对象
#                       常排在 6 屏之外 → 默认值够不到，会误报「未找到」。需要捞深位置时：
#                       SOUL_FIND_PAGES=30 python soul_reply.py "昵称" "内容"
#   SOUL_NAME_EXACT=1   昵称改为**精确相等**匹配（忽略 ❤️ 等装饰）。用于「昵称是别人昵称子串」
#                       的危险场景：如 "初见" ⊂ "若只如初见"、"只若如初见" —— 子串匹配会点错人。
FIND_PAGES = int(os.environ.get("SOUL_FIND_PAGES", "6"))
# 搜索通道逐步截图取证（2026-09-29 用户要求「用截图来确认 每一个步骤」）。关掉：SOUL_SEARCH_TRACE=0
SEARCH_TRACE = os.environ.get("SOUL_SEARCH_TRACE", "1") != "0"

# ⭐ 2026-09-29 频率闸（用户点名"10分钟4条"过热）：
#   SOUL_MAX_UNANSWERED       她上次开口之后，我最多能连发几条（默认 3）—— 核心判据
#   SOUL_MIN_BATCH_GAP_MIN    距我上一条的最小间隔（分钟），仅当"她还没回"才生效（默认 6）
#   注：**没有"24h 总条数"上限** —— 她一直在回的活跃对话，条数多不是问题（第一版就此翻车）。
MAX_UNANSWERED = int(os.environ.get("SOUL_MAX_UNANSWERED", "3"))
MIN_BATCH_GAP_MIN = float(os.environ.get("SOUL_MIN_BATCH_GAP_MIN", "1"))


def _norm_name(t):
    """去掉 ❤️ 等装饰与空白，用于精确比对"""
    import re as _re
    return _re.sub(r"[\u3002\uff01\uff1f\u2026\uff5e]+$", "", (t or "").replace("❤", "").replace("\ufe0f", "").replace("\u200d", "").strip())


_DB_NICKS = None


def _db_nicks():
    """本地库 im_user_bean.signature 的**权威昵称表**（不依赖 OCR）。

    ⭐ 2026-09-29 新增，用来区分两种"模糊命中"：
      · **库中另一个人的精确昵称** → 危险，必须拒绝（真实事故：目标「初见」，命中「若只如初见」）
      · **纯 OCR 噪声**（emoji `✨` 被读成 `+`）→ 库里对不上任何人 → 可以放行
    """
    global _DB_NICKS
    if _DB_NICKS is None:
        try:
            _DB_NICKS = {_norm_name(s) for s in im.names().values() if (s or "").strip()}
        except Exception as e:
            print(f"  !! 读取库昵称表失败（本次不做该层校验）: {e!r}")
            _DB_NICKS = set()
    return _DB_NICKS


def _is_other_person(nick):
    """nick 是不是**库里另一个人**的精确昵称（= 绝不该被当成目标的别名）"""
    return _norm_name(nick) in _db_nicks()


def _embeds_other_nick(nick, name_self, min_len=3):
    """nick 里**嵌着**另一个人的完整昵称吗？

    ⭐ 2026-09-29 补 `_is_other_person` 的漏洞：目标「初见」时，同一行会被 OCR 读成
      「若只如初见关注了我」——这串**不等于**任何库昵称，所以精确比对放行了它，
      点进去才发现是别人的会话（被串台闸拦下，没误发，但白跑一趟）。
      判据：候选串里若包含某个**别人的完整昵称**（≥3 字，避开「星河」这类短名误伤
      「星河入梦✨→星河入梦+」的 OCR 噪声场景）→ 判为别人的行，拒绝。
    """
    t = _norm_name(nick)
    me = _norm_name(name_self)
    for nk in _db_nicks():
        if nk and nk != me and len(nk) >= min_len and nk in t:
            print(f"    !! 候选串「{nick[:16]}」里嵌着别人的昵称「{nk}」→ 视为非目标行")
            return True
    return False


# ⭐ 2026-10-04（F1）sid 优先：调用方（soul_daemon.do_reply）已从 pending 的 sessionId
#   拆出**权威 uid**（sessionId = ME + toUserId，已由 2026-10-04 实测方案 A 印证）。
#   由它先于"昵称解析"生效：昵称是 OCR 产物，同名/长昵称/emoji 都会失手，而 sessionId
#   直接来自 Soul 自己的会话表，是同一行记录里带出来的，天然对应"发来这句话的人"。
_UID_HINT = {"uid": None, "nick": None}


def set_uid_hint(uid):
    """设置/清除本次发送的「权威 uid」。uid 为 None 时关闭（回退原昵称逻辑）。"""
    u = str(uid).strip() if uid is not None else ""
    if u and u.isdigit():
        nk = None
        try:
            nk = im.names().get(u)
        except Exception:
            nk = None
        _UID_HINT["uid"], _UID_HINT["nick"] = u, nk
    else:
        _UID_HINT["uid"], _UID_HINT["nick"] = None, None


def _uid_of(name):
    """昵称 → uid，**精确优先**。

    ⭐ 2026-09-29 新增。原来 4 处都写成 `next((k for k,v in nm.items() if name in v), None)`，
      **取的是字典里第一个子串命中** → 目标「初见」被解析成「若只如初见」的 uid（83850113）。
      后果（本轮实测）：
        · `_my_last_text()` 拿**别人**的聊天记录做"跨轮重复"比对 → 把正常消息误判成重复而拦下
        · `verify_sent()` / `_last_sender_is_me()` 校验的是**别人的会话** → 发送校验结果不可信
      策略与 `_hit` 一致：精确唯一 → 用它；子串唯一 → 用它；否则**拒绝猜**（防串台）。
    """
    # ⭐ 2026-10-04（F1）：先认调用方给的**权威 uid**。
    #   原来"库里有 2 个精确同名 → 拒绝猜 uid"（实测「小仙女」）会把真人挡在自家闸外；
    #   现在有 sid 反查出的 uid，就不再需要猜。防串台**只加强不削弱**：
    #   要求该 uid 在库里的昵称与本次目标昵称**骨架相容**（互相包含），否则照旧退回。
    _hu = _UID_HINT.get("uid")
    if _hu:
        _hk = _skeleton(_UID_HINT.get("nick") or "")
        _nk = _skeleton(name)
        if _hk and _nk and (_hk in _nk or _nk in _hk):
            return _hu
    nm = im.names()
    exact = [k for k, v in nm.items() if (v or "").strip() == name]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        print(f"  !! 昵称「{name}」库里有 {len(exact)} 个精确同名 → 拒绝猜 uid（防串台）")
        return None
    subs = [k for k, v in nm.items() if name in (v or "")]
    if len(subs) == 1:
        return subs[0]
    if len(subs) > 1:
        who = [nm[k] for k in subs]
        print(f"  !! 昵称「{name}」子串命中 {len(subs)} 人 {who} → 拒绝猜 uid（防串台）")
    return None


def _verify_by_content(name):
    """会话**已经打开**但拿不到 uid 时，用**屏幕上真实可见的内容**证明"这就是 name 的会话"。

    ⭐ 2026-09-29 补：替代那条危险的"退回全库最新会话"兜底。
    事故原型（本轮 12:40 实测）：搜索「老阿姨」→ 点开会话 → 脚本打印
        `未检出本次新开的会话，退回'全库最新'判据` → `候选1：uid=… 昵称=星河入梦✨ 历史真人消息=21 条`
        → `✅ 判定为本人`。
    实际点开的是**老阿姨**（落点对，DB 校验也没问题），但**身份核对的凭据来自一个完全无关的会话**
    —— 也就是说：这道闸在真点错人时**同样会放行**，形同虚设，且日志会把人带偏（本轮一度误判为误发）。

    办法：从库里取该会话最近若干条真人消息，抽 5 字探针去当前屏 OCR 文本里找；
    命中任一 → 屏幕内容确实属于该会话 → 通过。一条都找不到 → **拒绝**（fail-closed）。
    """
    try:
        uid = _uid_of(name)
        if not uid:
            # 🔴 2026-09-29 补：昵称解析不出唯一 uid 时**不要直接放弃**。
            #   实测：搜「心中藏」→ 库昵称是带老挝文装饰的「心中藏໌້ᮨ恶鬼…」，
            #   子串还撞上「心中藏山海～」「心中藏山海」→ `_uid_of` 拒绝猜 uid →
            #   本函数直接判"未通过" → 人明明就在会话页里也被放弃（连续多轮唤醒全灭的真凶之一）。
            #   兜底：用**会话页顶部标题 vs 库昵称骨架**比对（见 `_verify_by_title`）。
            return _verify_by_title(name)
        c = sqlite3.connect(im.IMDB)
        rows = c.execute("SELECT text, msgContent, localTime FROM chatmsg "
                         "WHERE sessionId LIKE ? ORDER BY localTime DESC LIMIT 60",
                         (f"%{uid}",)).fetchall()
        c.close()
        real = [(t or "").strip() for t, mc, lt in rows if t and not im._is_sys(t, mc)]
        if not real:
            return False, "库里该会话没有真人文本可比对"
    except Exception as e:
        return False, f"取库失败 {e!r}"
    try:
        soul.screenshot()
        screen = "".join(t for t, _, _ in rd.items()).replace(" ", "")
    except Exception as e:
        return False, f"读屏失败 {e!r}"
    for txt in real[:8]:
        s = txt.replace(" ", "")
        if len(s) < 4:
            continue
        probe = s[:5]
        if probe in screen:
            return True, f"屏幕上找到该会话原话「{probe}」"
        probe2 = s[-5:]
        if len(s) >= 5 and probe2 in screen:
            return True, f"屏幕上找到该会话原话「{probe2}」"
    return False, f"屏幕上看不到该会话任何原话（{len(real[:8])} 条探针全未命中）"


def _skeleton(s):
    """昵称"骨架"：只留中英文与数字，去掉老挝文/藏文/emoji/标点/空白。

    为什么需要：Soul 库里存的昵称常带装饰字符（例「心中藏໌້ᮨ恶鬼࿙໌້ᮨ࿚໌້」），
    而会话页顶部标题 OCR 出来的是可读版（「心中藏恶鬼，眼中无良人！」），
    两边直接比永远不相等 —— 洗净后才有可比性。
    """
    import re
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", s or "")


_EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D]")


def _type_query(name):
    """搜索框实际可输入的查询：昵称含 emoji（ADBKeyboard 输不进，实测搜「💞笑看🌱红尘💤」必败）
    → 改用核心串（只留中英文数字）；搜中后仍由历史快照 + 内容探针证明身份，不冒名。"""
    q = (name or "").strip()
    if _EMOJI_RE.search(q):
        core = _skeleton(q)
        if len(core) >= 2:
            return core
    return q


def _title_match(target, ts):
    """本人昵称骨架 target 与会话页标题骨架 ts 是否**真的对得上**。

    三条放行规则，一条都不能少：
      1) ts 以 target 开头 —— 正常（标题 = 昵称 + 「在线/23小时前」等后缀）
      2) target 以 ts 开头 —— 标题被 OCR 截断（「心中藏恶鬼，眼中无.」）
      3) ts **包含** target，且 target 之前那段**没有中文** —— 容忍角标噪声
         🔴 2026-09-29 实测必需：标题被读成「<2好好说话」（未读数 2 被 OCR 成符号），
            纯前缀判据直接把本人判成串台。
    🔴 但**前缀含中文**时必须拒绝：标题「若只如初见」里虽然有「初见」，
       前面「若只如」是中文 → 那是另一个人（真实事故，绝不能再放）。
    """
    import re as _re
    if not target or not ts:
        return False, "骨架为空"
    if ts.startswith(target):
        return True, "标题以本人昵称开头"
    if target.startswith(ts):
        return True, "标题被截断（是本人昵称的前缀）"
    if target in ts:
        pre = ts[:ts.index(target)]
        if not _re.search(r"[\u4e00-\u9fff]", pre):
            return True, f"含本人昵称（前缀「{pre}」是角标噪声）"
        return False, f"「{pre}」+本人昵称 → 这是另一个人"
    return False, "对不上"


def _lcp(a, b):
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def _mem_db():
    """累积库路径（只增不减）。由 soul_daemon.merge_memory() 维护。"""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "soul_memory.db")


def _hist_uids(minn=3):
    """库里**有真人往来**（≥minn 条）的对方 uid 集合 —— "这个人我们真聊过"的客观凭据。

    ⭐ 2026-10-04 修（会**误拒真人**）：
      正式库 im_data.db 是**从设备拉下来的**，而 Soul 会清空它（实测 2811 → 55 条）。
      清空后本函数判定全部归零 → `_resolve_db_target()` 返回 None →
      `_on_session_of()` 打印「库里没有聊过且含「XXX」的昵称 → 拒绝」→ **对真人拒发**。
      （`soul_fast` 里有 monkey-patch 让本函数并上累积库，但**只有 import 过 soul_fast
        才生效**；而 do_reply 走 allow_chain 全路径、不 import soul_fast → patch 经常没装上。）
      现在直接把**累积库**并进来：那才是全量事实。
      ⚠️ 累积库口径按"该会话消息条数"计（与 soul_fast 的 patch 一致），系统卡片也计入。
    """
    out = set()
    try:
        c = sqlite3.connect(im.IMDB)
        for sid, uid in c.execute("SELECT sessionId, toUserId FROM session"):
            rows = c.execute("SELECT text, msgContent FROM chatmsg WHERE sessionId=?",
                             (sid,)).fetchall()
            n = sum(1 for t, mc in rows if t and not im._is_sys(t, mc))
            if n >= minn:
                out.add(str(uid))
        c.close()
    except Exception as e:
        print(f"  !! 取往来 uid 失败（正式库）: {e!r}")
    mp = _mem_db()
    if os.path.exists(mp):
        try:
            c = sqlite3.connect(mp)
            for uid, n in c.execute(
                    "SELECT toUserId, (SELECT count(*) FROM chatmsg m "
                    " WHERE m.sessionId IN (SELECT sessionId FROM session s2 "
                    "  WHERE s2.toUserId = s.toUserId)) FROM session s"):
                if uid and (n or 0) >= minn:
                    out.add(str(uid))
            c.close()
        except Exception as e:
            print(f"  !! 取往来 uid 失败（累积库）: {e!r}")
    # ⭐ 2026-10-04：建脚本前人工聊过的老友 —— 历史条数 <minn，但**她最后一条真人
    #   消息还在等我回**（= 她主动来信）也算"聊过的本人"，否则会被误判成陌生人而拒发。
    #   口径与 soul_daemon._sendable 一致：只发过开场白、她还没回的仍不算。
    if os.path.exists(mp):
        try:
            c = sqlite3.connect(mp)
            for sid, uid in c.execute("SELECT sessionId, toUserId FROM session"):
                rows = c.execute(
                    "SELECT senderId, text, msgContent FROM chatmsg WHERE sessionId=? "
                    "ORDER BY localTime DESC LIMIT 8", (sid,)).fetchall()
                for sd, tx, mc in rows:
                    if not tx or not str(tx).strip():
                        continue
                    if im._is_sys(tx, mc):
                        continue
                    if uid and str(sd) != str(im.ME):
                        out.add(str(uid))
                    break
            c.close()
        except Exception as e:
            print(f"  !! 取「等我回」uid 失败（累积库）: {e!r}")
    return out


def _resolve_db_target(name):
    """**本人**的昵称骨架 —— 由"库里聊过的人"确定，**绝不由屏幕标题反推**。

    🔴 2026-09-29 事故后重写。上一版拿标题去库里挑"最像的那个"，等于
       *只要库里存在任何一个名字相近的人就放行*：目标「心中藏」→ 标题「心中藏山海～」
       → 库里恰好有「心中藏山海」→ 公共前缀 5 判"吻合" → **消息发给了陌生人**。
       这是典型的 fail-open：判据看上去在核对，实际任何人都对得上。

    现在的定义：本人 = 库里**聊过 ≥3 句**且昵称骨架包含 name 骨架的那个。
      心中藏 → 只有「心中藏恶鬼」有 27 条往来，山海/小月亮都是 0 条 → target=心中藏恶鬼 ✅
      初见   → 「初见」与「若只如初见」都聊过，但 name 本身是完整昵称 → 锁定「初见」 ✅
    定不下来（0 个或多个且 name 不是完整昵称）→ 返回 None，调用方必须拒绝。
    """
    ns = _skeleton(name)
    if not ns:
        return None
    try:
        nm = im.names()
    except Exception as e:
        print(f"  !! 取库昵称失败: {e!r}")
        return None
    hu = _hist_uids()
    K = set()
    for u, nk in nm.items():
        k = _skeleton(nk)
        if k and ns in k and str(u) in hu:
            K.add(k)
    if ns in K:
        return ns                      # name 本身就是完整昵称 → 锁死，不许挑别人
    if len(K) == 1:
        return next(iter(K))
    if len(K) > 1:
        print(f"  ⛔ 库里 {len(K)} 个聊过的昵称都含「{name}」 → 拒绝猜本人")
    else:
        print(f"  ⛔ 库里没有聊过且含「{name}」的昵称 → 拒绝（唤醒对象不该是陌生人）")
    return None


def _verify_by_title(name):
    """**会话页顶部标题** vs 库昵称骨架，证明"屏幕上这个人就是 name"。

    ⭐ 2026-09-29 新增（用户要求「直接走搜索」后暴露的缺口）：
      搜索通道点开了正确的人，但 `_verify_by_content` 因 uid 不唯一无从下手。

    判据（fail-closed，宁可不发）：
      1) 取会话页 y<200 的 OCR 文本当标题（实测昵称在 y≈104）
      2) 库昵称里"骨架包含 name 骨架"的构成候选集 K
      3) 定 target：
         · name 本身就是完整库昵称（skeleton(name) ∈ K）→ target = skeleton(name)
           —— **这条是防「初见」被「若只如初见」冒名的关键**：此时绝不许挑别人
         · 否则取与标题**最长公共前缀最大**的那个；并列 → 拒绝（不猜）
      4) 通过条件：target 与标题骨架**一方是另一方的前缀**（互相包含），
         且公共前缀 ≥ len(skeleton(name))

    实测：
      心中藏  → target「心中藏恶鬼」⊂ 标题「心中藏恶鬼眼中无」 ✅ 通过
      初见    → target「初见」，标题「若只如初见」不以它开头 ❌ 拒绝（正确）
    """
    try:
        soul.screenshot()
        title = "".join(t for t, _, cy in rd.items() if cy < 200)
    except Exception as e:
        return False, f"读标题失败 {e!r}"
    ts = _skeleton(title)
    ns = _skeleton(name)
    if not ts:
        return False, "会话页顶部读不到标题"
    if not ns:
        return False, "名字骨架为空（纯符号昵称，无法比对）"
    # 🔴 2026-09-29 事故修正：target **只能由库里"聊过的人"确定**，
    #    绝不用标题去库里反挑最像的（那样任何名字相近的人都能通过 → 误发给陌生人）。
    target = _resolve_db_target(name)
    if not target:
        return False, "定不下本人（库里聊过的昵称不唯一或不存在）"
    ok, why = _title_match(target, ts)
    return (True, f"标题「{ts[:12]}」与本人「{target}」：{why}") if ok else (
        False, f"标题「{ts[:12]}」与本人「{target}」：{why}")


def _hit(name):
    """当前屏找昵称，命中返回坐标。

    ⭐ 2026-09-28 修串台：原来是裸子串匹配 `name in t`，导致**短昵称误发**。
       实测：目标「辰」，结果匹配到「星辰还是星晨」把消息发给了别人（uid 完全不同）。
    现在三级策略：
      1) **精确相等** → 直接用（最可靠）
      2) 无精确 → 子串匹配，**只命中 1 个人**才用，并打印警告
      3) 子串命中多人 → **拒绝返回 None**，防串台（宁可找不到，也不能发错人）

    ⭐ 2026-09-29 补第 2 级的漏洞（真实事故）：目标「初见」本屏只有「若只如初见」一个子串命中
       → 被当"唯一候选"接受 → **消息发进了「若只如初见」（uid 83850113，另一个人）**。
       根因：判断"是不是同一个人"只看了**当前屏 OCR**，而**库里有权威昵称表**。
       → 现在：模糊命中若恰好是**库里另一个人的精确昵称**，一律拒绝（宁可找不到，也不发错人）。
    """
    items = rd.items()
    _want = _norm_name(name)
    _core_want = _skeleton(name)
    # 1) 精确（昵称一致；或去 emoji/装饰后的核心串一致 —— OCR 常把 💞🌱 整段读丢）
    for t, cx, cy in items:
        _tn = _norm_name(t)
        if _tn == _want:
            return cx, cy
        if len(_core_want) >= 2 and _skeleton(t) == _core_want:
            return cx, cy
    if os.environ.get("SOUL_NAME_EXACT") == "1":
        return None
    # 2) 子串，收集所有不同的候选昵称
    cands = {}
    for t, cx, cy in items:
        _tn = _norm_name(t)
        if _want in _tn:
            cands.setdefault(_tn, (cx, cy))
        elif len(_core_want) >= 2 and _core_want in _skeleton(t):
            cands.setdefault(_tn, (cx, cy))
    if len(cands) == 1:
        only = list(cands.keys())[0]
        if only != _norm_name(name):
            if _is_other_person(only) or _embeds_other_nick(only, name):
                print(f"⛔ 模糊命中「{only}」指向库里**另一个人**（目标「{name}」）→ 拒绝，防串台")
                return None
            print(f"⚠️ 昵称模糊命中：「{name}」→「{only}」（非精确匹配，请确认是否本人）")
        return list(cands.values())[0]
    if len(cands) > 1:
        print(f"⛔ 昵称「{name}」模糊匹配到 {len(cands)} 个不同的人 "
              f"{list(cands.keys())} → 拒绝，防串台（请用完整昵称）")
        return None
    return None


def _list_fingerprint(path=None):
    """列表页降采样灰度指纹：用来判断"画面有没有真的滚动"（截图有噪点，不能逐像素比）"""
    try:
        from PIL import Image
        p = path or soul.SHOT
        im = Image.open(p).convert("L").resize((32, 64))
        return list(im.getdata())
    except Exception:
        return None


def _fp_diff(a, b, tol=2.5):
    if a is None or b is None:
        return True
    return sum(abs(x - y) for x, y in zip(a, b)) / float(len(a)) > tol


def _scroll_top(max_try=25, force=False):
    """⭐ 2026-09-29 重写：**双击底部「聊天」回顶在这台 MuMu 上不生效**
    （实测连点 10+ 次仍停在 9月25日 区块 → 旧实现形同虚设）。

    用户指点：「新消息在顶部」。所以找人前先**用手势真滚到顶**，
    这样近期会话（也就是绝大多数发送目标）第一屏就能命中。
    判据：连续两次上滑画面不再变化 = 到顶。

    ⭐ 2026-09-30 提速（用户反馈"每次操作都太慢"）：
      本函数一次最多 25 轮 ×（mumu-cli 滑动 + 0.45s + 截图 + 指纹）≈ **30~40 秒**，
      而一轮里 `find()` / `find_by_search()` 会各调一次 → 同一个人白回顶两次。
      现在用 `soul.at_top()` 记进程内状态：**已知在顶就整段跳过**；
      任何 swipe/tap/切页都会由 `soul.mark_top(False)` 自动失效 → 不会漏回顶。
      另：`ensure_foreground()` 每次要跑一次 `dumpsys window`（实测上百毫秒），
      从循环里挪到开头只做一次。强制重来：`force=True`。
    """
    if soul.at_top() and not force:
        return True
    print(f"  [trace] _scroll_top 滚动回顶开始（max_try={max_try}，每轮 滑+截图 ≈1s）")
    soul.ensure_foreground()
    prev, still = None, 0
    for _ in range(max_try):
        soul.swipe_down()
        time.sleep(0.45)
        soul.screenshot()
        h = _list_fingerprint()
        if _fp_diff(prev, h):
            still = 0
        else:
            still += 1
            if still >= 2:
                soul.mark_top(True)
                return True
        prev = h
    return False


# 搜索通道坐标（**屏幕比例**，2026-10-04 实测重标定）
# ⚠️ 事故根因（本轮实测）：这里原来写死的是 900x1600 时代的**绝对像素**
#   （SEARCH_BOX/(180,800)、_SP_IN/(180,200)、SEARCH_PRIV_BTN_X/629），
#   而 Soul 实跑在 **540x960**（`wm size` 实测；900x1600 只是 soul_read 的旧缓存）。
#   (180,800) 在 540x960 下正好落在**某条会话行**上 → 点下去直接进了别人的会话页 →
#   被下层 POAV 的 ConversationActivity 守卫拦下 → "三个动作全部验证失败 → 放弃本轮"
#   ⇒ 所有"聊天列表里没有渲染行"的人（老友/卡片末条/语音末条）**永远发不出去**。
#   现一律用比例，运行时按 soul.DEV_W/DEV_H 换算（与 soul.py 的 _FRAC 同一套做法）。
# 实测证据（540x960，截图 E:\soul\_probe\00_list.png / 01_search.png）：
#   聊天列表**没有搜索框**，搜索入口 = 顶部栏右上角**放大镜图标** (440,69)；
#   搜索页输入框占位「搜索备注、昵称或者聊天记录」中心 (221,39)；「取消」(496,41)；
#   搜索结果/猜你想搜每行右侧「私聊」按钮 x≈477。
SEARCH_FRAC = {
    "icon":   (0.8148, 0.0719),   # 聊天列表顶部栏右上角放大镜（搜索入口）
    "input":  (0.4093, 0.0406),   # 搜索页顶部输入框
    "cancel": (0.9185, 0.0427),   # 搜索页右上角「取消」
    "priv_x": (0.8830, 0.0000),   # 搜索结果每行右侧「私聊」按钮的 x 比例
}


def _sf(k):
    """搜索通道比例 → 当前分辨率绝对坐标（调用时算，保证跟着 calibrate() 走）。"""
    fx, fy = SEARCH_FRAC[k]
    return (int(round(soul.DEV_W * fx)), int(round(soul.DEV_H * fy)))


def _tap_search_box():
    """POAV 闭环（2026-10-03 用户指示：先想→再看→决定点/滑→确认一致）：
    意图 = 进入搜索页；动作序列逐个试，每动作后截图验证；
    验证不过 → 换下一个动作；全失败 → 放弃本轮（fail-closed，绝不无证据输入）。
    A1: 底部「聊天」tab → （顶部「聊天」标签）→ 顶部栏右上角**放大镜**
    A2: 直接点放大镜（顶部栏固定，不随列表滚动 → 无需回顶）
    A3: 轻滑一段消除弹层 → 再点放大镜
    验证判据：检出「取消/猜你想搜/搜索历史」= 已进搜索页。"""
    def _verify():
        try:
            soul.screenshot(force=True)
        except Exception:
            pass
        pg = [t for t, _, _ in rd.items()]
        ok = any(("取消" in t) or ("猜你想搜" in t) or ("搜索历史" in t) for t in pg)
        return ok, pg[:6]
    def _act(tag, fn):
        print("  [POAV] 意图=进入搜索页 → 动作 %s" % tag)
        try:
            fn()
        except Exception as _e:
            print("    !! 动作异常: %r" % (_e,))
            return False
        time.sleep(2.2)
        act = soul.activity() or ""
        if "ConversationActivity" in act:
            print("    !! 动作落到了会话页 → 清输入框残留，换动作（绝不写脏草稿）")
            try:
                soul.tap(*soul.BOX_XY)
                time.sleep(0.6)
                soul.clear_text()
            except Exception:
                pass
            soul.tap_back_arrow()
            time.sleep(1.2)
            return False
        ok, pg = _verify()
        print("    [POAV] 验证: %s | activity=%s OCR=%r" %
              ("OK 已进搜索页" if ok else "NG 未进搜索页", soul.activity() or "?", pg))
        return ok
    # A1：底部聊天 tab → （顶部「聊天」标签）→ 顶部栏右上角放大镜
    def _a1():
        # 回「聊天」tab（用 soul.TAB_CHAT 比例坐标；不再写死 900 空间的 (615,1585) —— 在 540x960 上已出屏）
        try:
            soul.tap(*soul.TAB_CHAT)
            time.sleep(1.4)
        except Exception as _e:
            print("    · 回聊天 tab 失败: %r" % (_e,))
        soul.screenshot()
        # 顶部栏若有「聊天」标签（通讯录/聊天 切换），点它确保停在会话列表
        for _t, _cx, _cy in rd.items():
            if _t.strip().startswith("聊天") and _cy < soul.DEV_H * 0.15:
                soul.tap(_cx, _cy)
                time.sleep(1.0)
                break
        # 搜索入口是**放大镜图标**（OCR 读不出图形，只能用固定比例坐标）
        soul.tap(*_sf("icon"))
    if _act("A1 底部聊天tab+顶部标签+放大镜", _a1):
        return True
    # A2：直接点放大镜
    def _a2():
        # 放大镜在**顶部栏固定位置**，不随列表滚动移动 → 不必回顶（省 ~25s/次）
        soul.ensure_foreground()
        soul.tap(*_sf("icon"))
    if _act("A2 放大镜（比例坐标）", _a2):
        return True
    # A3：轻滑消弹层后再点放大镜
    def _a3():
        try:
            soul.swipe(int(soul.DEV_W * 0.5), int(soul.DEV_H * 0.73),
                       int(soul.DEV_W * 0.5), int(soul.DEV_H * 0.54), 300)
            time.sleep(1.0)
        except Exception:
            pass
        soul.screenshot()
        for _t, _cx, _cy in rd.items():
            if ("搜索" in _t) and (("聊天" in _t) or ("昵称" in _t) or ("记录" in _t)):
                soul.tap(_cx, _cy)
                return
        soul.tap(*_sf("icon"))
    if _act("A3 轻滑+放大镜", _a3):
        return True
    print("  !! [POAV] 三个动作全部验证失败 → 放弃本轮（fail-closed，不输入）")
    return False


def find_by_search(name, max_cand=3):
    """⭐ 2026-09-29 新增：**搜索框兜底通道**（聊天列表里没有渲染行时用）。

    为什么必须要有：
      **末条消息为非文本（卡片/图/语音）的会话，不在聊天列表渲染行**（界面地图早有记载）。
      而**奇遇铃进来的会话，末条必然是「[亲密度卡片]」** → 看过一次（红点清掉）它就从列表消失，
      `find()` 永远报「未找到: 某某」，但人其实好端端在（实测：Nicole）。
      → 奇遇铃的正确做法是**收铃当场就把开场白发掉**（那时人就在会话页里），
        一旦错过，只能靠本函数走搜索框把人捞回来。

    重名保护：实测搜「Nicole」出 3 个同名用户 → 逐个点「私聊」，
      用**本地 IM 库里该会话有没有往来记录**判定是不是本人
      （本人已聊过 → 有真人消息；陌生人 → 点开只是全新空会话）。
      若所有候选都没有历史，则仅在**只有一个候选**时才接受（否则拒绝，绝不上来就发）。
    返回 True = 已停在这个人的会话页（调用方可直接发）。
    """
    # ⭐ 2026-09-29 用户要求：「走搜索的时候 用截图来确认 每一个步骤」。
    #   每一步都重新截图 + OCR 打印当前屏，状态显式暴露，绝不靠"上一步应该在搜索页"来推断。
    def _trace(tag):
        if not SEARCH_TRACE:
            return
        try:
            soul.screenshot()
            rows = [(t, cx, cy) for t, cx, cy in rd.items() if cy < 1200]
            print(f"    [截图·{tag}] act={soul.activity() or '?'} 共 {len(rows)} 行")
            for t, cx, cy in rows[:14]:          # 只打前 14 行，够判页面又不刷屏
                print(f"        y={cy:<5} x={cx:<5} {t!r}")
        except Exception as e:
            print(f"    [截图·{tag}] !! 取证失败: {e!r}")

    _trace("进搜索前")
    if not _on_chat_list():
        _goto_chat_list()
    # 快照：谁有往来记录（真人消息条数 > 0）
    hist = {}
    try:
        im.pull()
        nm = im.names()
        c = sqlite3.connect(im.IMDB)
        for sid, uid in c.execute("SELECT sessionId,toUserId FROM session"):
            rows = c.execute("SELECT senderId,text,msgContent FROM chatmsg WHERE sessionId=?",
                             (sid,)).fetchall()
            n = sum(1 for s, t, ct in rows if not im._is_sys(t, ct) and t)
            hist[str(uid)] = n
        c.close()
    except Exception as e:
        print(f"  !! 搜索通道取历史失败（保守处理）: {e!r}")
        nm = {}
    # ⭐ 2026-10-04：并上**累积库**（正式库会被 Soul 清空，实测 2811→55 条）。
    #   否则"本人有没有往来"会被判成 0 → 明明聊过的真人被当陌生人拒发。与 _hist_uids 同口径。
    try:
        if os.path.exists(_mem_db()):
            _c = sqlite3.connect(_mem_db())
            for _uid, _n in _c.execute(
                    "SELECT toUserId, (SELECT count(*) FROM chatmsg m "
                    " WHERE m.sessionId IN (SELECT sessionId FROM session s2 "
                    "  WHERE s2.toUserId = s.toUserId)) FROM session s"):
                _u = str(_uid)
                if _uid and (int(_n or 0)) > hist.get(_u, 0):
                    hist[_u] = int(_n or 0)
            _c.close()
    except Exception as e:
        print(f"  !! 搜索通道累积库取历史失败（保守处理）: {e!r}")

    # ⭐ 2026-09-29：「刚点开的是谁」判据修正所需的**打开前快照**。
    #   旧实现取 `SELECT toUserId FROM session ORDER BY timestamp DESC LIMIT 1`，
    #   那是"全库最新会话"，不是"本次刚点开的会话" → 实测打印出完全无关的人：
    #   搜「超级颜控的飛儿」却报 `uid=133677664 昵称=蓝朋友呀 历史86条`（并据此"判定为本人"）。
    #   本次没酿成误发（真正的护栏是发送前的 `_on_session_of()` 标题闸），
    #   但**候选身份读错**在"多候选重名"时会让它挑错人 → 必须改成"快照差异"法。
    def _sess_ts():
        c2 = sqlite3.connect(im.IMDB)
        d = {r[0]: (r[1] or 0) for r in c2.execute("SELECT sessionId, timestamp FROM session")}
        c2.close()
        return d

    pre_ts = _sess_ts()

    # 🔴 2026-09-29 修「唤醒全灭」根因：**搜索框会随列表滚动移出屏幕**。
    #   事实（本轮实测，两个候选连续复现）：`find()` 为了找人已经把列表翻下去很多屏，
    #   此时 tap(SEARCH_BOX=(276,188)) 落在的**是一条会话行**，不是搜索框 →
    #   直接进了那个人的会话页 → 被下面的 ConversationActivity 守卫拦下 → 放弃。
    #   症状是「点搜索框后竟停在会话页」，而**所有深位置对象（≥6 屏）都必然走到这里**
    #   ⇒ 12h 唤醒连续多轮 0 成功，根因就在这，不在"标题 OCR 读不到"。
    #   修法：点搜索框**前先真滚到顶**，让 (276,188) 回到搜索框位置。
    _scroll_top()
    soul.ensure_foreground()
    _trace("滚到顶·点搜索框前")
    # 🔴 POAV（2026-10-03 用户指示）：意图=进搜索页 → 动作序列 → 验证 → 换动作 → 全败放弃。
    #   安全原则不变：**拿不到"确实在搜索页"的证据，就什么都不输入**（脏草稿事故护栏）。
    if not _tap_search_box():
        return False
    _trace("点搜索框后（已确认在搜索页）")
    # ⭐ 2026-10-03 修「搜索页坐标错位」（实测 18:14~18:26 连续失败根因）：
    #   SEARCH_INPUT=(180,800) 是**聊天列表页**搜索框；**搜索页打开后输入框在顶部
    #   (~180,200，用户截图 OCR 千分比 200,125)**。搜索框有旧文本时 OCR 找不到
    #   "搜索昵称或聊天记录"占位 → 旧代码回退 tap(SEARCH_INPUT=(180,800)) 点在搜索页
    #   中间 → 焦点没到输入框 → 清空/灌字全失效，框里残留旧词 → 永远"无结果"。
    #   现在：点**搜索页输入框正确位置**聚焦 → 清空 → click=False 灌字 → 截图验证（POAV V）。
    _SP_IN = _sf("input")           # 搜索页顶部输入框（比例坐标；540x960 实测 (221,39)）
    _qry = _type_query(name)
    soul.tap(*_SP_IN)
    time.sleep(0.7)
    try:
        soul.clear_text()
        print("  [POAV] 已清空搜索框残留，再输入")
    except Exception as _e:
        print("  !! 清空搜索框失败: %r" % (_e,))
    soul.type_text(_qry, _SP_IN, click=False)
    time.sleep(1.5)
    soul.screenshot(force=True)
    _box_ocr = [t for t, _, _ in rd.items() if t and t.strip()]
    if not any(_norm_name(_qry) in _norm_name(t) for t in _box_ocr):
        print("  !! 输入后框内未见「%s」（OCR=%r）→ 清空重输一次" % (_qry, _box_ocr[:8]))
        try:
            soul.clear_text()
        except Exception:
            pass
        soul.type_text(_qry, _SP_IN, click=False)
        time.sleep(1.5)
        soul.screenshot(force=True)
    time.sleep(2.8)
    soul.screenshot(force=True)                    # 输入后必取新帧（不复用缓存旧帧）
    _trace(f"输入「{name}」后")
    items = rd.items()
    # 🔴 2026-09-29 修「假性无结果」：旧判据 `_norm_name(t) == name` 是**精确相等**，
    #   实测搜「心中藏」明明有 5 条结果（她界面昵称是「心中藏恶鬼，眼中无良人！」，
    #   库里存的是带老挝文装饰的「心中藏໌້ᮨ恶鬼…」），**没有任何一条精确等于「心中藏」**
    #   → 一律报「无结果 → 放弃」，白白丢掉一个 14 条往来的活跃对象（连续多轮如此）。
    #   改法：精确相等优先；精确没命中才用**子串包含**兜底（宽松模式）。
    #   宽松模式的安全代价必须有补偿：**忽略"有历史就信"，一律强制走 `_verify_by_content()`**
    #   （末句探针 vs 屏幕 OCR，fail-closed），否则会重演「初见」被「若只如初见」冒名的事故。
    exact_rows, loose_rows = [], []
    _core_want = _skeleton(name)
    for t, cx, cy in items:
        # 只认「结果区」的行：高于搜索框（<9% 屏高）或低于可见列表（>85% 屏高，
        # 那里是底导航/悬浮按钮）都不是候选。原写死 150/1150 是 900x1600 时代的数值。
        if not (soul.DEV_H * 0.09 < cy < soul.DEV_H * 0.85):
            continue
        nt = _norm_name(t)
        ct = _skeleton(t)
        if not nt or not name:
            continue
        if nt == _norm_name(name):
            exact_rows.append((cy, t, True))
        elif len(_core_want) >= 2 and ct == _core_want:
            exact_rows.append((cy, t, True))
        elif _norm_name(name) in nt:
            loose_rows.append((cy, t, False))
        elif len(_core_want) >= 2 and _core_want in ct:
            loose_rows.append((cy, t, False))
    exact_rows = sorted(set(exact_rows))
    loose_rows = sorted(set(loose_rows))
    # ⭐ 2026-10-04：候选上限 3 → 6。实测「TeFuir」搜索命中 3 个**同名陌生人**
    #   （uid 500445694/26259132/451352138，全部 0 条往来）→ 被拒；而真正的本人
    #   排在第 4 行，**根本没被检查到**。多查几个候选（每个多花 ~5s，仅搜索兜底通道），
    #   换来"同名里也能找到本人"。安全闸不变（无往来仍一律拒绝）。
    cands = (exact_rows or loose_rows)[:max(6, max_cand)]
    loose_mode = not exact_rows
    if loose_mode and cands:
        print(f"  ⚠ 无精确命中 → 宽松子串模式（{len(cands)} 候选，全部强制内容验证）")
    if not cands:
        print(f"  !! 搜索「{name}」无结果 → 放弃")
        soul.tap(*_sf("cancel"))                # 取消，回列表
        time.sleep(1.5)
        return False
    print(f"  搜索「{name}」命中 {len(cands)} 个候选 → 逐个核对历史")

    hit = None
    for i, (cy, label, exact) in enumerate(cands):
        # ⭐ 2026-10-04 修「长昵称永远未找到」：
        #   原实现写死 `SEARCH_PRIV_BTN_X = 629` + `cy+20` 去点该行「私聊」按钮，
        #   实测（01:52 现场）点了之后仍停在 `RnContainerActivity` → 判"没进会话页"跳过
        #   → 全部候选被否 → 打印「!! 未找到: 175以上的男生请找我聊天」。
        #   注意搜索本身是**命中的**（候选标签完全一致），坏在"进会话"这一步。
        #   改为：① 优先用 OCR 找**同一行**的「私聊」按钮（行 y 距离最近者，限 60px）；
        #        ② 取不到再回落到固定坐标（保证不比原来差）；
        #        ③ 点击后**轮询**等会话页出现（原来只等一次 3.2s，RN 页转场慢就误判）。
        bx, by = _sf("priv_x")[0], cy + 20
        try:
            best = None
            for _t, _cx, _cy2 in rd.items():
                if ("私聊" in _t) or ("打招呼" in _t):
                    d = abs(_cy2 - cy)
                    if best is None or d < best[0]:
                        best = (d, _cx, _cy2)
            if best and best[0] <= 60:
                bx, by = best[1], best[2]
                print(f"    · 用 OCR 定位该行「私聊」按钮 = ({bx},{by})（行 y={cy}）")
        except Exception as _e:
            print(f"    · OCR 定位私聊按钮失败（回退固定坐标）: {_e!r}")
        soul.tap(bx, by)
        act = ""
        for _ in range(4):                       # 轮询最多约 6.4s
            time.sleep(1.6)
            act = soul.activity() or ""
            if "ConversationActivity" in act:
                break
        _trace(f"候选{i+1}·点私聊后")
        if "ConversationActivity" not in act:
            print(f"    候选{i + 1}「{label}」：没进会话页（{act}）→ 跳过")
            continue
        try:
            im.pull()
            nm2 = im.names()
            c = sqlite3.connect(im.IMDB)
            rows = c.execute("SELECT sessionId, toUserId, timestamp FROM session "
                             "ORDER BY timestamp DESC").fetchall()
            c.close()
            uid = None
            for sid, u, ts in rows:                 # 与打开前快照比：timestamp 变大 = 本次刚点开的
                if ts > pre_ts.get(sid, -1):
                    uid = str(u)
                    break
            uid_confident = True
            if uid is None:
                # ⭐ 2026-09-29 修：**绝不再用"全库最新会话"冒名顶替**（见 `_verify_by_content` 注释）。
                #   取不到 timestamp 差异 → uid 未知 → 改用**页面内容**做唯一凭据；验证不过就拒绝。
                uid = _uid_of(name)
                uid_confident = False
            nm2 = nm2.get(uid, "?") if uid else "?"
            n = hist.get(uid, 0) if uid else 0
        except Exception as e:
            print(f"    候选{i + 1}：核对失败 {e!r}")
            uid, n, nm2, uid_confident = None, 0, "?", False
        # ⭐ 宽松子串模式下**不认"有历史就信"**：必须内容验证（防「若只如初见」冒名「初见」）
        if (not uid_confident) or (not exact):
            ok, why = _verify_by_content(name)
            print(f"    候选{i + 1}「{label}」：{'未检出新开会话' if not uid_confident else '宽松模式'} → 页面内容验证{'通过' if ok else '未通过'}（{why}）")
            if not ok:
                soul.tap_back_arrow()           # 证明不了是本人 → 退出，试下一个
                time.sleep(1.8)
                continue
            print(f"    ✅ 判定为本人（页面内容吻合，昵称={nm2}）")
            hit = uid
            break
        print(f"    候选{i + 1}「{label}」：uid={uid} 昵称={nm2} 历史真人消息={n} 条（快照判据·可信）")
        if uid and n > 0:
            print(f"    ✅ 判定为本人（有 {n} 条往来）")
            hit = uid
            break
        soul.tap_back_arrow()                   # 不是本人 → 退出，试下一个
        time.sleep(1.8)
    if hit:
        return True
    # ⭐ 2026-09-29 修「静默误发」（真实事故）：
    #   原来写"没有历史记录可依据 → 只有唯一候选才接受"，实测**把消息发给了陌生人**：
    #   搜「欢欢」→ Soul 返回 uid 389220636（界面昵称「👋」、0 条往来）→ 被当候选接受 →
    #   后续点击落到发送按钮上 → 搜索词「欢欢」被原样发出（DB 可见 06:06:26 一条「欢欢」）。
    #   教训：**无往来 ≠ 是本人**；搜索结果里的同名可能只是模糊匹配或输入框残留。
    #   → 一律拒绝。要跟陌生人开场，走匹配 / 奇遇铃通道（那边有资料卡可核对身份）。
    print(f"  ⛔ {len(cands)} 个候选都无历史 → 拒绝（无法证明是本人，绝不给陌生人发消息）")
    soul.tap(*_sf("cancel"))
    time.sleep(1.5)
    return False


def find(name, pages=None):
    """自适应找人（2026-09-25 提速）：先看当前屏→逐屏下翻；N 屏找不到→滚顶→再逐屏下翻
    目标多数就在当前屏附近，不再每次先盲滚12次到顶
    ⭐ 2026-09-28：pages 默认改读 SOUL_FIND_PAGES（默认 6，行为不变）。
       注意 find() 的兜底会 `_scroll_top()` 回顶再扫 —— 所以**深位置对象（≥6屏）必然找不到**，
       必须临时 `SOUL_FIND_PAGES=30` 放大，否则误报「未找到」"""
    if pages is None:
        pages = FIND_PAGES
    p = _hit(name)
    if p:
        return p
    # ⭐ 2026-09-29 修「未找到」根因：列表可能停在很下面（上一次操作留下的位置），
    #   而**新消息在顶部**（用户指点）→ 先把列表真滚到顶再扫，否则近期会话永远找不到。
    _scroll_top()
    p = _hit(name)
    if p:
        return p
    for _ in range(pages):          # 从顶部往下翻找
        soul.swipe_up()
        time.sleep(0.8)
        p = _hit(name)
        if p:
            return p
    return None


# 底导航四个 tab 的文字区（720x1280 坐标）。选中=青色，未选中=灰 —— 颜色是最可靠判据。
_TAB_BOXES = {
    "星球": (55, 100, 1245, 1280),
    "广场": (230, 275, 1245, 1280),
    "聊天": (480, 522, 1245, 1280),
    "自己": (620, 665, 1245, 1280),
}
# 底导航「聊天」tab 点击坐标（切 tab 用；不能用来回顶，双击回顶在这台 MuMu 不生效）
# ⚠️ 2026-09-30 修：原先这里**自己存了一份** 501,1263（720 时代的数），跟 soul.TAB_CHAT 脱节
#   → _goto_chat_list() 点"回聊天列表"时落到信息流行上 → 反复进别人主页、整轮"无法回到聊天列表"。
#   **坐标只准有一份来源**，统一引用 soul.TAB_CHAT（按 `wm size` 实测 + 屏幕比例换算）。
CHAT_TAB = soul.TAB_CHAT


def _tab_color(name):
    """读该 tab 文字区的非白像素均值 (r,g,b)；全白/取不到 → None。

    2026-09-29 实测（用户指点「选中和没选中 颜色不一样」，一次取样得到干净信号）：
      未选中：星球(169,169,169) 广场(167,167,167) 自己(162,162,162) —— 灰，R≈G≈B
      选中  ：「聊天」(141,232,230) —— 青，G-R=91 B-R=89
    判据：G-R>35 且 B-R>35 → 选中。
    """
    try:
        from PIL import Image
        x0, x1, y0, y1 = _TAB_BOXES[name]
        im = Image.open(soul.SHOT).convert("RGB")
        W, H = im.size
        sx, sy = W / 720.0, H / 1280.0
        px = im.load()
        acc = []
        for y in range(int(y0 * sy), int(y1 * sy)):
            for x in range(int(x0 * sx), int(x1 * sx)):
                r, g, b = px[x, y]
                if r > 235 and g > 235 and b > 235:
                    continue                      # 跳过白底
                acc.append((r, g, b))
        if not acc:
            return None
        n = len(acc)
        return (sum(p[0] for p in acc) // n,
                sum(p[1] for p in acc) // n,
                sum(p[2] for p in acc) // n)
    except Exception as e:
        print(f"  !! tab 取色失败: {e!r}")
        return None


def _tab_sel(name):
    """该 tab 是否**处于选中态**（选中=青，未选中=灰）"""
    c = _tab_color(name)
    if not c:
        return False
    r, g, b = c
    return (g - r) > 35 and (b - r) > 35


def _on_chat_list():
    """判断是否**真的在聊天列表页**（不只是"在主框架"）。

    ⭐ 2026-09-29 两轮踩坑后的最终版：
      第 1 版只验底导航**文案**含「星球」「广场」「聊天」→ 但**「广场」tab 页的底导航一模一样**，
        人在广场页被判成"已在聊天列表" → 跳过导航 → 后续 swipe 全在刷广场 feed
        → 报成"未找到: 某某"（看着像定位 bug，其实是页面不对；用户手动点回列表才暴露）。
      第 2 版改用搜索框文案——能用但仍依赖 OCR。
      **最终版（用户指点「选中和没选中 颜色不一样」）**：主框架 + 底导航「聊天」文字为**选中色**。
      颜色判据不依赖 OCR、不受文案改版影响，最稳。
    """
    if not soul.on_main():
        return False
    soul.screenshot()                              # 取新鲜截图再比色
    return _tab_sel("聊天")


def _page_state():
    """给操作前后打印用：当前究竟在哪一页（显式暴露，不静默猜）"""
    if not soul.on_main():
        return f"非主框架（activity={soul.activity() or '未知'}）"
    soul.screenshot()
    for tab in ("聊天", "广场", "星球", "自己"):
        if _tab_sel(tab):
            return "聊天列表页" if tab == "聊天" else f"主框架·「{tab}」tab"
    try:
        if any("et_sendmessage" in n["rid"] for n in soul.nodes()):
            return "会话页"
    except Exception:
        pass
    return "主框架但页面异常（底导航无选中态）"


def _on_session_of(name):
    """⭐ 防串台硬闸：确认已进入「name」的会话页（2026-09-26 实战教训）
    背景：列表滚动位置被改变 / 用户同时在模拟器上手操时，find() 拿到的坐标会点到**别人**，
    脚本却照样发送 → 把 A 的话发给 B（2026-09-26 00:34 真实事故：给「晚风藏落日香」写的话发进了「白开水」）。
    判据：① 页面**无底导航**（=不在主框架列表）② 顶部标题含 name。

    ⚠️ 2026-09-28 MuMu 版：改用 OCR 读屏（uiautomator 不可用）。
      OCR 有误识风险 → 判据放宽为「标题区(y<200)含 name 且无底导航」，
      宁严不松：两次都不过就判失败（拒绝发送），绝不冒串台风险。
    """
    for i in range(2):
        items = rd.items()
        texts = [t for t, _, _ in items]
        joined = " ".join(texts)
        ok_bar = ("星球" in joined) and ("广场" in joined) and ("聊天" in joined)
        # 只认顶部标题区的名字，避免把聊天内容里的名字误判成标题
        # ⭐ 2026-09-29 补漏：原来只要"标题含 name"就放行 → 目标「初见」时
        #   「若只如初见」的标题也含「初见」→ 闸门放行，消息发错人（真实事故）。
        #   现在：标题里**优先找精确相等**；否则若命中的标题是**库里另一个人的精确昵称** → 判失败。
        titles = [_norm_name(t) for t, _, cy in items if cy < 200 and (name in t)]
        nm_want = _norm_name(name)
        if nm_want in titles:
            title_hit = True
        else:
            # 🔴 2026-09-29 修 fail-open：旧判据是"标题没命中库里别人的**完整**昵称就放行"。
            #    实测事故：目标「心中藏」，标题「心中藏山海～在线」——它不是任何库昵称的**全串**，
            #    于是被当成"OCR 噪声"放行 → 消息发给了陌生人「心中藏山海～」。
            #    现在必须**正面证明**：标题骨架 ⊇ 或 ⊆ 「库里聊过的本人昵称」骨架。
            tgt = _resolve_db_target(name)
            tsk = _skeleton(" ".join(titles))
            _ok, _why = _title_match(tgt, tsk) if (tgt and tsk) else (False, "")
            title_hit = _ok
            if not title_hit:
                print(f"    ⛔ 顶部标题「{titles}」证明不了是「{name}」（库里本人={tgt}）→ 判为串台")
        if (not ok_bar) and title_hit:
            return True
        if i == 0:
            time.sleep(1.2)              # 等页面渲染稳定后再判一次
    # ⭐ 2026-10-04 补「标题 OCR 读空 → 误判串台」：
    #   实测 01:57 目标「175以上的男生请找我聊天」——搜索通道已正确进入她的会话，
    #   `_verify_by_content` 也**已在屏幕上找到该会话原话「经常旅行听」**（正面证据），
    #   但顶部标题区 OCR 读出 `[]`（长昵称 + RN 页转场）→ `title_hit=False`
    #   → 被本闸判"串台"中止，白跑一轮。
    #   现在：标题证不了时，改用**会话内容探针**做正面证据。
    #   ⚠️ 探针必须命中（fail-closed），所以**没有放宽**防串台强度：
    #     屏幕上必须真实出现该会话的原话，否则照样拒绝。
    try:
        _ok_c, _why_c = _verify_by_content(name)
        if _ok_c:
            print(f"    · 顶部标题读不到 → 改用会话内容探针判定本人：{_why_c}")
            return True
        print(f"    · 内容探针也未通过（{_why_c}）")
    except Exception as _e:
        print(f"    · 内容探针异常（按不通过处理）: {_e!r}")
    return False


def verify_sent(name):
    """发完后回 APK 库确认「本次确实有我的消息发出去了」。

    ⚠️ 2026-09-26 修正：旧逻辑要求"最后一条必须是我发的"，但**对方可能在 --wait 等待期间回复**，
    那样最后一条就是她 → 明明发成功了却报「DB 校验失败：最后一条不是我发的」（实测踩到）。
    现在改成：看最近 3 条，只要有我发的就算成功。
    """
    im.pull()
    nm = im.names()
    uid = _uid_of(name)
    if not uid:
        return False
    c = sqlite3.connect(im.IMDB)
    sids = [r[0] for r in c.execute(
        "SELECT sessionId FROM session WHERE toUserId=?", (uid,))]
    if not sids:
        c.close(); return False
    ph = ",".join("?" * len(sids))
    rows = c.execute(
        f"SELECT senderId, text FROM chatmsg WHERE sessionId IN ({ph}) "
        f"ORDER BY localTime DESC LIMIT 3", sids).fetchall()
    c.close()
    # 最近 3 条内有我发的（且是非空文本）即认定发送成功
    return any(str(r[0]) == im.ME and r[1] for r in rows)


def _last_msg(name):
    """返回该会话最后一条消息 (who, text)：who = 'me' / 'her' / None（--wait 用）"""
    nm = im.names()
    uid = _uid_of(name)
    if not uid:
        return None
    c = sqlite3.connect(im.IMDB)
    sids = [r[0] for r in c.execute(
        "SELECT sessionId FROM session WHERE toUserId=?", (uid,))]
    if not sids:
        c.close(); return None
    ph = ",".join("?" * len(sids))
    rows = c.execute(
        f"SELECT senderId, text, msgContent FROM chatmsg WHERE sessionId IN ({ph}) "
        f"ORDER BY localTime DESC LIMIT 10", sids).fetchall()
    c.close()
    # ⭐ 跳过系统推送卡片（text 为空 + clickItems/bgUrl/trackId/wink… 等），
    #   否则会把 Soul 的推送误当成"她发了消息"，连发闸判断就错了。
    SYSK = ("clickItems", "buttonConfig", "jumpUrl", "activityId", "highlightText",
            "tagAuthGuide", "bgUrl", "emotionUrl", "偶遇crush", "Soulmate", "soulmate",
            "trackId", "pushType", "interact-push", "打招呼吧", "想聊天", "打完招呼",
            "emojiInfo", "actionContent", "wink", "Wink", "看了看")
    for sender, text, content in rows:
        if not text and any(k in str(content or "") for k in SYSK):
            continue
        if not text:
            continue                      # 图片/表情也跳过，按"无文本"处理
        return ("me" if str(sender) == im.ME else "her", text)
    return None


def _goto_chat_list():
    """导航回**聊天列表页**并滚到顶部（动作前先把页面摆正）。

    ⭐ 2026-09-29 加固（用户指点根因）：旧版只 `am start --activity-clear-top`
       → 人停在「广场」tab 时，clear-top 会把 App 重置回**它自己的默认 tab**（常常不是聊天），
         于是"回到了主框架"却仍然不在聊天列表 → 后续全错。
    现在两步走：
      ① 已在主框架 → 直接点底导航「聊天」tab 纠正（快、准、不动会话栈）
      ② 仍不在（陷在会话页/WebView）→ 再 clear-top 清栈，回来后补点一次「聊天」tab
    返回 True = 确认已在聊天列表页。
    """
    # ⓪ 「未成年模式」弹窗：每次冷启动/清栈后必弹，盖住底导航 → 先清障
    #   （2026-09-29 根治：此前它导致 _on_chat_list() 误判"页面异常"，reply 全跳过）
    soul.close_kid_popup()
    # ① 主框架内纠 tab
    if soul.on_main() and not _on_chat_list():
        soul.tap(*CHAT_TAB)
        time.sleep(1.8)
    # ② 还不行 → 清栈重启主活动，再补点「聊天」tab
    # ⭐ 2026-10-04：4 → 2。异常期反复 `am start --activity-clear-top` 会猛敲模拟器，
    #   在画面已经不健康时形成正反馈（"越弄越死"）。画面健康闸已在 daemon 层兜底。
    for _ in range(2):
        if _on_chat_list():
            break
        soul.adb("shell", "am", "start", "-n", ACT, "--activity-clear-top")
        time.sleep(2.2)
        soul.close_kid_popup()          # ⭐ 清栈=冷启动 → 弹窗必现，必须再关一次
        if soul.on_main() and not _on_chat_list():
            soul.tap(*CHAT_TAB)
            time.sleep(1.8)
    soul.mark_top(False)        # 切页/清栈后列表位置未知 → 不许复用"已在顶部"
    _scroll_top()                                      # 手势真滚到顶（新消息置顶）
    ok = _on_chat_list()
    if not ok:
        print(f"  !! 导航后仍不在聊天列表页（当前：{_page_state()}）")
    return ok


def _similar(a, b):
    """两段中文的字符重合度（Jaccard），识别『换个说法又发一遍』"""
    sa, sb = set(str(a)), set(str(b))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _my_recent_texts(name, k=6):
    """**她上次开口之后**我发过的真人文本，最多 k 条，新的在前（跨轮重复检测用）。

    🔴 2026-09-29 21:53 事故：旧版只取"我最近一条"→ 重复闸形同虚设。
       实测对「风止遇你」连发：21:47「对 我一年没进过健身房」、21:49「十八梯那段…」、
       她 21:51 回一句、我 21:53 **又发了一遍 21:47 那句**——
       因为 last_mine 只看 21:49 那条，21:47 那句滑出了视野，闸没拦住。
    ⇒ 重复是"跟**这一轮我说过的话**比"，不是跟"字面最后一条"比。
    """
    return _my_last_text(name, k=k, as_list=True)


def _my_last_text(name, k=1, as_list=False):
    """我发给她的最后一条真人文本（跨轮重复检测用）

    `as_list=True` → 返回**列表**（最近 k 条，新的在前），供重复闸逐条比对。
    """
    nm = im.names()
    uid = _uid_of(name)
    if not uid:
        return None
    c = sqlite3.connect(im.IMDB)
    sids = [r[0] for r in c.execute(
        "SELECT sessionId FROM session WHERE toUserId=?", (uid,))]
    if not sids:
        c.close(); return None
    ph = ",".join("?" * len(sids))
    rows = c.execute(
        f"SELECT senderId, text, msgContent FROM chatmsg WHERE sessionId IN ({ph}) "
        f"ORDER BY localTime DESC LIMIT 30", sids).fetchall()
    c.close()
    SYSK = ("clickItems", "buttonConfig", "jumpUrl", "activityId", "highlightText",
            "tagAuthGuide", "bgUrl", "emotionUrl", "偶遇crush", "Soulmate", "soulmate",
            "trackId", "pushType", "interact-push", "打招呼吧", "想聊天", "打完招呼",
            "emojiInfo", "actionContent", "wink", "Wink", "看了看")
    out = []
    for sender, text, content in rows:
        if not text:
            continue
        if any(kw in str(content or "") for kw in SYSK):
            continue
        if str(sender) == im.ME:
            out.append(text)
            if len(out) >= k and not as_list:
                return text
            if as_list and len(out) >= k:
                break
            continue
        break                      # 最近一条真人是她发的 → 我没有"上一条"
    if as_list:
        return out
    return out[0] if out else None


def _topic_stuck_warn(name, texts, lookback=3, min_hits=3):
    """⭐ 2026-09-29 用户纠偏落地：「不要在一个话题上死磕 要引导到自己的目的去聊」

    检测"我是不是在重复同一个话题"——**只告警不阻断**（话术由模型生成，脚本替不了它做判断，
    但必须把"已在死磕"显式喊出来，这是防静默失败的老规矩）。

    判据：待发的这条 + 我在该会话最近 `lookback` 条真人消息里，
          某个 ≥2 字的中文词在 ≥`min_hits` 条里都出现过 → 认定死磕。

    返回告警字符串（没死磕就返回 None）。
    """
    nm = im.names()
    uid = _uid_of(name)
    if not uid:
        return None
    c = sqlite3.connect(im.IMDB)
    sids = [r[0] for r in c.execute(
        "SELECT sessionId FROM session WHERE toUserId=?", (uid,))]
    if not sids:
        c.close(); return None
    ph = ",".join("?" * len(sids))
    rows = c.execute(
        f"SELECT senderId, text FROM chatmsg WHERE sessionId IN ({ph}) "
        f"ORDER BY localTime DESC LIMIT 40", sids).fetchall()
    c.close()
    mine = [t for s, t in rows if t and str(s) == im.ME][:lookback]
    pool = list(texts) + mine
    if len(pool) < min_hits:
        return None

    # ⚠️ 锚点词必须排除在"死磕"统计之外：零锚点 30 人是长期欠账，
    #    转向时**本来就要反复给重庆细节**，把「重庆」当死磕会误伤补锚点策略。
    # ⭐ 2026-10-04 双开：锚点按实例取（实例0 与现网逐字一致；实例>0 = 账号2 城市锚点）
    try:
        from soul_persona import anchors as _anchors
        ANCHOR = _anchors()
    except Exception:
        ANCHOR = {"重庆", "小面", "火锅", "南山", "渝中", "解放碑", "洪崖洞",
                  "鹅岭", "山城", "綦江", "江边", "巷子", "龙门阵",
                  "南滨路", "轻轨", "凉虾", "十八梯", "朝天门", "观音桥", "磁器口",
                  "坡", "梯坎", "江风", "老楼"}
    # 🔴 单字也要一并排除：words() 会把「重庆」拆出「重」「庆」两枚单字，
    #    只排除双字词 → 实测「重」×3、「庆」×3 照样报死磕（本轮 请勿查户口/风止遇你 均误报）。
    ANCHOR_CHARS = set("".join(ANCHOR))

    # 虚词/高频字：只统计它们没有意义（"的""了"会出现在每一条里）
    STOP = set("的了是我在有和就都很也要去个没这那你我他好不啊哦嗯呢吧嘛哈行对说"
               "天时侯候会能还又才真太最什么点些上下里来回做过"
               "一二三四五六七八九十百千万两几多少半"
               "早夜晚今明昨年月日周点钟分")
    # ↑ 前两行是实测噪声：初见「稀饭还得煮几天」被报「天」×3（来自"明天/几天"），
    #   这类时间量词出现在几乎每条消息里，当话题词会**误报**。
    # ↑ 第三行（2026-09-29 21:40 轮补）：数词/量词同样遍地都是——
    #   实测「我就住坡上 上下班腿比健身房狠」被报「一」×3（来自"一晚/一趟"），纯噪声。
    def words(s):
        """话题词 = 2-gram + 单个实词字。

        ⚠️ 只取 2-gram 会漏掉单字话题（实测「蛋」在「四个蛋」「鸡蛋」里都是单字出现，
        2-gram 抓不到 → 死磕告警形同虚设）。所以补单字，但用 STOP 滤掉虚词噪声。
        """
        out = []
        for seg in re.findall(r"[\u4e00-\u9fa5]+", str(s)):
            for i in range(len(seg) - 1):
                out.append(seg[i:i + 2])
            for ch in seg:
                if ch not in STOP and ch not in ANCHOR_CHARS:
                    out.append(ch)
        return set(out)

    from collections import Counter
    cnt = Counter()
    for t in pool:
        for w in words(t):
            cnt[w] += 1
    # ⚠️ 锚点词必须排除在"死磕"统计之外：零锚点 30 人是长期欠账，
    #    转向时**本来就要反复给重庆细节**，把「重庆」当死磕会误伤补锚点策略。
    hot = [(w, n) for w, n in cnt.items() if n >= min_hits and w not in ANCHOR]
    if not hot:
        return None
    # 双字词比单字更有信息量（「蛋」×3 不如「鸡蛋」×3 直观），同频次优先展示双字
    hot.sort(key=lambda x: (-x[1], -(len(x[0]) == 2)))
    top = "、".join(f"「{w}」×{n}" for w, n in hot[:3])
    return (f"我在「{name}」最近 {len(pool)} 条里反复提到 {top} "
            f"—— 同一个话题已经聊了 {len(mine)} 轮，该转向了")


def _my_burst_gate(name):
    """⭐ 2026-09-29 新增：防"她没回我还连追"的过热闸。

    真实教训（本轮实测）：对「风止遇你」**10 分钟内发了 4 条** ——
      01:35 「你要真嫌 那分我五斤 我这儿正好缺」
      01:36 「早点睡 明天别又只喝稀饭」
      (她 01:36 回「分你10斤都行」)
      01:40 「快睡 明天那碗稀饭加个蛋」
      01:43 「成交 十斤都记你账上 别赖」   ← 顺序还错乱了（重试导致），读起来语无伦次
    旧闸只拦"同一批里的第 2 条"，我分 4 次调用就绕过去了。

    ⚠️ 2026-09-29 二次修正：**第一版按"24h 总条数"算，是错的** ——
       它把「蓝朋友呀」（24h 我发 27 条）这种**她一直在回的活跃对话**也拦了。
       活跃对话本来就该你来我往，条数多不是问题。
       **真正的病征只有一个：她没回，我还在追。**
    所以两个判据（任一命中即拦）：
      ① 她上次开口之后，我连发了几条（未答连追）≥ MAX_UNANSWERED
      ② 距我上一条时间 < MIN_BATCH_GAP_MIN **且** 她在那之后没说过话
    返回 (放行?, 原因)。
    """
    try:
        import soul_im as _im, sqlite3
        _im.pull()
        nm = _im.names()
        uid = next((k for k, v in nm.items() if v == name), None) \
            or next((k for k, v in nm.items() if name and name in str(v)), None)
        if not uid:
            return True, ""
        c = sqlite3.connect(_im.IMDB)
        row = c.execute("SELECT sessionId FROM session WHERE toUserId=?", (uid,)).fetchone()
        if not row:
            c.close()
            return True, ""
        raw = c.execute("SELECT senderId,text,msgContent,localTime FROM chatmsg "
                        "WHERE sessionId=? ORDER BY localTime ASC", (row[0],)).fetchall()
        c.close()
        real = [(str(s), t, lt) for s, t, ct, lt in raw
                if not _im._is_sys(t, ct) and t]
        mine = [(t, lt) for s, t, lt in real if s == _im.ME]
        hers = [(t, lt) for s, t, lt in real if s != _im.ME]
        if not mine:
            return True, ""
        her_last_lt = hers[-1][1] if hers else 0
        # ① 她上次说话之后，我连发了几条
        streak = sum(1 for _, lt in mine if (lt or 0) > (her_last_lt or 0))
        if streak >= MAX_UNANSWERED:
            return False, (f"她上一条之后我已连发 {streak} 条（上限 {MAX_UNANSWERED}）"
                           f"→ 停手，等她先开口")
        # ② 距我上一条太近，且她没回
        my_last_lt = mine[-1][1] or 0
        gap = (time.time() * 1000 - my_last_lt) / 60000.0
        if gap < MIN_BATCH_GAP_MIN and (my_last_lt > (her_last_lt or 0)):
            return False, (f"距上一条只过了 {gap:.1f} 分钟（下限 {MIN_BATCH_GAP_MIN} 分钟）"
                           f"且她还没回 → 太快，等人回")
        return True, ""
    except Exception as e:
        print(f"  !! 频率闸查询失败（保守放行）: {e!r}")
        return True, ""


def reply(name, texts, verify_db=True, wait=0, allow_chain=False):
    soul.connect()
    # ⭐ 2026-10-04 计时标记：发送链路排障用，每步耗时看时间戳间隔
    print(f"=== reply({name}) 开始 texts={texts!r} allow_chain={allow_chain}")
    # ⭐ 2026-09-29 前置清障：确保 Soul 在前台 + 清掉遮挡弹窗。
    #   背景：奇遇铃会不定时弹出并盖住聊天列表，此时 OCR 找不到目标会话，
    #   表现成"未找到: 某某"（实测踩到）；而 Soul 被切后台时 display 检测会失败。
    #   放在最前面，任何后续定位都建立在一个干净的前台上。
    soul.ensure_ready()
    # ⭐ 2026-09-29：动作前**显式打印当前页面**（用户要求「操作之前你要先看看自己在哪个页面」）
    print(f"  [页面] 操作前：{_page_state()}")
    # ⭐ 更新全局锁心跳：让"上一轮是否还在跑"判断得出来（跨实例互斥用）
    try:
        import soul_global_lock as _GL
        _GL.touch(f"reply:{name}")
    except Exception:
        pass
    # ⭐ 长度预检（2026-09-28）：不合格就地打回，根本不进 UI（不动屏幕、不占锁）
    bad = [(t, vis_len(t)) for t in texts if vis_len(t) > REPLY_MAXLEN]
    if bad:
        for t, n in bad:
            print(f"⛔ 长度硬闸：{n} 字 > 上限 {REPLY_MAXLEN} 字 —— 聊天不是小作文。原句：{t}")
        print(f"⛔ 本轮不发，拆成 2~3 条短句（每条 ≤20 字居多）再调")
        return False
    # ⭐ 跨轮重复闸（2026-09-27）：她没回，隔几小时换个字又发同一句话 = 尬聊。
    #   连发闸只管同一轮，这条管跨轮。覆盖率 >0.5 判定为重复，直接拒发。
    # 🔴 比对范围＝我这一轮说过的**所有**话（最近 6 条），不是只比字面最后一条
    #   （2026-09-29 事故：只比一条 → 隔一条重发老句子，闸直接放行）
    # ⚠️ 同名多人时 `_uid_of()` 拒绝猜 → 返回 None（如「可爱的小猫」3 个同名），
    #    这里必须兜成 []，否则 `for mine in None` 直接 TypeError（2026-09-29 实测）
    recent_mine = _my_recent_texts(name, k=6) or []
    for t in texts:
        for mine in recent_mine:
            r = _similar(t, mine)
            if r > 0.5:
                print(f"⛔ 重复拦截：「{t[:22]}」与我这条「{str(mine)[:22]}」"
                      f"重合 {r:.0%}（最近 {len(recent_mine)} 条内的重发）"
                      f"→ 不发，换个新话题或等人回")
                return False
    # ⭐ 频率闸（2026-09-29）：同一人 24h 条数上限 + 两批最小间隔（防"过热连追"）
    _ok, _why = _my_burst_gate(name)
    if not _ok:
        print(f"⛔ 频率闸拦截：{_why}")
        return False
    # ⭐ 死磕告警闸（2026-09-29 用户纠偏：「不要在一个话题上死磕 要引导到自己的目的去聊」）
    #   **只告警不阻断**——话术由模型生成，脚本无法替它做判断，但必须把"已在死磕"显式喊出来。
    #   判据：本次要发的这条 + 我在该会话最近 3 条真人消息里，某个 ≥2 字中文词出现 ≥3 次。
    #   实测死磕现场：风止遇你「鸡蛋」连了 6 轮、初见「稀饭」5 轮、漩涡鸣人「小说」4 轮。
    try:
        _warn = _topic_stuck_warn(name, texts)
        if _warn:
            print(f"⚠️⚠️ 死磕告警：{_warn}")
            print(f"     → 按「转向铁律」：接住她那句后，**借势落到重庆具体细节**（别硬拐）")
    except Exception as _e:
        print(f"⚠️ 死磕检测异常（不影响发送）：{type(_e).__name__}: {_e}")
    # 双守卫：既要在聊天列表页（底导航 + 搜索框），又要确是主框架 Activity（非 WebView/广场杂页）
    if not _on_chat_list() or not soul.on_main():
        print(f"  [页面] 不在聊天列表 → 导航中…")
        _goto_chat_list()
        # 二次确认：若仍不在，再救一次
        if not _on_chat_list():
            print("!! 页面异常，重试导航")
            _goto_chat_list()
        print(f"  [页面] 导航后：{_page_state()}")
        if not _on_chat_list():
            print(f"!! 无法回到聊天列表页（当前：{_page_state()}）→ 本次不发，「{name}」跳过")
            return False
    print(f"  [trace] find({name}) 开始（列表扫描+滚顶，最坏 40s+）")
    pos = find(name)
    if not pos:
        # ⭐ 2026-09-29：列表里没有渲染行（末条是卡片/图/语音的会话不渲染）→ 走搜索框兜底
        print(f"  [trace] find() 未命中 → 改走搜索框通道")
        print(f"  「{name}」在聊天列表里没有渲染行 → 改走搜索框通道")
        if not find_by_search(name):
            print("!! 未找到:", name)
            return False
    else:
        print("进入会话:", name, pos)
        # ⭐ 2026-09-29 新增护栏（真实事故后加）：
        #   误触事故：某次 tap 时页面其实**已经不是聊天列表**（列表已滚动/页面已切换），
        #   行坐标 (x,y) 落在了**会话页顶部的「关注后可邀请通话」按钮**上 →
        #   Soul 以我的名义发出 messageType="follow_and_invite_call" 的通话邀请卡，
        #   对方看到后问「你给我打语音了？」（真实发生，2026-09-29 02:29，对象：委委佗佗）。
        #   原有 _on_session_of 只能拦住"发错人"，拦不住"点错按钮"这种**点击副作用**。
        #   → 点行坐标之前，再确认一次"我还在聊天列表页"；不在就放弃本次（不进 UI）。
        if not _on_chat_list():
            print("!! 点行前页面已变（不在聊天列表页）→ 中止本次点击，避免误触会话页按钮")
            return False
        soul.tap(*pos)
        time.sleep(2.5)
    # ⭐ 防串台硬闸：点进去后必须确认标题就是目标人，否则中止（绝不盲发）
    if not _on_session_of(name):
        print(f"!! 串台拦截：点击后未进入「{name}」会话，已中止发送"
              f"（列表滚动/并发操作所致）；请复查当前页面")
        soul.tap_back_arrow()
        return False
    sent = 0
    # ⭐ 连发闸（2026-09-26 用户定）：**她没回复就不要发第二条**
    #   首条发出后，只有在她确实回了的情况下才允许继续发下一条；否则当场截断。
    for i, t in enumerate(texts):
        if i > 0:
            if allow_chain:
                pass   # ⭐ 2026-10-03 拆分条豁免连发闸（字数多拆两条发）
            else:
                im.pull()
                r = _last_msg(name)
                if r is None:
                    pass                       # 新会话（此前无真人消息）→ 允许
                elif r[0] == "me":
                    print(f"⛔ 连发拦截：她还没回（末条仍是我发的「{str(r[1])[:20]}」）→ "
                          f"**不再发第二条**，等她回了再说")
                    break
        # ⭐ 2026-09-26 修正：send_msg 抢不到发送锁会返回 False（＝不发）。
        # 旧版不检查返回值照样 db.log → 库里记了"我发过"、对方其实没收到，属静默失败。
        print(f"  [trace] send_msg 第{i + 1}/{len(texts)} 条开始：{str(t)[:18]}")
        if not send_msg(t, maxlen=REPLY_MAXLEN):
            print(f"!! 发送失败（长度超限 / 抢不到发送锁 / 并发实例）→「{t}」未发出，"
                  f"**本条不写库**，需人工核对后重试")
            break
        db.log(name, "me", t)
        db.bump(name, 1)
        sent += 1
        print("已发:", t)
        time.sleep(0.8)

    if sent and wait:
        print(f"--- 静默等待 {wait}s，看有没有回 ---")
        time.sleep(wait)
        im.pull()
        r = _last_msg(name)
        if r and r[0] == "her":
            print(f"✅ 她回了: {r[1]}")
            db.log(name, "her", r[1])
        elif r and r[0] == "me":
            print("（等待期内无回复；消息已确认落地 IM 库）")
        else:
            print("？拉库后没读到该会话，请人工核对是否真发出")

    time.sleep(0.8)
    soul.tap_back_arrow()
    print("已返回列表")
    if sent == 0:
        # ⭐ 2026-09-28：旧版此处照样 return True → 明明一条没发却报成功（静默失败）。
        print("!! 本次 0 条发出（长度闸 / 发送锁 / 连发闸拦截）→ 返回失败")
        return False
    if verify_db:
        time.sleep(1.5)
        print("  [trace] verify_sent() 开始（im.pull + DB 指纹比对）")
        if not verify_sent(name):
            print("!! DB 校验失败：最后一条不是我发的，需重试")
            return False
    return True


def _last_sender_is_me(name):
    """不重新 pull 的轻量校验：最后一条是不是我发的（批量校验用，im.pull 由调用方统一做一次）"""
    nm = im.names()
    uid = _uid_of(name)
    if not uid:
        return False
    c = sqlite3.connect(im.IMDB)
    sids = [r[0] for r in c.execute(
        "SELECT sessionId FROM session WHERE toUserId=?", (uid,))]
    if not sids:
        c.close(); return False
    ph = ",".join("?" * len(sids))
    r = c.execute(
        f"SELECT senderId FROM chatmsg WHERE sessionId IN ({ph}) "
        f"ORDER BY localTime DESC LIMIT 1", sids).fetchone()
    c.close()
    return r is not None and str(r[0]) == im.ME


def reply_batch(pairs, verify_db=True):
    """⭐ 批量回复（2026-09-25 提速）：一次导航，循环回多人；批次末统一 pull+DB 校验一次。
    pairs = [(昵称, ["文本1", ...]), ...]
    相比逐人跑 reply()：省掉每人的重复导航/重连/pull，5人从 ~4min → ~1.5min"""
    soul.connect()
    if not _on_chat_list() or not soul.on_main():
        _goto_chat_list()
        if not _on_chat_list():
            print("!! 页面异常，重试导航")
            _goto_chat_list()
    results = {}
    for name, texts in pairs:
        bad = [(t, vis_len(t)) for t in texts if vis_len(t) > REPLY_MAXLEN]
        if bad:
            for t, n in bad:
                print(f"⛔ 长度硬闸：{n} 字 > {REPLY_MAXLEN} —— 跳过「{name}」。原句：{t}")
            results[name] = False
            continue
        pos = find(name)
        if not pos:
            print("!! 未找到:", name)
            results[name] = False
            continue
        print("进入会话:", name, pos)
        if not _on_chat_list():
            print("!! 点行前页面已变（不在聊天列表页）→ 中止本次点击，避免误触会话页按钮")
            results[name] = False
            continue
        soul.tap(*pos)
        time.sleep(2.2)
        # 会话页守卫：必须有输入框（否则没进去/串页），且命中付费弹窗立即中止整批
        soul.dump(); ns = soul.nodes()
        if not any("et_sendmessage" in n["rid"] for n in ns):
            print("!! 未进会话页，跳过", name)
            results[name] = False
            soul.tap(50, 105); time.sleep(1.5)
            continue
        if any(any(k in (n["text"] or "") for k in
                     ("立即购买", "充电宝", "超级星人", "开通", "¥")) for n in ns):
            print("!! 命中付费弹窗，中止整批，需人工检查")
            results[name] = False
            break
        sent_any = False
        for t in texts:
            if not send_msg(t, maxlen=REPLY_MAXLEN):   # ⭐ 抢不到发送锁/超长 → 不发也不写库
                print(f"!! 发送失败（抢不到发送锁）→「{t}」未发出，本条不写库")
                break
            db.log(name, "me", t)
            db.bump(name, 1)
            sent_any = True
            print("已发:", t)
            time.sleep(0.6)
        soul.tap(50, 105)               # 显式返回列表，接着回下一人（不重导航）
        time.sleep(1.8)
        results[name] = sent_any
    if verify_db:                        # 批次末统一校验一次（每人单独 pull 要 ~6s）
        time.sleep(1.0)
        im.pull()
        for name, ok in list(results.items()):
            if ok and not _last_sender_is_me(name):
                print("!! DB 校验失败:", name)
                results[name] = False
    return results


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    a = sys.argv[1:]
    if not a or a[0] == "--scan":
        scan()
    else:
        name, texts, wait, i = a[0], [], 0, 1
        while i < len(a):
            if a[i] == "--wait" and i + 1 < len(a):
                wait = int(a[i + 1]); i += 2
            else:
                texts.append(a[i]); i += 1
        reply(name, texts, wait=wait)
