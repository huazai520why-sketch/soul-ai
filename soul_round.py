# -*- coding: utf-8 -*-
"""Soul 轮次编排器 —— 把自动化 prompt 里那套流程固化成一条命令

【为什么要有这个脚本】
自动化 prompt 里写着"先抢锁 → 连设备 → 时段判断 → 重启 Soul → 取未读/待回 → ..."，
靠 Agent 每次手动串起来执行，规则会被漏掉（2026-09-26 实测：写了"23:30~08:00 只回不撩"，
凌晨仍发起了 51 个新会话）。把准备阶段固化成脚本，Agent 只需要做"回复内容"这一件事。

用法:
  python soul_round.py start     # 准备本轮：抢锁 → 连设备 → 时段判断 → 重启Soul → 拉库 → 打印待回清单
  python soul_round.py budget    # 看时长账：已跑多久 / 距保底还差多少 / 距上限还剩多少
  python soul_round.py idle      # 没事干时驻留：等回复 or 等满保底时长（见下方退出码）
  python soul_round.py touch     # 刷新锁心跳（单轮跑得久时调用，防止被误判为死锁）
  python soul_round.py end       # 收工：释放锁（**未满保底会拒绝收工**）
  python soul_round.py end --force  # 强制收工（仅设备掉线/异常中断等真急情况用）
  python soul_round.py lock      # 只看锁状态

⭐ 单轮时长铁律（2026-09-26 用户定）：
    保底 MIN_ROUND=30 分钟，最长 MAX_ROUND=60 分钟。
    · 未满 30 分钟调 end → **拒绝收工（exit 8）**，继续干活；
    · 满 60 分钟 → 强制收工，不再续；
    · 中间没事干 → 用 `idle` 挂着等回复，别直接收工。

退出码:
  0 正常可继续 ｜ 9 抢不到锁（**本轮只读库、不碰屏幕**） ｜ 1 设备连不上
  idle: 2=有人回复了(去回) ｜ 3=已达60分钟上限(必须收工) ｜ 0=已跑满保底(可收工)
  end : 8=未满保底被拒绝

⚠️ 抢不到锁时不要绕过本脚本手动操作 —— 那正是 2026-09-26 02:32 消息发重事故的成因。
"""
import sys, io, os, time, json, subprocess, sqlite3
from datetime import datetime

sys.path.insert(0, r"E:\soul")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import soul
import soul_lock
import soul_im as I

# ⚠️ 2026-09-28 迁移 MuMu：雷电已卸载，ldconsole 不复存在。
#    改用 mumu-cli 拉起/查询模拟器（免端口，不依赖 adb）。
MUMU_CLI = r"D:\MuMuPlayer\nx_main\mumu-cli.exe"
VMINDEX = "0"


def _launch_emulator():
    """拉起 MuMu 模拟器（取代雷电时代的 ldconsole launch）"""
    try:
        subprocess.run([MUMU_CLI, "control", "-v", VMINDEX, "launch"],
                       capture_output=True, timeout=120,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
        return True
    except Exception:
        return False


# ⭐ 时段两档（2026-09-25 用户定，SKILL.md 第 174 行起）：
#   白天 08:00~19:00 → 只回复，禁止一切主动搭讪/匹配/奇遇铃
#   晚上 19:00~次日08:00 → 完整流程，允许灵魂匹配/奇遇铃
#   旧的「23:30~08:00 禁匹配」三档规则已作废，以本表两档为准。
DAY_START = 8 * 60                          # 08:00 白天档起点
DAY_END   = 19 * 60                         # 19:00 白天档终点（含 19:00 起算晚上）
ROUND_STALE = 3900                          # 轮次锁有效期 65 分钟（单轮最长 1 小时）
# ⭐ 单轮时长铁律（2026-09-26 用户定）：保底 30 分钟，最长 60 分钟
MIN_ROUND   = 30 * 60                       # 保底 1800s：没跑满不许收工
MAX_ROUND   = 60 * 60                       # 上限 3600s：到点强制收工
IDLE_POLL   = 60                            # ⭐ 轮询间隔（秒）—— 用户 2026-09-26："每隔一分钟检测一下"
STATE       = r"E:\soul\.soul_round_state.json"   # 本轮计时状态
SEEN        = r"E:\soul\.soul_seen.json"          # 「新消息」水位线：{昵称: 她最后发言时间戳}
NEWMSG      = r"E:\soul\.soul_newmsg.json"        # watch 后台写入的新消息队列
NEWMSG_SEEN = r"E:\soul\.soul_newmsg_seen.json"   # ⭐ agent 侧消费队列的指纹集合（与 SEEN 分开，避免和 watcher 抢水位线）
OFFICIAL = ("我的遇见", "系统通知", "官方号消息")


def _mmss(sec):
    sec = max(0, int(sec))
    return f"{sec // 60}分{sec % 60:02d}秒"


# ── 轮次计时状态 ───────────────────────────────────────────────
def _save_state(d):
    try:
        with open(STATE, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except Exception as e:
        _p(f"  [warn] 轮次状态写入失败: {e!r}")


def _load_state():
    try:
        with open(STATE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def elapsed():
    """本轮已跑秒数（无状态则视为 0）"""
    st = _load_state()
    t0 = st.get("start_ts")
    return (time.time() - t0) if t0 else 0.0


def _min_round():
    """本轮保底时长（短轮模式 --quick 时为 0）"""
    st = _load_state()
    return st.get("min", MIN_ROUND) if st else MIN_ROUND


def _p(msg=""):
    print(msg)
    sys.stdout.flush()


def is_official(name):
    n = str(name)
    if n in OFFICIAL:
        return True
    d = n.replace("-", "").replace("_", "").replace(" ", "")
    return d.isdigit() or n.startswith("204268")


def connect_device():
    """确认 MuMu 里的 Soul 可用；不可用则尝试拉起模拟器后再确认一次。

    ⚠️ 2026-09-28 MuMu 版：不再有 adb connect/get-state ——
    MuMu 不向 Windows 暴露 adb 端口，一切走 mumu-cli 的免端口通道。
    这里把「连通性」重新定义为**能读到 Soul 进程 + 能定位到它的 display**。
    """
    for i in (1, 2):
        try:
            # 先看模拟器在不在跑
            info = soul.cli("info", "-v", VMINDEX, timeout=25)
            if '"is_process_started": true' not in info and '"is_process_started":true' not in info:
                _p(f"  第 {i} 次：模拟器未启动")
            elif not soul.app_running():
                _p(f"  第 {i} 次：Soul 未运行 → 启动它")
                soul.launch_app(wait=12)
            d = soul.display(refresh=True)
            if soul.app_running() and d is not None:
                return True
            _p(f"  第 {i} 次连接失败 (soul_running={soul.app_running()}, display={d})")
        except Exception as e:
            _p(f"  第 {i} 次连接异常: {e!r}")
        if i == 1:
            _p("  → 尝试拉起 MuMu 模拟器，等待 60 秒…")
            _launch_emulator()
            time.sleep(60)
    return False


def _db_status():
    """读本地档案状态（JSON `soul_notes.json`，本地 SQLite 库 2026-09-26 已废弃）"""
    try:
        import soul_db
        return soul_db.status_map()
    except Exception:
        return {}


CLOSERS = ("晚安", "睡了", "好", "嗯", "哦", "好的", "哈哈", "行")

# ⭐ 系统卡片特征（2026-09-26 实测）：Soul 的系统推送卡片 **senderId 挂在对方 ID 下**，
# text 为空、msgType=27/35，内容含 clickItems/jumpUrl 等。
# 不过滤的话会把"系统提示"误判成"她发了图片"，凭空多出一条待回任务。
SYS_CARD_KEYS = ("clickItems", "buttonConfig", "jumpUrl", "activityId",
                 "highlightText", "tagAuthGuide", "bgUrl", "emotionUrl",
                 "偶遇crush", "Soulmate", "soulmate")


def _is_system_msg(text, content):
    """text 非空 = 真人文本消息；text 为空时按内容特征判是否系统卡片。

    2026-09-26 实测三种系统卡片（senderId 常挂在**对方 ID** 下，极易被误判成"她发的"）：
      · tagAuthGuide  —— 认证引导
      · type=35 + bgUrl + "[心满意足]是你今日的偶遇crush哦" —— 「今日奇遇」卡片
      · type=35 + clickItems/速看 —— 「恭喜解锁 Soulmate 记忆」
    另外 type=27 的按钮卡片也在此列。
    """
    if text:
        return False
    return any(k in str(content or "") for k in SYS_CARD_KEYS)


def collect_pending():
    """拉库 → 返回真人会话列表（含最后发言方）；已放弃/官方/系统卡片的自动剔除"""
    I.pull()
    nm = I.names()
    status = _db_status()
    c = sqlite3.connect(I.IMDB)
    rows = c.execute("SELECT sessionId, toUserId, unReadCount, timestamp FROM session "
                     "ORDER BY timestamp DESC").fetchall()
    out = []
    for sid, uid, unread, ts in rows:
        name = nm.get(str(uid), str(uid))
        if is_official(name):
            continue
        if status.get(name) in ("stopped", "skipped", "gift"):
            continue                      # 已明确放弃/需付费的，不再打扰
        # 取「最近一条真实消息」：逐条往前跳过系统卡片，避免把系统提示当成人发言
        raw = c.execute("SELECT senderId, text, msgContent, localTime FROM chatmsg "
                        "WHERE sessionId=? ORDER BY localTime DESC LIMIT 8", (sid,)).fetchall()
        real = None
        for sender, text, content, lt in raw:
            if _is_system_msg(text, content):
                continue
            real = (sender, text, lt)
            break
        if not real:
            continue                      # 整个会话只有系统消息 → 忽略
        sender, text, lt = real
        body = str(text)[:32] if text else "(图片/表情)"
        out.append({
            "name": name,
            "unread": unread or 0,
            "mine": str(sender) == I.ME,
            "text": body,
            "last": I._fmt(lt),
            "ts": ts or 0,
            "mts": lt or 0,          # 她最后一条真实消息时间戳（新消息水位线用）
            "closer": bool(text) and len(str(text)) <= 4 and str(text) in CLOSERS,
        })
    c.close()
    out.sort(key=lambda z: z["ts"], reverse=True)
    # ⭐ 重名检测：emoji / 短昵称极易撞（2026-09-26 实测有两个「🍀」，
    # 一个是水瓶座新会话、一个是处女座老对话，模糊匹配会串台）→ 标出来，回复前先核对
    from collections import Counter
    dup = Counter(p["name"] for p in out)
    for p in out:
        p["dup"] = dup[p["name"]] > 1
    return out


def start():
    t0 = datetime.now()
    _p("=" * 64)
    _p(f"  SOUL 轮次准备   {t0.strftime('%Y-%m-%d %H:%M:%S')}")
    _p(f"  单轮时长铁律：保底 {_mmss(MIN_ROUND)}，最长 {_mmss(MAX_ROUND)}")
    _p("=" * 64)

    # ── 1/5 抢轮次所有权 ───────────────────────────────────────
    # ⚠️ 轮次锁必须写在**独立文件** .soul_round.lock，不能跟 send 的短锁共用，
    # 否则整个轮次期间 send 永远抢不到锁 → 一条都发不出去（2026-09-26 实测踩中）。
    token = soul_lock.round_acquire(stale=ROUND_STALE)
    if not token:
        holder = soul_lock._read_round()
        _p(f"!! 抢不到轮次锁 —— 已有实例在跑: {holder}")
        _p("   → 本轮【只读数据库、不碰屏幕】，把这一情况写进汇报。")
        _p("   → 若确认是上一轮崩溃留下的死锁，先执行: python soul_lock.py release")
        _p("=" * 64)
        sys.exit(9)
    _p(f"[1/5 锁]    OK  tag=round  pid={os.getpid()}  有效期 {ROUND_STALE}s")
    # ⭐ --quick：短轮模式（7×24 高频唤醒用）—— 有活就干、干完就走，**不做 30 分钟保底**。
    #   保底 30 分钟是"每小时一次、别太快收工"场景下定的；
    #   改成每 20 分钟唤醒一次后，累计在线时长本来就够，再强制 30 分钟只会让实例堆积撞车。
    quick = "--quick" in sys.argv
    _save_state({"start_ts": time.time(), "pid": os.getpid(), "token": token,
                 "min": 0 if quick else MIN_ROUND,
                 "max": MAX_ROUND, "quick": quick})
    if quick:
        _p("  [短轮] min=0 → 干完即可收工，不受 30 分钟保底约束")

    # ── 2/5 连设备 ────────────────────────────────────────────
    if not connect_device():
        _p("[2/5 设备]  连接失败 → 结束本轮（不重试，避免空转）")
        soul_lock.release()
        _p("=" * 64)
        sys.exit(1)
    _p("[2/5 设备]  OK  127.0.0.1:5555")

    # ── 3/5 时段硬判断（两档：白天只回复 / 晚上完整流程）──────────
    minutes = t0.hour * 60 + t0.minute
    is_day = DAY_START <= minutes < DAY_END          # 08:00~19:00 白天档
    allow_match = not is_day                         # 白天禁搭讪；晚上完整流程
    if is_day:
        _p(f"[3/5 时段]  {t0.strftime('%H:%M')} → 白天档(08:00~19:00)"
           f"【只回复，禁止一切主动搭讪 / 匹配 / 奇遇铃】")
    else:
        _p(f"[3/5 时段]  {t0.strftime('%H:%M')} → 晚上档(19:00~次日08:00)"
           f"完整流程，允许灵魂匹配 / 奇遇铃等主动搭讪")

    # ── 4/5 重启 Soul ─────────────────────────────────────────
    soul.restart_app()
    time.sleep(6)
    # ⭐ 2026-09-26 实测：重启后 App 常停在**广场页**（上次退出时的页面），
    # 不摆回聊天列表的话，后续定位人全靠 soul_reply 内部兜底导航，多花时间。
    try:
        import soul_reply as R
        R._goto_chat_list()
        _p("[4/5 重启]  Soul 已重启，并已摆回聊天列表")
    except Exception as e:
        _p(f"[4/5 重启]  Soul 已重启（导航列表失败: {e!r}，后续脚本会自行兜底）")

    # ── 5/5 拉库 + 待回清单 + 可推进续聊 ───────────────────────
    pend = collect_pending()
    waiting = [p for p in pend if not p["mine"]]
    _p(f"[5/5 拉库]  已同步 IM 库｜真人会话 {len(pend)} 个，"
       f"其中【她最后发言、等你回】{len(waiting)} 个")

    _p("")
    _p("---------- ①需要回复的人（未读 + 已读未回，全数据库口径）----------")
    if waiting:
        for p in waiting:
            flag = f"未读{p['unread']}" if p["unread"] else "已读"
            # 2026-09-26 用户口径：**已读未回也算待回**，不因为"不是未读"就跳过；
            # 收尾语(晚安/好的)不复读，改用「新话题 + 自己状态」自然接一句。
            tip = "   ← 收尾语，用新话题接（别复读晚安）" if p["closer"] else ""
            warn = "   ⚠ 重名，回复前先核对是谁" if p.get("dup") else ""
            _p(f"  [{flag:>5}] {p['name']:<20} 她: {p['text']}   ({p['last']}){tip}{warn}")
    else:
        _p("  （无 —— 没有人在等你说话）")

    # ② 可推进续聊：她回过 ≥8 条 + 我最后发言 + 冷了 ≥12h（2026-09-26 用户定）
    try:
        fol = I.follow()
    except Exception as e:
        fol = []
        _p(f"  （follow 查询失败: {e!r}）")
    _p("")
    _p("---------- ②可推进续聊（她回≥8条 · 冷却≥12h）----------")
    if fol:
        for idle_h, name, hers, total, last_text, lt in fol[:8]:
            _p(f"  {name:<20} 合计 {total:>3} 句(她{hers:>3}) | 已冷 {idle_h:.1f}h | 我最后说: "
               f"{str(last_text)[:26]}")
    else:
        _p("  （无 —— 没有够格且冷够 12 小时的人）")

    _p("")
    _p("---------- 下一轮动作（⚠ 本轮至少跑满 30 分钟才允许收工）----------")
    _p('  1) 回复上面的人（未读优先；**已读未回的也要回**）:')
    _p('     python soul_reply.py "昵称" "内容" --wait 25')
    _p("  ⭐ **每回完一个立刻查新消息**（用户要求：每隔一分钟检测，有就赶紧回）:")
    _p("     python soul_round.py new       # 增量检测，有就回，没有继续")
    _p("     python soul_round.py watch     # 或另开终端常驻，每 60s 自动检测并写 .soul_newmsg.json")
    _p("  2) 回完还有时间 → 做②续聊（冷 12h 以上的老熟人，用新话题开口）")
    _p("  3) 还有时间 → 主动找新人: 星球页「开始匹配」(123,461) / 奇遇铃(需先 grep 坐标)")
    _p("  4) 再查一遍有没有新消息 → 有就回，没有继续下一步")
    _p("  5) 还是没有 → 逛广场（⚠ 实测：评论/私聊在 PostDetailActivity 里 adb 点不动，别硬试）")
    _p("  6) 没人可回时挂着等: python soul_round.py idle   （等到有人回 or 跑满保底）")
    _p("  7) 随时查时长账: python soul_round.py budget     ｜ 续聊清单: python soul_im.py follow")
    _p("  8) 收工: python soul_round.py end   （未满 30 分钟会被拒绝，exit 8）")
    _p("=" * 64)
    return 0


def budget():
    """打印本轮时长账：已跑 / 距保底 / 距上限"""
    el = elapsed()
    _p("=" * 50)
    _p(f"  本轮时长账   已跑 {_mmss(el)}")
    mn = _min_round()
    if el < mn:
        _p(f"  ⛔ 距保底还差 {_mmss(mn - el)} —— 现在【不允许收工】")
        _p("     继续：回复待回的人 → 没活了就 python soul_round.py idle 挂着等")
        if allow_match_now():
            _p("     或主动找新人（当前是社交时段）")
    elif el < MAX_ROUND:
        _p(f"  ✅ 已满足保底（30 分钟）")
        _p(f"  ⏳ 距 60 分钟上限还剩 {_mmss(MAX_ROUND - el)} —— 可继续，也可收工")
        _p("     没事干就 python soul_round.py end 收工，别空耗")
    else:
        _p(f"  🔴 已超过 60 分钟上限 —— 立刻 python soul_round.py end")
    _p("=" * 50)
    return 0 if el < mn else 2


def allow_match_now():
    """当前是否允许主动搭讪（晚上档 19:00~次日08:00）"""
    n = datetime.now()
    m = n.hour * 60 + n.minute
    return not (DAY_START <= m < DAY_END)


def _waiting_now():
    """当前「她最后发言」的真人清单（排除收尾语）"""
    pend = collect_pending()
    return [p for p in pend if not p["mine"] and not p["closer"]]


def _load_seen():
    try:
        with open(SEEN, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_seen(d):
    try:
        with open(SEEN, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except Exception:
        pass


def new_messages(mark=True):
    """⭐ 增量检测：自上次查看以来，**有没有新消息**（2026-09-26 用户："每隔一分钟检测一下，有就赶紧回"）

    靠 `SEEN` 里的水位线比对「她最后发言时间戳」，只看**新增**的，不会重复报。
    mark=True 会把水位线推进（下次不再报）。
    """
    # ⭐ 先确认库真的拉到了（2026-09-27）。
    #   拉失败时如果照常查本地旧库，会**一直报"无新消息"** —— 看起来一切正常，
    #   其实早就瞎了。这是最危险的静默失败。
    #   → 拉取失败返回 **None**（不是 []），让调用方能区分"失败"和"真没消息"。
    try:
        got = I.pull()
    except Exception as e:
        print(f"⚠️ 拉取 Soul 库异常: {e!r}")
        return None
    if got < 2:
        print(f"⚠️ 拉取 Soul 库失败（got={got}，adb 未连接？）")
        return None

    pend = collect_pending()
    waiting = [p for p in pend if not p["mine"]]
    seen = _load_seen()
    out = []
    for p in waiting:
        mts = p.get("mts") or 0
        if mts > seen.get(p["name"], 0):
            out.append(p)
        if mark:
            seen[p["name"]] = max(seen.get(p["name"], 0), mts)
    if mark:
        _save_seen(seen)
    return out


def _load_newseen():
    try:
        with open(NEWMSG_SEEN, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return None                      # None = 文件不存在（首次运行）


def _save_newseen(s):
    try:
        with open(NEWMSG_SEEN, "w", encoding="utf-8") as f:
            json.dump(sorted(s)[-500:], f, ensure_ascii=False)
    except Exception:
        pass


def _watcher_alive():
    """watcher 心跳是否新鲜（>180s 视为已死）"""
    try:
        return (time.time() - os.path.getmtime(HEARTBEAT)) < 180
    except Exception:
        return False


def cmd_new():
    """打印「watcher 抓到、但我还没报过」的新消息（增量，不重复报）

    ⚠️ 2026-09-27 修**静默漏报**（本轮实测连踩 3 次）：
      旧实现自己又算一遍 `new_messages(mark=True)`，但它和常驻 watcher **共用同一个
      SEEN 水位线** —— watcher 每 60s 先跑一轮把水位推掉，于是 agent 再跑 `new`
      永远报"无新消息"，而库里明明有她的新回复（`new` 说无、`pending` 说有）。
      现在：**读 watcher 写好的队列 .soul_newmsg.json**，用独立指纹集合去重，
      两边职责不再打架；**watcher 死了才回退**到实时计算并明确告警。
    """
    alive = _watcher_alive()
    out, mode = [], ""
    if alive:
        mode = "队列（watcher 在跑）"
        try:
            with open(NEWMSG, "r", encoding="utf-8") as f:
                q = json.load(f)
        except Exception:
            q = []
        seen = _load_newseen()
        if seen is None:                 # 首次运行：把存量当已读，不回灌历史
            _save_newseen({f"{i.get('name')}|{i.get('time')}|{i.get('at')}|{i.get('text')}"
                           for i in q})
            seen = _load_newseen() or set()
        for it in q:
            fp = f"{it.get('name')}|{it.get('time')}|{it.get('at')}|{it.get('text')}"
            if fp not in seen:
                seen.add(fp)
                it["closer"] = False
                out.append(it)
        _save_newseen(seen)
        # ⭐ 过滤「已经回过的」：队列里的旧项在 agent 回完人之后仍然躺着，
        #   直接照搬会**重复报刚回完的人 → 有二次误回的风险**（2026-09-27 实测）。
        #   判据以实时库为准：只有她现在**仍是「她最后发言」**才算还没处理。
        try:
            wait_names = {p["name"] for p in collect_pending() if not p["mine"]}

            def _kept(n_):
                if n_ in wait_names:
                    return True
                return any(n_ and (n_[:8] in w or w[:8] in n_) for w in wait_names)

            out = [it for it in out if _kept(it.get("name"))]
        except Exception:
            pass
    else:
        mode = "实时计算（⚠ watcher 心跳已停，回退模式）"
        live = new_messages(mark=True)
        if live is None:
            _p("=" * 56)
            _p("  ⚠️ 拉取 Soul 库失败 —— 现在【不知道】有没有新消息，别当成'没有'")
            _p("=" * 56)
            return 3
        out = [{"name": p["name"], "text": p["text"], "time": p["last"],
                "at": "", "closer": p["closer"], "dup": p.get("dup")} for p in live]

    _p("=" * 56)
    if out:
        _p(f"  🔔 新消息 {len(out)} 条 —— 赶紧回：  [{mode}]")
        for it in out:
            tip = "  ← 收尾语，用新话题接" if it.get("closer") else ""
            warn = "  ⚠ 重名，先核对是谁" if it.get("dup") else ""
            _p(f"    {it['name']:<20} {it['text']}   ({it.get('time','')}){tip}{warn}")
            _p(f'      → python soul_reply.py "{it["name"]}" "内容" --wait 25')
    else:
        _p(f"  （无新消息）  已跑 {_mmss(elapsed())}   [{mode}]")
    _p("=" * 56)
    return 2 if out else 0


WATCHLOG  = r"E:\soul\.soul_watch.log"        # watch 常驻日志
HEARTBEAT = r"E:\soul\.soul_watch_beat"       # 心跳文件（外部据此判断 watcher 是否还活着）


def watch(interval=IDLE_POLL):
    """⭐ 7×24 常驻检测：每 interval 秒查一次库，有新消息就写进 .soul_newmsg.json。

    **只读数据库，不碰屏幕** —— 所以可以和 agent 的操作并行，不会撞车。
    用法：python soul_round.py watch [间隔秒]      （Ctrl-C / 删心跳文件 停止）

    ⭐ 加固（2026-09-27，为 7×24 运行）：
      · 外层永不吃异常退出 —— 单次出错只记日志，继续下一轮；
      · adb 掉线自动重连，模拟器没开自动 `ldconsole launch`（等 60s 再连）；
      · 每轮写心跳 + 追加日志到 .soul_watch.log（不刷屏，方便事后查）；
      · 连续 30 次全失败才放弃重连，避免死循环烧 CPU。
    """
    _p(f"[watch] 启动常驻检测，每 {interval}s 一轮（日志 {WATCHLOG}）")
    _wlog(f"=== watch 启动 interval={interval}s ===")
    queue = []
    fails = 0
    last_ok = "从未成功"
    while True:
        # ⭐ 2026-09-29 停止开关（血泪教训）
        #   背景：Windows 计划任务 `SoulWatchdog` **每 5 分钟**跑一次看门狗，
        #   看门狗再用 `CREATE_NEW_CONSOLE` 拉起本进程（独立进程）。
        #   因此「杀 watch 进程」根本没用 —— 5 分钟内必复活，
        #   表现为"用户明明要求停掉监控，它自己又跑起来了"。
        #   本开关提供**即使被拉起也不干活**的兜底（与删除计划任务双保险）：
        #   存在 `_watch_stop` 文件时，watch 主动体面退出。
        if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_watch_stop")):
            _wlog("[watch] 检测到 _watch_stop → 主动退出（不再监控）")
            _p("[watch] 检测到 _watch_stop → 退出")
            return
        try:
            out = new_messages(mark=True)
            if out is None:                            # 拉取失败 ≠ 没消息
                raise RuntimeError("拉取 Soul 库失败（数据不可信）")
            fails = 0                                  # 成功就清零
            last_ok = datetime.now().strftime("%H:%M:%S")
            for p in out:
                item = {"name": p["name"], "text": p["text"], "time": p["last"],
                        "at": datetime.now().strftime("%m-%d %H:%M:%S")}
                queue.append(item)
                line = f"🔔 {item['at']}  {p['name']}: {p['text']}"
                _p("  " + line)
                _wlog(line)
            try:
                with open(NEWMSG, "w", encoding="utf-8") as f:
                    json.dump(queue[-200:], f, ensure_ascii=False, indent=1)
            except Exception:
                pass
        except Exception as e:
            fails += 1
            msg = f"[watch] 第 {fails} 次检测失败: {e!r}"
            _p(msg); _wlog(msg)
            if fails <= 30:
                # 先试重连 adb；不行就拉起模拟器
                try:
                    d = soul.display(refresh=True)
                    if not soul.app_running() or d is None:
                        raise RuntimeError(f"soul_running={soul.app_running()} display={d}")
                    _wlog("[watch] 模拟器/Soul 正常")
                except Exception as e2:
                    _wlog(f"[watch] 检查失败({e2!r})，尝试拉起模拟器")
                    try:
                        _launch_emulator()
                        time.sleep(60)
                        soul.launch_app(wait=12)
                        _wlog("[watch] 模拟器已拉起")
                    except Exception as e3:
                        _wlog(f"[watch] 拉起模拟器失败: {e3!r}")
            else:
                _wlog("[watch] 连续失败 30 次，暂停 10 分钟后再试")
                time.sleep(600)
                fails = 0

        # ⭐⭐ 心跳写在 try/except **外面** —— 它代表"循环还活着"，
        #    跟"这次轮询成功没成功"无关。写在 try 里的话，adb 一抽风就不写心跳，
        #    看门狗会误判死亡 → 想重启 → 发现进程还在 → 死锁（2026-09-27 实测踩过）。
        try:
            with open(HEARTBEAT, "w", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "last_ok": last_ok, "fails": fails, "msgs": len(queue)}))
        except Exception:
            pass
        time.sleep(interval)


def _wlog(line):
    """追加一行到 watch 日志（不刷屏）"""
    try:
        with open(WATCHLOG, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%m-%d %H:%M:%S')}] {line}\n")
    except Exception:
        pass


def watch_alive(max_age=180):
    """watcher 是否还活着（心跳文件多久没更新）"""
    try:
        age = time.time() - os.path.getmtime(HEARTBEAT)
        return age <= max_age, age
    except OSError:
        return False, -1


def idle():
    """没事干时驻留：等新回复 or 等满保底时长。

    退出码: 2=有人回复了（去回） ｜ 3=已达 60 分钟上限（必须收工） ｜ 0=已跑满保底（可收工）
    """
    base = {p["name"]: p["ts"] for p in _waiting_now()}
    _p(f"[idle] 开始驻留，每 {IDLE_POLL}s 轮询一次｜基线待回 {len(base)} 人")
    tick = 0
    while True:
        el = elapsed()
        if el >= MAX_ROUND:
            _p(f"[idle] 已达 60 分钟上限（{_mmss(el)}）→ 必须收工")
            return 3
        if el >= _min_round():
            _p(f"[idle] 已跑满保底 {_mmss(el)} → 可以收工（待回 {len(base)} 人自行决定要不要先回）")
            return 0

        time.sleep(IDLE_POLL)
        tick += 1
        try:
            soul_lock_touch()
        except Exception:
            pass

        now = _waiting_now()
        fresh = [p for p in now if p["ts"] > base.get(p["name"], 0)]
        if fresh:
            for p in fresh:
                _p(f"  🔔 新回复: {p['name']}  {p['text']}  ({p['last']})")
            _p(f"[idle] 有人回复 → 去回复（已跑 {_mmss(elapsed())}）")
            return 2

        if tick % 2 == 0 or el >= _min_round():   # 每 1 分钟报一次进度
            left = _min_round() - el
            tail = f"距保底 {_mmss(left)}" if left > 0 else "已满足保底"
            _p(f"  … {datetime.now().strftime('%H:%M:%S')} 已跑 {_mmss(el)}，{tail}，"
               f"待回 {len(now)} 人，无新消息")


def soul_lock_touch():
    """刷新轮次锁心跳，防止长轮被判定为死锁"""
    soul_lock.round_touch()


def touch():
    soul_lock_touch()
    r = soul_lock._read_round()
    _p(f"[touch] 轮次锁心跳已刷新 tag={r.get('token', '?')[:12]}…")


def end(force=False):
    el = elapsed()
    mn = _min_round()
    if not force and el < mn:
        left = mn - el
        _p("=" * 58)
        _p(f"  ⛔ 拒绝收工：本轮只跑了 {_mmss(el)}，距保底还差 {_mmss(left)}")
        _p("=" * 58)
        _p("  用户要求每轮保底 30 分钟。继续干活：")
        _p('    1) 还有人待回 → python soul_reply.py "昵称" "内容" --wait 30')
        if allow_match_now():
            _p("    2) 没活了 → 主动找新人（当前社交时段）: 星球页「开始匹配」(123,461)")
        _p("    3) 或挂着等回复: python soul_round.py idle")
        _p("    4) 查时长账: python soul_round.py budget")
        _p("  真有急事（设备掉线/异常中断）才用: python soul_round.py end --force")
        _p("=" * 58)
        sys.exit(8)
    if el >= MAX_ROUND:
        _p(f"[end] 已跑 {_mmss(el)}，超过 60 分钟上限 → 强制收工")
    else:
        _p(f"[end] 本轮 {_mmss(el)}（已满足保底）→ 释放轮次锁")
    soul_lock.round_release()
    _save_state({})            # 清空计时与 token，下一轮重新算
    _p("[end] 轮次锁已释放，可以交接给下一个实例")


def lock_status():
    if os.path.exists(soul_lock.ROUND_LOCK):
        age = time.time() - os.path.getmtime(soul_lock.ROUND_LOCK)
        _p(f"ROUND HELD {soul_lock._read_round()}  age={age:.0f}s  "
           f"io_allowed={soul_lock.io_allowed()}")
    else:
        _p("ROUND FREE")
    if os.path.exists(soul_lock.LOCK):
        age = time.time() - os.path.getmtime(soul_lock.LOCK)
        _p(f"IO    HELD {soul_lock._read()}  age={age:.0f}s   ← 短锁滞留，"
           f"可用 python soul_lock.py release 清掉")
    else:
        _p("IO    FREE")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "start"
    if cmd == "start":
        sys.exit(start())
    elif cmd == "budget" or cmd == "left":
        sys.exit(budget())
    elif cmd == "idle":
        sys.exit(idle())
    elif cmd == "new":
        sys.exit(cmd_new())
    elif cmd == "beat":
        ok, age = watch_alive()
        _p(f"watcher {'✅ 活着' if ok else '❌ 已停'}（心跳 {age:.0f}s 前）")
        sys.exit(0 if ok else 1)
    elif cmd == "watch":
        watch(int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else IDLE_POLL)
    elif cmd == "touch":
        touch()
    elif cmd == "end":
        end(force="--force" in sys.argv)
    else:
        lock_status()
