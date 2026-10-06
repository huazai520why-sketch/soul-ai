# -*- coding: utf-8 -*-
"""
Soul 24 小时守护进程 —— 本地模型干活，云端可复盘
================================================
用户口径（2026-10-03）：
  「让本地模型24小时干活，24小时监控 soul 对话数据库：
     有人聊的时候优先聊天；没人聊天的时候去星球匹配认识新人；
     顺便把之前的聊天对象重新唤醒。」
  · 全天不分昼夜（节假日也不分）—— 时段两档（白天只回/晚上全流程）已作废。

优先级（从高到低，用户 2026-10-06 定稿）：
  ① 奇遇铃        全局最高：弹铃**立刻中断**当前动作（回消息/匹配/唤醒），处理完再回原流程
  ② 待回消息      分两类，**未读优先**，同组内新 → 旧：
                    · 未读待回（她刚发、我还没看）
                    · **已读待回**（我看过但还没回）
                  另有「聊天导航红点」= 有新消息 → 本轮**不匹配**，先回来回消息（清红点=回消息）
  ③ 空闲      → 星球匹配认识新人（复用 soul_match，抢占式：中途来消息立刻中断）
  ④ 间歇      → 唤醒老联系人（im.follow 选「聊过≥10句且冷≥12h」的人 + jianghua 开场）
  ⑤ 都没有    → 空闲等待（每 10s 探头，来新消息 10s 内醒来）

安全与稳定（都是这台机器上真金白银换来的）：
  · 单实例互斥：复用 soul_global_lock（整轮锁），与 Agent 手动操作互不撞车
  · 守护单例：daemon.lock 存 pid，重复启动直接退出
  · 限频：每小时总发送上限 + 匹配/唤醒各自最小间隔（防封、防连追）
  · 熔断：连续失败 N 次 → 暂停一段时间，写日志（不静默失败）
  · 确定性闸：复述她/复述我/违禁词/说教词/超长 → 一律不发（宁可沉默，不发废句）
  · 危险动作白名单：本进程**只做**「回消息 / 匹配 / 唤醒」三件事，绝不点礼物/充值/删除/举报

跑法（副机 Session 1，pythonw 无窗）：
  pythonw E:/soul/soul_daemon.py                      # 实例0（主号）
  set SOUL_VMINDEX=1 ^& pythonw E:/soul/soul_daemon.py   # 实例1
"""
import os, sys, io, json, time, sqlite3, subprocess, urllib.request, traceback, re
from datetime import datetime

# ── ⚡ 提速开关（必须在 import soul_reply 之前设，否则不生效）─────────────
# ① SOUL_SEARCH_TRACE：搜索通道逐步「截图+OCR+activity」取证。默认开，
#    6 个步骤 × (0.55s 截图 + 2.45s OCR + 0.38s dumpsys) ≈ 20s，纯调试开销。
#    稳定期关掉；要排查定位问题时 `set SOUL_SEARCH_TRACE=1` 再跑。
# ② SOUL_FIND_PAGES：find() 翻页找人的上限。找不到才翻满；6 → 4 少翻 2 页 ≈ 7s。
#    （滚到顶后近期会话本就在第一屏，绝大多数第一页就命中）
os.environ.setdefault("SOUL_SEARCH_TRACE", "0")
os.environ.setdefault("SOUL_FIND_PAGES", "4")

VM = os.environ.get("SOUL_VMINDEX", "0")
BASE = r"E:\soul"
OUTD = os.path.join(BASE, "_uimap", "daemon")
try:
    os.makedirs(OUTD, exist_ok=True)
except Exception:
    pass

# ---- pythonw 无 stdout 兜底：无条件落盘（2026-10-03 修）----
# 旧逻辑只在 sys.stdout is None 时重定向；guard 用 DETACHED 拉起时
# stdout 可能是"无效句柄"而非 None → reconfigure 成功但 print 静默丢失，
# 诊断全部不可见。改为无条件重定向到 stdout.<vm>.txt。
try:
    # ⭐ 2026-10-04：加 buffering=1（行缓冲）。原来默认块缓冲（8KB），
    #   soul_reply 的报错（!! 未找到 / 串台拦截 / 拒绝）要等缓冲区写满才落盘，
    #   排查时看到的是几分钟前的现场，极易误判（本次事故诊断就吃了这个亏）。
    _so = open(os.path.join(OUTD, "stdout.%s.txt" % VM), "a",
               encoding="utf-8", errors="replace", buffering=1)
    sys.stdout = _so
    sys.stderr = _so
except Exception as _e:
    print("!! stdout 重定向失败: %r" % (_e,))

sys.path.insert(0, BASE)
import soul
import soul_im as im
import soul_reply as sr

try:
    from soul_instance import state_path as _sp
except Exception:
    def _sp(base, name):
        return os.path.join(base, name)

# ⭐ 2026-10-05：累积库/运行状态**必须按当前登录账号解析**（切号后立即换文件）。
#   主号沿用原名（soul_memory.db / state.0.json）—— 现有数据零迁移；
#   非主号自动变 soul_memory.<uid>.db / state.0.<uid>.json。
#   ⚠️ 不能用模块级常量：切号发生在运行期，路径必须**每次调用时**解析。
import soul_acct as _acct          # noqa: E402


def _memdb():
    """当前账号的累积库路径（只增不减）。"""
    return _acct.path(BASE, "soul_memory.db")


def _state_path():
    """当前账号的 daemon 运行状态文件路径。"""
    return _acct.state_path(OUTD, VM)


# 兼容：旧代码/诊断脚本若引用 MEMDB/STATE 常量，取的是**导入那一刻**主号的值。
MEMDB  = _memdb()
STATE  = _state_path()
LOGF   = os.path.join(OUTD, "daemon.%s.log" % VM)
PIDF   = os.path.join(OUTD, "daemon.%s.pid" % VM)
LOCKD  = os.path.join(OUTD, "daemon.%s.lock" % VM)   # ⭐ 独立锁文件（永不删除）
STDOUT = os.path.join(OUTD, "stdout.%s.txt" % VM)
_CRASH_FH = None   # ⭐ 崩溃取证：faulthandler 的文件句柄（挂模块级防被回收）

HOST  = "http://192.168.10.210:11434"          # 本机 Ollama
MODEL = "jianghua"                             # 人设模型
N_MSG = 2                                      # 每条待回最多发几条
USE_FAST = os.environ.get("SOUL_FAST_REPLY", "1") == "1"   # 快速回复路径（soul_fast）

# ⭐ 2026-10-06 用户口径（定稿）：
#   · **匹配来的新用户**（她说过 ≤ 这个轮数）→ 用**本地模型**生成回复（上下文少，够用且快）
#   · **超过这个轮数的老对话** → **一律走智囊团**；智囊团拿不到候选就**不发**，绝不降级本地
#     （老对话用弱提示词的本地模型 = 死磕旧话题 / 干巴巴，实测就是这么聊崩的）
# 计数口径：hist 里 role=="her" 的条数（她说过几句）。
NEW_ROUNDS = int(os.environ.get("SOUL_NEW_ROUNDS", "3"))

# ── ⚡ 提速（2026-10-03，用户要求"操作快一点"）────────────────────
# 实测耗时：模型生成 ~0.4s、OCR 稳态 ~1.2s、截图 ~0.7s、pull ~2.3s。
# 真正的慢来自两处：① pull() 每轮被调 2 次 ② soul_reply 里成串的保守 sleep。
# 两项都可一键关掉验证：SOUL_SLEEP_SCALE=1 / SOUL_PULL_GAP=0
SLEEP_SCALE = float(os.environ.get("SOUL_SLEEP_SCALE", "0.55"))   # 1.0 = 不加速
SLEEP_FLOOR = 0.30                                                # 永不低于 0.3s
PULL_MIN_GAP = float(os.environ.get("SOUL_PULL_GAP", "15"))       # 秒；0 = 每次都拉
# 只缩放这些文件里发起的 sleep（本守护自己的巡检间隔不受影响）
_ACCEL_FILES = ("soul_reply", "soul_match", "soul_round", "soul_daily",
                "soul_auto", "soul.py", "winshot", "soul_read")

# ── 节奏与限额 ────────────────────────────────────────────────
POLL_IDLE   = 150        # 无消息 → 巡检间隔（秒）
POLL_HOT    = 25         # 刚有真人消息 → 快速再看（趁她还在线）
MATCH_EVERY = 60        # 「匹配新人」最小间隔（2026-10-03 用户首要目标：回复完立即匹配，额度内连续匹配）
WAKE_EVERY  = 30 * 60    # 「唤醒老人」最小间隔
# ⭐ 2026-10-06（用户：「怎么还没拉起 唤醒好友 速度太慢了」）：
#   当天匹配额度读完为 0 → 匹配整天不跑，唤醒成了**唯一**的活，还守 30 分钟就太空转。
#   此时收紧到 WAKE_EVERY_IDLE。调：`SOUL_WAKE_EVERY_IDLE`（分钟）
WAKE_EVERY_IDLE = int(os.environ.get("SOUL_WAKE_EVERY_IDLE", "10")) * 60
MATCH_N     = 3          # 每次匹配几个
# ⭐ 2026-10-06 用户现场报「匹配次数用完了 没有走下一步唤醒」→ 挖出的真 bug：
#   `match_nav_fail_streak`（导航失败连续计数）**只被写入、从未被判断** = 死变量。
#   后果：连续十几二十次 `no_planet`（压根没进到星球页）→ 因为 10-06「防误判额度耗尽」
#   的修正把它排除在 `match_empty_streak` 之外 → 判不出「耗尽」→ 不转唤醒 → **空转到天亮**。
#   实测 2026-10-06 04:38 抓到 `match_nav_fail_streak = 15`、`match_empty_streak = 0`。
#   现在：连续 ≥MATCH_NAV_FAIL_MAX 次导航失败 → 判定**匹配通道故障** → 立刻转唤醒 + 明确告警
#   （根因可能是额度用完、UI 改版、App 卡死，任何一种都不该让机器干等）。
MATCH_NAV_FAIL_MAX = int(os.environ.get("SOUL_MATCH_NAV_FAIL_MAX", "3"))
# ⭐ 2026-10-06 补（用户现场质疑「怎么还是在一直走匹配啊」）：
#   上面两个兜底都只做到了「**这一次**跳过匹配 / 立刻转唤醒」，**没有**让后续轮次别再来。
#   匹配间隔才 60s → 判完用完，1 分钟后照样再进一次「星球匹配」，而且一半轮次压根
#   进不去星球页（`no_planet`），每次白烧 ~100s 导航（实测 06:27~06:29 一轮 98966 ms）。
#   所以再加一层：**确认没戏 → 整段匹配静默** MATCH_SILENT_COOL，期间连 to_planet 都不做，
#   只走「唤醒老人 + 巡检」。到期再试一次（App 会刷新次数 / 弹层「去聊天」能攒回次数）。
#   静默源分两类，**时长不同**（2026-10-06 用户口径：「静默也不会更新次数，除非次日」）：
#     ① 额度读完为 0 → **静默到次日**。App 是**每日**配额（弹层原文
#        「今日免费匹配机会已用完(50/50)」），当天再试多少次都不会有新次数 → 试就是白烧。
#        跨天（日期一变）自动放行，不用人工干预。
#     ② 匹配通道故障（no_planet）→ 静默 MATCH_SILENT_COOL(30min)。那是 UI/App 卡死，
#        过一阵可能自己好，不该为它放弃一整天。
MATCH_SILENT_COOL = int(os.environ.get("SOUL_MATCH_SILENT_COOL", "30")) * 60
# ⭐ 2026-10-06 匹配前的「聊天导航红点」闸（用户两轮定稿）：
#   · 「匹配之前记得把聊天导航的红点消除完了之后再匹配」
#   · 「清红点**不是叫你进入返回**，是**进入然后回消息**」
#   → 连 DOT_BLOCK_MAX 轮都消不掉的红点 = 系统卡片（回不了）→ 放行匹配，防饿死。
DOT_BLOCK_MAX = int(os.environ.get("SOUL_DOT_BLOCK_MAX", "3"))
# ⭐ 2026-10-06 用户口径：「**先问智囊团，60s 拿不到才用预生成池**」（作用于**老对话**）。
#   这是**墙钟硬预算**（不是单次调用超时）：智囊团自己那套（快 40s／深最多 ~80s）
#   是"单次 http"的上限，叠加起来会超 60s，所以这里再用线程 join 兜一层。
#   ⚠️ 新对话不走智囊团（直接本地层），所以这条预算实际只对 >NEW_ROUNDS 轮的老对话生效。
BRAIN_BUDGET = float(os.environ.get("SOUL_BRAIN_BUDGET", "60"))
WAKE_N      = 2          # 每次唤醒几个
# ⭐ 2026-10-03 用户：「有的人最近聊天时间太久了 不要唤醒」
#   → 唤醒窗口 = 冷 12h ~ 3 天；**超过 3 天（72h）不开口**（太久没联系，开口很突兀）。
WAKE_MIN_H  = 12
WAKE_MAX_H  = 3 * 24      # 72 小时；想放宽/收紧改这一行
# ⭐ 2026-10-04 用户口径：唤醒「按一天一次计算」——同一个人一天最多唤 1 次；
#   累计 3 天唤了都没回（她也没主动找我）→ **永久不再主动唤醒她**。
#   注意：只停「主动唤醒」，她若主动来信仍照常回复（见 do_reply）。
WAKE_MAX_DAYS = 3
SEND_CAP_H  = 40         # 每小时总发送上限（回复+匹配+唤醒）
FAIL_MAX    = 5          # 连续失败熔断阈值
FAIL_PAUSE  = 30 * 60    # 熔断暂停时长

BAN_WORDS   = ["代码", "脚本", "程序", "互联网", "程序员", "算法", "服务器", "运维"]
# ⭐ 2026-10-03 追加「装富/过度承诺」词：江华人设是穷（小电驴），
#   实测模型生成过「我开车过去接你」——不符合人设，ban 掉这类表述。
BAN_RICH    = ["开车", "接你", "送你回家", "我请客", "我买单", "请你吃饭",
               "转账", "包了", "给你点", "我买单了"]
RETRY_MAX   = 3            # 同一句「她说」最多折腾几次（生成+发送）
RETRY_COOL  = 40 * 60      # 到了上限就冷却多久（期间不再为它忙，去干别的）
# ⭐ 2026-10-04 画面健康熔断：连续 N 次取不到有效截图 → 判定模拟器画面挂死
#   （实测事故：MuMu 渲染进程挂死 → wshot 恒 3406B 纯色 → 截图/OCR 全空 →
#    所有 UI 判定失败，每轮白烧 100~200s，还把真人误判成"重试冷却"）
SHOT_FAIL_MAX = 3         # 连续几次截图异常就触发分级恢复
RECOVER_COOL  = 10 * 60   # 两次恢复之间的最小间隔（防恢复风暴）
# ⭐ 2026-10-04（F2）「停手转人工」与「僵尸待回」——针对全天日志实测的两处空转：
#   ① 3 个真人被重试到 16~19 次仍卡在"冷却→重试→冷却"里，每 40 分钟白烧 90~200s/人，
#      一天产生 600+ 条"重试冷却"，既不休止也不上报（N8）。
#   ② 几天前的已读消息永远挂在 pending，每 10s 假唤醒一次，匹配/唤醒通道被反复打断（N7）。
COOL_CYCLE_MAX = 2        # 熬过几个「冷却周期」仍失败 → 停手转人工（2×RETRY_MAX≈6 次尝试）
ZOMBIE_H       = 36       # 已读且她这句挂起 ≥ 这么多小时 → 降级（不再每轮抢占/重试）
MANUAL_REVIEW  = os.path.join(OUTD, "manual_review.%s.txt" % VM)
# ⭐ 2026-10-04 删掉「你得」：它是方言/口语里的普通词，实测把正常回复误杀
#   （`剔: [["说教['你得']", '说了你得请我喝汽水']]` → 闸后空 → 该回的消息直接不回）。
#   真正的"说教"由「你应该/你要改/听我的/不该/别老」覆盖，不靠"你得"。
PREACH_WORDS = ["毛病", "你应该", "你要改", "不该", "别老", "老是", "这样不好",
                "我照你说的", "听我的"]
SKIP_LAST   = ("互动消息", "系统通知", "官方号", "陪伴聊天助手", "打招呼吧", "想聊天",
               "打完招呼", "看了看", "wink", "Wink")
SYS_CARD_KEYS = ("clickItems", "buttonConfig", "jumpUrl", "activityId", "highlightText",
                 "tagAuthGuide", "bgUrl", "emotionUrl", "messageType", "bubble_im_choice",
                 "little_tip", "authTags", "pushType", "trackId")


# ══════════════════════ 基础：日志 / 状态 ══════════════════════
# ⭐ 2026-10-04（N15）守护存活脉冲：任何 log() / 显式 _pulse() 都刷新它。
#   旧看门狗**只在持整轮锁期间**（in_round）检查锁心跳；而实测事故是 daemon 卡在
#   main() 的**启动阶段**（持锁之前，如 soul.calibrate() / OCR·模型预热）
#   → 全程无人检查 → 静默 80 分钟无人接管（guard 只看 pid 存活，而 pid 还活着）。
#   新闸独立于整轮锁：脉冲超过 HANG_LIMIT 没更新 → 自杀等 guard 重拉。
_PULSE = {"t": time.time()}
HANG_LIMIT = 12 * 60      # 秒；超这么久没任何动作即判定卡死（熔断暂停走 _pause_loop 分片刷新）


def _pulse():
    _PULSE["t"] = time.time()


def _pause_loop(total, chunk=300):
    """长暂停（如熔断 30 分钟）**分片睡**，每片刷新存活脉冲 ——
    免得看门狗把「按设计暂停」误判成卡死。总时长与 time.sleep(total) 等价。"""
    end = time.time() + float(total)
    while True:
        left = end - time.time()
        if left <= 0:
            return
        _pulse()
        time.sleep(min(float(chunk), left))


def log(s):
    _PULSE["t"] = time.time()
    line = "[%s] %s" % (datetime.now().strftime("%m-%d %H:%M:%S"), s)
    try:
        print(line)
    except Exception:
        # ⭐ 2026-10-04：print 失败绝不能把日志整体搞崩（实测踩到两次：
        #   pythonw 无 stdout 兜底、控制台 GBK 编码打印 "⛔" → UnicodeEncodeError
        #   → 真实错误被编码异常掩盖，只剩 traceback）。落盘那条路必须照常走。
        pass
    try:
        with io.open(LOGF, "a", encoding="utf-8", errors="replace") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_state():
    try:
        with io.open(_state_path(), encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def save_state(st):
    try:
        with io.open(_state_path(), "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
    except Exception as e:
        log("  !! state 写入失败: %r" % (e,))


def _reachable(name):
    """昵称是否 OCR/搜索可达（全 emoji / 空白特殊字符 → 找不到 → 不算待回）"""
    for ch in (name or ""):
        if '\u4e00' <= ch <= '\u9fff':
            return True
        if ch.isascii() and ch.isalnum():
            return True
    return False


def _is_fake_last(text):
    s = str(text or "")
    return (not s.strip()) or any(k in s for k in SKIP_LAST)


def _sendable(name):
    """陌生人预检（2026-10-03；2026-10-04 修订）——
    「可发」= 累积库聊过 ≥3 句  **或**  她最后一条真人消息还在等我回，但不碰 UI。

    ⭐ 2026-10-04 修订（用户澄清）：有些老友是**建脚本之前人工聊的**，聊天记录在
       App 会话表里，但历史只有 1~2 条 → 被原判据(≥3句)误判成陌生人、整批跳过不回。
       她既然主动来信（末条是她），就不是"陌生人"；回她不违反「防发给陌生人」红线。
       只发过开场白、她还没回的（末条是我、条数<3）照旧跳过 —— 保留原优化（省 ~60s/人 UI 白跑）。
    真正防串台由 soul_reply 的标题/内容硬闸负责，这里只是预检。
    uid 查不到时不拦（罕见，交给 soul_reply 硬闸兜底，防 nick 表缺口误杀）。"""
    uid = _uid_by_name(name)
    if not uid:
        return True
    try:
        c = sqlite3.connect(_memdb())
        n = c.execute(
            "SELECT count(*) FROM chatmsg WHERE sessionId IN "
            "(SELECT sessionId FROM session WHERE toUserId=?)",
            (str(uid),)).fetchone()[0]
        if (n or 0) >= 3:
            c.close()
            return True
        # ⭐ 兜底：她最后一条真人消息是不是在等我回（= 主动来信的老友/老联系人）
        sids = [r[0] for r in c.execute(
            "SELECT sessionId FROM session WHERE toUserId=?", (str(uid),)).fetchall()]
        if sids:
            ph = ",".join("?" * len(sids))
            rows = c.execute(
                "SELECT senderId, text, msgContent FROM chatmsg "
                "WHERE sessionId IN (%s) ORDER BY localTime DESC LIMIT 8" % ph,
                sids).fetchall()
            for _sd, _tx, _mc in rows:
                if not _tx or not str(_tx).strip():
                    continue
                try:
                    if im._is_sys(_tx, _mc):
                        continue
                except Exception:
                    if any(k in str(_mc or "") for k in SYS_CARD_KEYS):
                        continue
                c.close()
                return str(_sd) != str(im.ME)      # 末条真人消息是她发的 → 等我回 → 可发
        c.close()
        return False
    except Exception:
        return True


def _deliver(name, texts, my_recent=None, allow_chain=False):
    """统一发送通道：快速路径优先，MISS 才回退全路径。
    返回 "SENT"/"SKIP"(硬闸)/"FAIL"/"UNKNOWN"(校验未知·可能已发)。do_reply 与 wake_old 共用。"""
    # ⭐ 2026-10-05 账号安全闸（放这里：do_reply 与 wake_old 两条发送路径都覆盖）
    # ⭐ 2026-10-06 force=True：发送前强制刷新身份
    if not _account_gate_ok(force=True):
        return "SKIP"
    if allow_chain:
        # ⭐ 2026-10-03 拆分条只走全路径（快速路径不认连发豁免）
        try:
            r = sr.reply(name, texts, allow_chain=True)
            return "SENT" if r is True else ("UNKNOWN" if r == "UNKNOWN" else "FAIL")
        except Exception as e:
            log("     !! 发送异常: %r" % (e,))
            return "FAIL"

    if USE_FAST:
        try:
            import soul_fast as sf
            r = sf.fast_reply(name, texts, my_recent=my_recent)
        except Exception as e:
            log("     !! 快速路径异常: %r → 回退全路径" % (e,))
            r = "MISS"
        if r == "SENT":
            return "SENT"
        if r == "GATED":
            log("     ⛔ 硬闸拦截（长度/重复/频率）→ 不发")
            return "SKIP"
        if r == "FAIL":
            return "FAIL"
        log("     … 快速路径未达成(MISS) → 回退 soul_reply 全路径")
    try:
        r = sr.reply(name, texts)
        return "SENT" if r is True else ("UNKNOWN" if r == "UNKNOWN" else "FAIL")
    except Exception as e:
        log("     !! 发送异常: %r" % (e,))
        return "FAIL"


def _follow_mem(min_msgs=10, cool_h=12, max_idle_h=None, skip=None):
    """im.follow 的**累积库版**（2026-10-03 修「唤醒永远没人」）：
    正式库会被 Soul 清空（实测 2811→55 条），im.follow 读它 → 无人满足「聊过≥10句」。
    累积库只增不减（2863 条），才是全量事实。判据与 im.follow 完全一致：
    双方真人消息合计 ≥min_msgs、她至少回过 1 句、**最后一条是我发的**（对话凉了）、
    冷 ≥cool_h 小时；**max_idle_h = 上限（超过就是"太久没聊"，不开口）**；按冷却时长降序。"""
    try:
        status = im._db_status()
    except Exception:
        status = {}
    out = []
    too_old = 0
    skip = set(skip or ())     # ⭐ 永久放弃主动唤醒的人（用户 2026-10-04）
    try:
        c = sqlite3.connect(_memdb())
        nicks = {str(u): n for u, n in c.execute("SELECT uid, name FROM nick")}
        rows = c.execute("SELECT sessionId, toUserId, timestamp FROM session").fetchall()
        now_ms = time.time() * 1000
        for sid, uid, ts in rows:
            name = nicks.get(str(uid), str(uid))
            if name in skip:
                continue           # ⭐ 已「永久放弃主动唤醒」→ 不进候选
            try:
                if im._is_official(name) or status.get(name) in ("stopped", "skipped", "gift"):
                    continue
            except Exception:
                pass
            msgs = c.execute(
                "SELECT senderId, text, msgContent, localTime FROM chatmsg "
                "WHERE sessionId=? ORDER BY localTime DESC LIMIT 300", (sid,)).fetchall()
            real = []
            for m in msgs:
                try:
                    if not im._is_sys(m[1], m[2]):
                        real.append(m)
                except Exception:
                    real.append(m)
            if not real:
                continue
            hers = sum(1 for m in real if str(m[0]) != str(im.ME))
            total = len(real)
            last_sender, last_text, last_lt = real[0][0], real[0][1], real[0][3]
            if total < min_msgs or hers == 0:
                continue
            if str(last_sender) != str(im.ME):
                continue                       # 她最后发言 → 属 pending，不在此重复
            idle_h = (now_ms - (last_lt or 0)) / 3600000.0
            if idle_h < cool_h:
                continue
            if max_idle_h and idle_h > max_idle_h:
                too_old += 1                 # 太久没聊 → 不开口（用户 2026-10-03 口径）
                continue
            out.append((idle_h, name, hers, total, last_text, last_lt))
        c.close()
    except Exception as e:
        log("  !! _follow_mem 异常: %r" % (e,))
        # ⭐ 2026-10-06 修（P1#10）：异常时**不再返回 []**（那会被上层当成"唤醒池 0 人"→
        #   进而判干旱→切号）。返回 None = 未知，调用方必须区分处理（fail-closed）。
        return None
    if too_old:
        log("  唤醒候选：%d 人因「冷 > %.0f 天」被排除（太久没聊，不开口）"
            % (too_old, (max_idle_h or 0) / 24.0))
    out.sort(key=lambda z: z[0], reverse=True)
    return out


def _norm_nick(n):
    """昵称归一化：去掉波浪号/空格/尾部的句号等装饰 → 与 OCR 读到的形式构成子串（定位才命中）

    ⭐ 2026-10-03 加「去尾部标点」：实测「别在垃圾堆里找糖。」（昵称自带句号）
    列表 find 与搜索都匹配不上（OCR 常不渲染/识别那个句号）→ 定位必失败。
    """
    t = str(n or "").replace("~", "").replace("～", "").replace(" ", "").strip()
    t = t.rstrip("。.．!！?？·、,，…～~'\"“”‘’_-")
    return t if len(t) >= 2 else str(n or "").strip().rstrip("。.．!！?？")


def _hour_budget(st):
    hk = time.strftime("%Y%m%d%H")
    if st.get("send_hour") != hk:
        st["send_hour"] = hk
        st["send_cnt"] = 0
    return int(st.get("send_cnt", 0)) < SEND_CAP_H


def _spend(st, k=1):
    st["send_cnt"] = int(st.get("send_cnt", 0)) + k


# ══════════════════════ ⚡ 提速：sleep 缩放 / pull 节流 / 分段计时 ══════════════════════
_real_sleep = time.sleep
_ACC = {"saved": 0.0, "pulls": 0, "pull_skips": 0}


def _sleep_fast(sec):
    """只加速 soul_* 模块内的 sleep；本进程的巡检睡眠不受影响。"""
    try:
        f = sys._getframe(1)
        fn = (f.f_code.co_filename or "").replace("\\", "/").rsplit("/", 1)[-1]
        if any(fn == p or fn.startswith(p) for p in _ACCEL_FILES):
            try:
                v = float(sec)
            except Exception:
                v = sec
            if isinstance(v, float):
                nv = v if v <= SLEEP_FLOOR else max(SLEEP_FLOOR, v * SLEEP_SCALE)
                _ACC["saved"] += (v - nv)
                v = nv
            sec = v
    except Exception:
        pass
    _real_sleep(sec)


_ACC.update({"shot": [0, 0.0], "ocr": [0, 0.0], "sh": [0, 0.0], "tap": [0, 0.0]})


def _wrap(name, fn):
    """给 soul 的函数包一层计数+计时"""
    def _f(*a, **kw):
        t0 = time.time()
        try:
            return fn(*a, **kw)
        finally:
            c = _ACC[name]
            c[0] += 1
            c[1] += time.time() - t0
    _f.__name__ = getattr(fn, "__name__", name)
    return _f


def _install_counters():
    """埋点：统计截图/OCR/sh/tap 的次数与总耗时（排查"到底慢在哪"的唯一依据）"""
    try:
        soul.screenshot = _wrap("shot", soul.screenshot)
    except Exception:
        pass
    try:
        soul.sh = _wrap("sh", soul.sh)
    except Exception:
        pass
    try:
        soul.tap = _wrap("tap", soul.tap)
    except Exception:
        pass
    try:
        import soul_read
        soul_read.items = _wrap("ocr", soul_read.items)
    except Exception:
        pass
    log("⏱ 埋点已装：screenshot / OCR / sh / tap 计数计时")


def _acc_line():
    s, o, h, t = _ACC["shot"], _ACC["ocr"], _ACC["sh"], _ACC["tap"]
    return ("⏱ 调用统计：截图 %d次/%.1fs ｜ OCR %d次/%.1fs ｜ sh %d次/%.1fs ｜ tap %d次/%.1fs"
            % (s[0], s[1], o[0], o[1], h[0], h[1], t[0], t[1]))


# ── 变化检测：设备库没变就不 pull（pull 一次约 2.3s）─────────────────────
_STAMP = {"last": None, "skips": 0, "probe_ms": 0.0}


def _device_stamp():
    """设备端直查（一次 sqlite3，~0.3s）→ (消息数, 最大时间戳)。拿不到返回 None。"""
    try:
        db = "%s/IM-SDK-%s-DATA.db" % (im.DBDIR, im.SESS)
        out = soul.sh('sqlite3 %s "select count(*)||\'|\'||coalesce(max(localTime),0) '
                      'from chatmsg"' % db, timeout=15)
        import re as _re
        m = _re.search(r"(\d+)\s*\|\s*(\d+)", str(out or ""))
        if m:
            return (int(m.group(1)), int(m.group(2)))
    except Exception:
        pass
    return None


def install_pull_gate():
    """只有设备库真的变了才 pull —— 没新消息时省掉 2.3s 的 6 次 cp + 校验。"""
    orig = im.pull
    gate_on = {"v": True}

    def _pull(*a, **kw):
        if gate_on["v"]:
            t0 = time.time()
            st = _device_stamp()
            _STAMP["probe_ms"] = (time.time() - t0) * 1000
            if st is None:                      # 设备端直查不可用 → 老老实实全量拉
                gate_on["v"] = False
                log("  (设备端 sqlite3 不可用 → 关闭变化检测，恢复全量 pull)")
            elif st == _STAMP["last"]:
                _STAMP["skips"] += 1
                return 0                        # 没变化，跳过
            else:
                _STAMP["last"] = st
        return orig(*a, **kw)

    im.pull = _pull
    log("⚡ 提速：pull 变化检测（设备端直查指纹，未变则跳过）")


def install_accel():
    """装载提速：sleep 缩放 + pull 变化检测 + 埋点。必须在 import 完 soul_* 之后调用。"""
    _install_counters()
    install_pull_gate()
    if SLEEP_SCALE >= 1.0:
        log("⚡ 提速：sleep 缩放已关闭 (SLEEP_SCALE=1)")
    else:
        time.sleep = _sleep_fast
        for name in ("soul", "soul_reply", "soul_read"):
            m = sys.modules.get(name)
            if m is not None and getattr(m, "sleep", None) is _real_sleep:
                try:
                    m.sleep = _sleep_fast
                except Exception:
                    pass
        log("⚡ 提速：soul_* 内 sleep ×%.2f（下限 %.2fs）" % (SLEEP_SCALE, SLEEP_FLOOR))

    # （原「15s 时间节流」已移除：变化检测更准，且不会漏掉真实新消息）


class _Tick(object):
    """分段计时：with _Tick("名字") as t: ..."""
    def __init__(self, name, st=None):
        self.name, self.st, self.t0 = name, st, 0.0

    def __enter__(self):
        self.t0 = time.time()
        return self

    def __exit__(self, *e):
        ms = (time.time() - self.t0) * 1000
        log("   ⏱ %-22s %7.0f ms" % (self.name, ms))
        if self.st is not None:
            self.st.setdefault("t", {}).__setitem__(self.name, round(ms))
        return False


# ══════════════════════ 累积库：merge + 取历史 ══════════════════════
def merge_memory():
    """把正式库(im.IMDB)增量并入累积库 —— Soul 会清库，这里只增不减。

    ⭐ 2026-10-06 用户拍板「把累积库和正式库合并一下不就行了」：
      实现已**搬到 `soul_im.merge_memory()`**，并挂在 `soul_im.pull()` 成功之后自动执行
      → 消除"每轮才同步一次"的滞后。此处保留同名薄包装，老调用点（本轮轮末）行为不变：
      轮末再兜一次，确保即使某次 pull 之后才写入的消息也不会滞留到下一轮。
    """
    try:
        import soul_im as _im
        return _im.merge_memory()
    except Exception as e:
        log("  !! merge_memory（薄包装）出错: %r" % (e,))
        return 0


def _sid_of(uid):
    try:
        c = sqlite3.connect(_memdb())
        r = c.execute("SELECT sessionId FROM session WHERE toUserId=?", (str(uid),)).fetchall()
        c.close()
        return r[0][0] if r else None
    except Exception:
        return None


def _uid_from_sid(sid):
    """sessionId → toUserId（**权威**）。F1 的基石。

    两级证据：
      ① 累积库 session 表 sessionId → toUserId（同一行记录，最可靠）
      ② 兜底字符串切分：sid = ME + toUserId（2026-10-04 方案 A 实测印证，
         例 96691646 + 459174231 = 96691646459174231）
    拿不到就返回 None（调用方退回原昵称逻辑，绝不猜）。
    """
    if not sid:
        return None
    s = str(sid).strip()
    try:
        c = sqlite3.connect(_memdb())
        r = c.execute("SELECT toUserId FROM session WHERE sessionId=?", (s,)).fetchone()
        c.close()
        if r and r[0]:
            return str(r[0])
    except Exception:
        pass
    me = str(im.ME)
    if s.startswith(me) and len(s) > len(me) and s[len(me):].isdigit():
        return s[len(me):]
    return None


def _uid_by_name(name):
    """昵称 → uid（累积库 nick 表，精确优先、模糊兜底）"""
    try:
        c = sqlite3.connect(_memdb())
        r = c.execute("SELECT uid FROM nick WHERE name=?", (str(name).strip(),)).fetchall()
        if not r:
            r = c.execute("SELECT uid FROM nick WHERE name LIKE ?",
                          ("%" + str(name).strip() + "%",)).fetchall()
        c.close()
        return r[0][0] if r else None
    except Exception:
        return None


def hist_of(sid, limit=24):
    """从累积库取该会话历史（滤系统卡片），返回 [{role,text}...]"""
    if not sid:
        return []
    try:
        c = sqlite3.connect(_memdb())
        rows = c.execute("SELECT senderId, text, msgContent, localTime FROM chatmsg "
                         "WHERE sessionId=? ORDER BY localTime DESC LIMIT ?",
                         (sid, limit)).fetchall()
        c.close()
    except Exception:
        return []
    out = []
    for s, t, ct, lt in reversed(rows):
        if not t or not str(t).strip():
            continue
        if any(k in str(ct or "") for k in SYS_CARD_KEYS):
            continue
        out.append({"role": "me" if str(s) == str(im.ME) else "her", "text": str(t)[:100]})
    return out[-10:]


# ══════════════════════ 本地模型：生成 ══════════════════════
def _llm(prompt, temp=0.85, timeout=180):
    body = json.dumps({"model": MODEL, "prompt": prompt, "stream": False, "think": False,
                       "options": {"temperature": temp}}).encode()
    req = urllib.request.Request(HOST + "/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = json.loads(r.read()).get("response", "").strip()
    return raw, int((time.time() - t0) * 1000)


def _same(a, b):
    """判定「复述」（是否几乎照搬原话）。

    ⭐ 2026-10-04 修**误判**（用户反馈）：
      原判据 `len(b) >= 2 and b in a` —— 只要回复里出现她消息的**任意连续 2 字**
      就判「复述她」并剔空。中文高频二字词遍地（今天/时间/什么/我们/怎么样…），
      导致大量正常回复被误杀。
      实测 00:21:42：她说「时间多」→ 回「时间多来摆哈龙门阵撒」→ 判复述剔空
      （闸后: []），明明是个好回复却白白沉默一轮。
      改为「**实质相同**」才算复述，正常引用关键词不再误伤。
    """
    if not a or not b:
        return False
    a, b = a.strip(), b.strip()
    if not a or not b:
        return False
    if a == b:                       # ① 完全相同
        return True
    la, lb = len(a), len(b)
    if min(la, lb) < 4:              # ② 短句（嗯/好/在吗）不做包含判断，避免误杀
        return False
    short, long_ = (a, b) if la <= lb else (b, a)
    if short in long_ and len(short) >= len(long_) * 0.8:
        return True                  # ③ 短句占了长句 80%+ → 基本是照搬
    try:                             # ④ 整体高度雷同兜底
        from difflib import SequenceMatcher
        return SequenceMatcher(None, a, b).ratio() >= 0.85
    except Exception:
        return False


# ══════════════ 重试闸（2026-10-03）：防止「同一个待回被无限重试」占死守护 ══════════════
#   事故：风止遇你的同一句被反复生成 8 次、闸剔空 → 不发 → 她仍 pending → 下一轮又来。
#   2 小时 16 分钟里「有人在聊」303 次、星球匹配只跑了 1 次、唤醒 0 次 —— 守护被占死。
#   判据键 = 昵称 + 她最后一句（她一有新发言就是新键，立刻恢复响应能力）。
def _try_key(name, her_text):
    return "%s|%s" % (str(name), str(her_text or "")[:40])


def _cooling(st, name, her_text):
    """该「她这一句」是否已到重试上限且仍在冷却窗口内"""
    tr = st.setdefault("tries", {})
    rec = tr.get(_try_key(name, her_text))
    if not rec:
        return False
    try:
        n, ts = int(rec[0]), float(rec[1])
    except Exception:
        return False
    # ⭐ 2026-10-04（F2 补）：已达「停手转人工」阈值的人**不再算冷却**。
    #   否则冷却过滤会先把他从 real 里摘掉，停手逻辑永远看不到他
    #   —— 实测 Te Fuir 已试 17 次（5 个冷却周期）却因正在冷却窗口内而漏网。
    if _abandoned(st, name, her_text):
        return False
    return n >= RETRY_MAX and (time.time() - ts) < RETRY_COOL


def _bump_try(st, name, her_text):
    """记一次失败尝试（闸空/发送失败都算）"""
    tr = st.setdefault("tries", {})
    k = _try_key(name, her_text)
    try:
        n = int(tr.get(k, [0, 0.0])[0]) + 1
    except Exception:
        n = 1
    tr[k] = [n, time.time()]
    # 清掉一天前的旧键，避免 state 无限膨胀
    for kk in list(tr):
        try:
            if time.time() - float(tr[kk][1]) > 24 * 3600:
                tr.pop(kk, None)
        except Exception:
            tr.pop(kk, None)
    return n


# ══════════════ F2：停手转人工 / 僵尸待回降级（2026-10-04）══════════════
def _try_cycles(st, name, her_text):
    """该「她这一句」已经熬过几个完整的冷却周期（0 = 还在第一个周期里）。
    n 次尝试 → cycles = n // RETRY_MAX：3 次=1 个周期、6 次=2 个周期。"""
    rec = (st.get("tries") or {}).get(_try_key(name, her_text))
    if not rec:
        return 0
    try:
        return max(0, int(rec[0])) // max(1, RETRY_MAX)
    except Exception:
        return 0


def _abandoned(st, name, her_text):
    """连续 ≥COOL_CYCLE_MAX 个冷却周期仍失败 → 判"结构性发不出去"，停手转人工。"""
    return _try_cycles(st, name, her_text) >= COOL_CYCLE_MAX


def _zombie(p):
    """「僵尸待回」：**已读**且她这句已挂起 ≥ZOMBIE_H 小时 → 不再算真待回。
    为什么：这类条目永远回不了也永不消失，每 10s 把空闲等待打断一次、每次都让唤醒/匹配
    以为"有真待回"而让位（实测 10-04 全天如此），系统永远进不了安静巡检。
    ⚠️ 未读（她刚发）绝不算僵尸；也不是永久丢弃 —— 她再发新消息即自动恢复。
    """
    try:
        _ts, _name, unread, _text, lt, _sid = p[:6]   # im.pending() 现为 7 元（尾带 msgType）
    except Exception:
        return False
    if unread:
        return False
    tms = lt or _ts or 0
    try:
        return (time.time() * 1000 - float(tms)) / 3600000.0 >= ZOMBIE_H
    except Exception:
        return False


def _has_real_pending(rows):
    """统一口径：有没有「真待回」（可达 + 非假末条 + 可发 + 非僵尸）。
    三处"是否让位/提前结束等待"的判定共用，避免口径漂移。"""
    for r in (rows or []):
        try:
            if not (_reachable(r[1]) and not _is_fake_last(r[3]) and _sendable(r[1])):
                continue
            if _zombie(r):
                continue
            return True
        except Exception:
            continue
    return False


def _real_pending(st):
    """当前「真待回」列表（与轮首同一套过滤链）。

    ⭐ 2026-10-06 用户口径「**有新消息来了 该优先回复对方**」：
      回复循环原来对**轮首快照**遍历，中途来的新消息要等下一轮（实测 ~8min）才轮到；
      再叠加几个"设备端已无会话"的幽灵各占 ~100s，新消息可能 10 分钟以上没人理。
      本函数供循环里**每回完一个就复查一次**，谁刚发来就把谁插到队首。
    """
    try:
        pend = im.pending()
    except Exception as e:
        log("  !! 插队复查失败: %r" % (e,))
        return []
    out = []
    for p in (pend or []):
        try:
            if not (_reachable(p[1]) and not _is_fake_last(p[3]) and _sendable(p[1])):
                continue
            if _zombie(p) or _cooling(st, p[1], p[3]) or _abandoned(st, p[1], p[3]):
                continue
        except Exception:
            continue
        out.append(p)
    return out


def _pend_key(p):
    """待回排序键（用户 2026-10-06 口径：「② 待回消息 …… 这里加一个**已读待回**」）。

    顺序：**未读待回**（她刚发、我还没看）优先 → 再 **已读待回**（我看过但没回）；
    同组内按时间 **新 → 旧**。
    行结构 = (timestamp, name, unread, text, localTime, sessionId, msgType)
    """
    try:
        u = 0 if int(p[2] or 0) > 0 else 1      # 0 = 未读（排前面）
    except Exception:
        u = 1
    try:
        ts = int(p[0] or 0)
    except Exception:
        ts = 0
    return (u, -ts)


def _retire(st, rows):
    """F2：把结构性发不出去的人**停手转人工复核**（不删证据、可逆）。
    落 soul_db status=skipped —— im.pending() 本就按这个状态过滤，会自动从所有队列消失；
    同时写人工复核清单，附 sid/uid/尝试次数与恢复命令。"""
    for ts, name, unread, text, lt, sid in (r[:6] for r in rows):
        cyc = _try_cycles(st, name, text)
        try:
            trn = (st.get("tries") or {}).get(_try_key(name, text), ["?", 0])[0]
        except Exception:
            trn = "?"
        uid = _uid_from_sid(sid) or "?"
        log("  ⛔ 停手转人工：%s（已试 %s 次 / %d 个冷却周期）→ status=skipped；"
            "复核后可用 soul_db.set_status('%s','active') 恢复" % (name, trn, cyc, name))
        try:
            _f = io.open(MANUAL_REVIEW, "a", encoding="utf-8")
            _f.write("[%s] %s ｜ 她最后一句: %r ｜ 已试 %s 次 / %d 个冷却周期 "
                     "｜ sid=%s uid=%s ｜ 恢复: soul_db.set_status(%r,'active')\n"
                     % (datetime.now().strftime("%m-%d %H:%M:%S"), name, str(text)[:40],
                        trn, cyc, sid, uid, name))
            _f.close()
        except Exception as e:
            log("  !! 写人工复核清单失败: %r" % (e,))
        try:
            import soul_db as db
            db.set_status(name, "skipped",
                          "自动停手：%d 个冷却周期仍发送失败（见 manual_review）" % cyc)
        except Exception as e:
            log("  !! 落 skipped 失败: %r" % (e,))


# ══════════ 额度弹层：只清弹层，**不再锁当天匹配**（2026-10-04 用户口径改）══════════
# 改前（已作废）：一旦确认额度用尽 → 记一个「今天已用尽」的日期标记，当天不再匹配。
# 用户新口径：没额度也别停 —— 照样进星球页匹配；弹出「今日免费匹配机会已用完」
#   就点弹层里的「去聊天」走免费出口（落到聊天列表；聊满一颗心 +5 次），
#   所以这里只留「弹层检测」，把那套「当天锁定」的逻辑整段删掉。
QUOTA_KEYS = ("免费匹配机会已用完", "匹配机会已用完", "今日免费匹配")


def _today():
    return time.strftime("%Y-%m-%d")


def _screen_has_quota_sheet():
    """当前屏是否浮着「今日免费匹配机会已用完」弹层（只读 OCR，不点击）"""
    try:
        import soul_read as _rd
        txt = "".join(t for t, _, _ in _rd.items())
    except Exception as e:
        log("  !! 额度弹层检测失败: %r" % (e,))
        return False
    return any(k in txt for k in QUOTA_KEYS)


# ⭐ 2026-10-06 用户要的「塌房自述」护栏（A 方案）：本地模型在上下文极薄时会退化出
#   自我塌房鬼话（实测「我玩48岁母单」「我也在玩48对的老母」）。这些词在破冰回复里
#   绝不该出现，命中即剔掉 → 闸后为空 → 触发重生成；全重试失败则降级不发。
_RED_SELF = re.compile(
    r"(母单|老母|离异|带娃|二婚|丧偶|单亲)"      # 婚恋/单亲身分词（破冰回复里绝不该出现）
    r"|玩\s*\d{1,2}\s*[岁对]"                     # 玩NN岁/玩NN对（"玩48岁母单"塌房句式）
    r"|我\s*\d{1,2}\s*岁"                          # 我NN岁（自曝年龄）
)

def gate(raw, incoming, n, my_recent=None):
    """确定性闸：剔复述她/复述我/违禁词/说教词/塌房自述。返回 (可用句, 剔除明细)"""
    inc = (incoming or "").strip().rstrip("？?。.!！~～")
    mine = [str(x).strip() for x in (my_recent or [])]
    kept, dropped = [], []
    for raw_ln in raw.splitlines():
        ln = raw_ln.strip(' \t．.。、,，!！?？~～"“”\'`-*#0123456789.')
        if not ln:
            continue
        if _same(ln, inc):
            dropped.append(["复述她", ln]); continue
        if any(_same(ln, m) for m in mine):
            dropped.append(["复述我", ln]); continue
        hit = [b for b in BAN_WORDS if b in ln]
        if hit:
            dropped.append(["违禁词%s" % hit, ln]); continue
        # ⭐ 2026-10-06（P1#5 内容红线）：违禁词之后再补一道**代码级硬闸** ——
        #   命中 HARD_BAN（时政/政要/军事/领土/灾难/案件 + 擦边 + 站外导流）即剔除该句。
        #   这些不是"语气"问题，是**安全红线**，绝不靠模型自觉。单一源：soul_rules.HARD_BAN。
        # ⭐ 2026-10-06 整改②（消除 fail-open）：红线闸**不可用**时该句**不得放行** ——
        #   旧写法 `except: pass` 会让未过闸的文本漏出去（fail-open），绝不允许。
        #   现在任何异常路径都 `dropped.append(["红线闸不可用"]); continue`（丢弃 + 记日志）。
        _hh = None
        try:
            import soul_rules as _R
            _hh = _R.hard_hit(ln)
        except Exception as _e:
            log("  ⚠️ 红线闸不可用（%r）→ 该句按不可放行丢弃：%r" % (_e, ln[:20]))
            dropped.append(["红线闸不可用"]); continue
        if _hh:
            dropped.append(["红线:%s" % _hh]); continue
        rich = [b for b in BAN_RICH if b in ln]
        if rich:
            dropped.append(["装富%s" % rich, ln]); continue
        ph = [p for p in PREACH_WORDS if p in ln]
        if ph:
            dropped.append(["说教%s" % ph, ln]); continue
        if _RED_SELF.search(ln):
            dropped.append(["塌房自述", ln])
            return [], dropped      # ⭐ 整条判废（不是逐句剔）：塌房 → 触发重生成/降级，避免发半截
        if len(ln) > 22:
            dropped.append(["超长%d" % len(ln), ln]); continue
        kept.append(ln)
    return kept[:n], dropped


# ⭐ 2026-10-04 双开人设：实例 N>0 用独立身份；实例0 返回**原字面量**（逐字不变）。
# ⭐ 2026-10-06 单一数据源：改引用 `soul_rules.IDENT_*`（同一份、且已过双开 rewrite），
#    不再手抄 —— 此前这里与 soul_rules 各存一份、vm1 还要另走一条分支，迟早分叉。
def _who(kind):
    """返回「我是谁」身份串。kind ∈ {reply, wake, pick}。"""
    try:
        import soul_rules as _R
        m = {"reply": _R.IDENT_REPLY, "wake": _R.IDENT_WAKE, "pick": _R.IDENT_PICK}
        if kind in m:
            return m[kind]
    except Exception:
        pass
    return {"reply": "江华（男，在厂里上班，穷、不装富、不吹牛）",
            "wake": "江华（男，在厂里上班，重庆人）",
            "pick": "江华（男，在厂里上班，穷、不装富、不吹牛，重庆人）"}[kind]


def _env_now():
    """一句话「当前环境」（时间/季节/天气），供提示词结合当下。

    ⭐ 2026-10-06 用户口径：「回复要结合当前时间环境天气等等因素，当然这些是次要的」。
    由 soul_env 统一提供（带缓存 + fail-open）；任何异常返回 ''，绝不影响回复链路。
    """
    try:
        import soul_env as _env
        return _env.now_bg()
    except Exception:
        return ""


def _strategy():
    """返回「战略铁律」提示词块 —— **单一来源**：直接复用 soul_brain.PERSONA。

    ⭐ 2026-10-06 用户口径：「聊天守则 = 结合天气环境 + 推进关系 + 铁律 让她主动 我享受」。

    核查发现的结构性缺口：
      · 这条铁律在**在线智囊团**提示词里写了 4 遍（`soul_brain.PERSONA` / `SYSTEM_FAST` /
        `SYSTEM_DEEP` / `_build_case` 尾块）——智囊团是知道的。
      · 但**本地模型链路一个字都没有**：`gen_reply()` 全文只有一句
        「你是江华（男，在厂里上班，穷、不装富、不吹牛）」，没有关系四阶段、
        没有「她付出我享受」、没有「绝不是我去倒贴」。
      · 而 `do_reply` 的 `_is_new` 分支让**新对话 100% 走本地模型**
        （用户 2026-10-06 定稿「奇遇铃/匹配/第一次对话 走本地模型」）
        ⇒ 破冰期完全缺战略：只会「爽」，不会「推进」，也不会「钓她主动」。

    这里**复用** `soul_brain.PERSONA` 而不是手抄一份：
      ① 它已过 `soul_persona.rewrite()` 做双开身份隔离（实例1→阿凯/沈阳），复用即自动隔离；
      ② 守则现在已经散在 6 处硬编码，再加一处手工拷贝迟早分叉 —— 这里刻意做成第 7 处
         **引用**而非拷贝。
    任何异常返回 ''（fail-open，绝不让本地回复链路挂掉）。
    """
    try:
        import soul_brain as _SB
        return _SB.PERSONA
    except Exception:
        return ""


def _stage_line_of(sid, name=""):
    """sid (+昵称) → 「当前关系阶段 + 亲密度档位」摘要（供提示词注入）；拿不到 → ''。

    例：
      `熟悉（41 轮 · 我 15 / 她 26）· 本阶段目标：信息交换 + 情绪共鸣，进入熟人区`
      `亲密度 L2（按轮数推定） ｜ 本档行动清单：L2 私人化｜生活细节互换：吃啥、住哪…`

    ⭐ 2026-10-06 用户追问「8~12 轮是不是太少了」暴露的缺陷：
      提示词里只有 PERSONA 那句「每 8~12 轮升温一档」，却**没有任何地方告诉模型现在是第几轮**
      ⇒ 该规则无法执行（模型不知进度、也不知此人聊了多少轮）；
      同时阶段判断只活在 `soul_progress`/`soul_review` 两份报告里，生成链路完全不知道阶段。
      现在注入真实阶段（口径 = 用户 2026-09-29 定：轮 = senderId 变化段数，从基线起算），
      并接上 `soul_db` 的**亲密度阶梯 L0~L4**（用户「接进链路」；此前是死代码）。
    fail-open：任何异常返回 ''，绝不影响回复链路。
    """
    if not sid:
        return ""
    try:
        import soul_stage as _sg
        return _sg.turn_line(sid, name)
    except Exception:
        return ""


def _stuck_line_of(hist):
    """从 hist 取我最近几句 → 「死磕警报」一行；没死磕 / 异常 → ''。

    ⭐ 2026-10-06 用户批准（审计第 4 条「白跑」）：这个检测原先**只在发送闸里跑**
      （`soul_reply._topic_stuck_warn`，实测抓到过 风止遇你「鸡蛋」6 轮、初见「稀饭」5 轮、
      漩涡鸣人「小说」4 轮），但那一行明写「**只告警不阻断**」→ 只 print 给后台看，
      **生成侧完全不知道**，等于白跑。
      现在用同一份实现（`soul_rules.stuck_hot`）在**生成前**跑一次，命中就把**脚本实测出的**
      死磕词直写进提示词「这些词一个都不许再出现」——从泛泛守则升级成**准硬约束**。
      模型最需要的不是「别死磕」这三个字，而是「**哪个词**别再提」。
    fail-open：任何异常返回 ''，绝不影响回复链路。
    """
    try:
        import soul_rules as _R
        mine = [str(h.get("text") or "") for h in (hist or [])
                if h.get("role") == "me"][-3:]
        return _R.stuck_line(mine)
    except Exception:
        return ""


def gen_reply(her_msg, hist, attempt=0, banned=None, stage_line="", stuck_line=""):
    """生成回复。attempt>0 = 上一稿被闸剔掉了，换要求重来（**换话题/换说法**）。

    stage_line: 「当前关系阶段」一行摘要（soul_stage.turn_line），空串 = 不注入。
    stuck_line: 「死磕警报」（soul_rules.stuck_line），空串 = 没死磕。
    """
    ctx = "".join(("我: " if h["role"] == "me" else "她: ") + h["text"] + "\n" for h in hist)
    scarce = "" if len(hist) >= 3 else "（上下文很少，别硬接、别乱猜，回得短一点）\n"
    if attempt > 0:
        scarce += ("⚠️ 你上一稿被判为「炒冷饭/复述/不符人设」被砍掉了。\n"
                   "**必须换完全不同的话**：可以反问她、说自己这边的事、或换个新话题，\n"
                   "绝不能再出现下面这些句子（包括意思相近的）：\n%s\n"
                   % ("\n".join("· " + str(b)[:30] for b in (banned or [])[:6]) or "· （你自己刚说过的）"))
    # 【优先级·用户 2026-10-06 定稿】本地兜底也按同一套优先级排。
    #   ⭐ 2026-10-06「单一数据源」：直接引用 soul_rules（与智囊团同一份），不再手抄一份。
    #   ⭐ 2026-10-06 审计第 5 条「去重」：**这里不再写爽感四要素**——
    #      `PERSONA` 里的 `PUNCH`（长版，含四个来源详解）已随 `_strat_blk` 注入，
    #      再叠一份 `PUNCH_BRIEF` 是同一件事讲两遍：8B 小模型不像大模型会「取最严那条听」，
    #      重复只占预算、冲淡重点。实测去重前本地 prompt 1543 字里有
    #      `爽感铁律`×1＋`四要素`×1＋`有画面`×3、`一次只说一件事`×2。
    #      红线禁语（REVERSE/WEAK →FORBID）同样已在 PERSONA 内，也不重复。
    try:
        import soul_rules as _R
        xiang = _R.PRIORITY + "\n"
    except Exception:
        xiang = ""
    # ⭐ 2026-10-06 审计第 4 条：死磕警报（脚本实测，非猜测）；没死磕就是 ''。
    _stuck_blk = stuck_line if stuck_line else ""
    _now = _env_now()
    _strat = _strategy()
    # ⭐ 2026-10-06：本地模型补「战略铁律」（来源 = soul_brain.PERSONA，单一引用）。
    #   新对话 100% 走本地 ⇒ 破冰期必须也有「她主动·我享受」，否则只会爽、不会推进。
    #   ⭐ 2026-10-06 清理：标题里原先那句「来自与智囊团同一份文本」是**实现细节**，
    #      对模型毫无意义（它不知道什么叫智囊团），纯浪费 token，删掉。
    _strat_blk = ("【我的战略铁律·最高优先级】\n%s\n\n" % _strat) if _strat else ""
    # ⭐ 2026-10-06：本地模型也注入「当前关系阶段」（新对话 100% 走本地，最需要知道进度）。
    _stage_blk = ("【当前关系阶段 + 亲密度档位·用户 2026-09-29 / 09-26 定稿，据此判断该不该升温】\n"
                  "%s\n"
                  "（⚠️ 到哪个阶段就做哪个阶段的事：没到不要硬拉，到了就自然往目标走；别跳步。）\n\n"
                  % stage_line) if stage_line else ""
    # ⭐ 2026-10-06「塌房禁语」源头拦截（配合 gate 护栏双保险）：8B 模型上下文薄时
    #   容易退化出「我玩48岁母单」这类自我塌房，从源头禁掉，减少护栏触发重试的浪费。
    _ban_blk = ("【硬性禁语·踩线整条作废】回复里**绝不允许**出现：年龄数字（如 48岁）、"
                "「母单/离异/带娃/二婚/丧偶/单亲」、「我玩/我撩/我泡+某人」这类词。\n\n")
    p = ("%s【你俩最近的对话，按时间顺序】\n%s\n"
         "【她刚发来的这一句】\n%s\n\n"
         "%s"
         "%s"
         "%s"
         "%s"
         "%s"
         "你是%s。请**接着上下文**回复她："
         "直接输出 %d 条消息，每行一条、≤20 字、口语、不要编号、不要解释、"
         "**不要重复你自己刚说过的话**、不要复述她的话、不要编造上下文里没有的人和事。%s"
         % ((_now + "\n") if _now else "", ctx or "(这是你俩首次对话)\n",
            her_msg, xiang, _stuck_blk, _strat_blk, _stage_blk, _ban_blk,
            _who("reply"), N_MSG, scarce))
    return _llm(p, temp=(0.8 if attempt == 0 else 0.95))


def gen_wake(name, hist, idle_h):
    ctx = "".join(("我: " if h["role"] == "me" else "她: ") + h["text"] + "\n" for h in hist)
    _now = _env_now()
    # ⭐ 2026-10-06 单一数据源：爽感四要素 + 优先级改引用 soul_rules
    #   （此前手抄一份，且「重庆式」没过 rewrite → vm1 会漏成重庆话，双开串味）。
    try:
        import soul_rules as _R
        _rules = "⭐ " + _R.PUNCH_BRIEF + "\n" + _R.PRIORITY + "\n"
    except Exception:
        _rules = ""
    p = (("%s【你和「%s」之前聊过（按时间序）】\n%s\n"
          "（这段对话已经冷了约 %.0f 小时，最后是我说话、她没接。）\n\n"
          "你是%s。用你的口吻**换个新话题**自然开口一句，"
          "≤18 字，口语、轻松、不刻意。**不要问「在吗」「最近好吗」「怎么不理我」**，"
          "别重复上面出现过的内容，不要说教，不要编造。\n"
          % ((_now + "\n") if _now else "", name, ctx or "(几乎没有聊天记录)",
             idle_h, _who("wake")))
         + _rules
         + "直接输出要发的 1 句，不要引号、不要解释。")
    return _llm(p, temp=0.9)


# ══════════════════════ 三态动作 ══════════════════════
# ═══════════════ 画面健康熔断与分级恢复（2026-10-04）═══════════════
# 为什么必须有（用户实测事故）：
#   MuMu 渲染窗口挂死（IsHungAppWindow=1）→ 整帧纯白 → PrintWindow 仍返回 1 →
#   OCR 全空 → `_on_chat_list()` 取色失败 → `_goto_chat_list()` 判"页面异常" →
#   `reply()` 放弃发送。守护**完全无感**：既没报错，也没恢复，只是每轮空转。
#   更糟：`_goto_chat_list()` 异常时会连做多次 `am start --activity-clear-top`
#   （反复重启 App）+ 每轮 `_scroll_top()`（最多 25 次滑动+截图），在画面已不健康时
#   形成"失败→猛敲模拟器→更死"的正反馈。
# 本熔断：先自己取一帧截图判定健康；不健康 → 本轮**不做任何 UI 操作**，
#   达到阈值后分级恢复（① 重启 Soul 应用 → ② 重启 MuMu 实例），并保证恢复冷却。
_SHOT_HEALTH = {"fail": 0, "recover_at": 0.0, "stage": 0}


def _shot_ok():
    """取一帧新截图并判定是否可用（空白/失败都算不健康）"""
    try:
        p = soul.screenshot(force=True)
        return bool(p) and os.path.exists(p) and os.path.getsize(p) > 5000
    except Exception:
        return False


def _restart_vm():
    """重启 MuMu 实例（shutdown → launch）。必须在 Session 1 执行才有效。"""
    MGR = r"D:\MuMuPlayer\nx_main\MuMuManager.exe"
    NW = 0x08000000
    for args in ([MGR, "control", "-v", VM, "shutdown"],):
        try:
            subprocess.run(args, timeout=180, capture_output=True, creationflags=NW)
            log("     MuMu shutdown 已下发")
        except Exception as e:
            log("     MuMu shutdown 异常: %r" % (e,))
    time.sleep(15)
    try:
        subprocess.run([MGR, "control", "-v", VM, "launch"], timeout=180,
                       capture_output=True, creationflags=NW)
        log("     MuMu launch 已下发")
    except Exception as e:
        log("     MuMu launch 异常: %r" % (e,))
    # 等 Android 与 Soul 起来（下一轮 _ensure_device 会再兜一次）
    for _ in range(12):
        time.sleep(10)
        if _shot_ok():
            return True
    return False


def _display_guard():
    """画面健康检查 + 分级恢复。返回 True = 画面健康，本轮可继续做 UI 操作。

    分级（每次恢复之间有 RECOVER_COOL 冷却，避免恢复风暴）：
      第 1 次：重启 Soul 应用（轻）
      第 2 次及以后：重启 MuMu 实例（重）
    """
    if _shot_ok():
        if _SHOT_HEALTH["fail"]:
            log("  ✅ 画面已恢复（此前连续 %d 次截图异常）" % _SHOT_HEALTH["fail"])
        _SHOT_HEALTH["fail"] = 0
        _SHOT_HEALTH["stage"] = 0
        return True
    _SHOT_HEALTH["fail"] += 1
    log("  ⛔ 截图异常（连续第 %d/%d 次）→ 模拟器画面可能已挂死"
        % (_SHOT_HEALTH["fail"], SHOT_FAIL_MAX))
    if _SHOT_HEALTH["fail"] < SHOT_FAIL_MAX:
        return False
    if time.time() < _SHOT_HEALTH["recover_at"]:
        log("  ⏸ 恢复冷却中（%d 秒后可再试）"
            % max(0, int(_SHOT_HEALTH["recover_at"] - time.time())))
        return False
    _SHOT_HEALTH["recover_at"] = time.time() + RECOVER_COOL
    _SHOT_HEALTH["fail"] = 0
    _SHOT_HEALTH["stage"] += 1
    if _SHOT_HEALTH["stage"] == 1:
        log("  🔧 恢复 1/2：重启 Soul 应用")
        try:
            soul.restart_app(wait=20)
        except Exception as e:
            log("     重启 Soul 失败: %r" % (e,))
    else:
        log("  🔧 恢复 2/2：重启 MuMu 实例（shutdown → launch）")
        _restart_vm()
    return False


def _ensure_device():
    for i in (1, 2):
        try:
            d = soul.display(refresh=True)
            if d is None or not soul.app_running():
                # ⭐ 2026-10-05 治本：MuMu 上 Soul 会自己掉后台（进程在但无 resumed）
                #   → 之前只在"进程不在"时才拉起，掉后台就漏了 → 连续失败计数。
                #   现在 display=None（无论进程在不在）都拉回前台。
                log("  设备：Soul 不在前台/未运行 → 拉起")
                soul.launch_app(wait=12, wait_disp=20)
                d = soul.display(refresh=True)
            if soul.app_running() and d is not None:
                return True
            log("  设备：第 %d 次未就绪 (running=%s, display=%s)"
                % (i, soul.app_running(), d))
        except Exception as e:
            log("  设备异常：%r" % (e,))
        time.sleep(8)
    return False


def gen_pick(cand, her_msg, hist):
    """本地 jianghua 从智囊团多条候选里挑最贴合人设的一条（可微调语气，别大改）"""
    ctx = "".join(("我: " if h["role"] == "me" else "她: ") + h["text"] + "\n" for h in hist)
    _now = _env_now()
    # ⭐ 2026-10-06 单一数据源：选择优先级 + 反面清单改引用 soul_rules（不再手抄一份）。
    try:
        import soul_rules as _R
        _rules = _R.PRIORITY + "\n" + _R.NEG_BRIEF
    except Exception:
        _rules = ""
    p = (("%s【你俩最近的对话】\n%s\n【她刚发来的这一句】\n%s\n\n"
          "【智囊团给出的候选话术（每条很短）】\n%s\n\n"
          "你是%s。"
          "从候选里**挑 1 条最贴合你人设和当前语境的**，可微调语气但别大改。\n"
          % ((_now + "\n") if _now else "", ctx or "(这是你俩首次对话)\n", her_msg,
             "\n".join("· " + str(c) for c in cand), _who("pick")))
         + _rules
         + "直接输出那一条（<=20 字），不要解释、不要编号。")
    return _llm(p, temp=0.7)


def _split_msg(t, limit=30):
    """单条 >30 字拆成 2 条短消息（用户 2026-10-03：字数太多分两次发送）"""
    if len(t) <= limit:
        return [t]
    for sep in ("。", "！", "？", "，", ",", "；", " "):
        if sep in t:
            a, b = t.rsplit(sep, 1)
            a = a + sep
            if len(a) >= 8 and len(a) <= limit + 10:
                return [a.strip(), b.strip()]
    mid = len(t) // 2
    return [t[:mid].rstrip(), t[mid:].lstrip()]


_ACCT_GATE = {"me": None, "ok": True, "ts": 0.0}

def _account_gate_ok(force=False):
    """⭐ 2026-10-05 改（用户口径）：**跟随 App 内切号**，不再拒绝发送。
    ⭐ 2026-10-06 force=True：发送前强制刷新（跳过 60s 缓存），保证 ME/SESS 与设备一致。

    背景：用户放弃「多实例 / 应用内分身」，改为**在 Soul App 里手动切号**。
    账号本来就会变，原来那套「设备账号 ≠ 配置账号 → 本轮全部跳过」只会把机器人卡死
    （实测 10-05 22:48 之后一个字都发不出去）。

    新行为：设备当前账号与配置不一致 → **自动把配置同步为当前账号**并继续发送；
    探测不到（返回 None）→ 放行、保持原配置（fail-open，不因探测抖动误拦）。

    ⭐ 2026-10-05 二次修（用户：「如何确认当前账号 只需要点击导航栏的 自己即可」）：
    探测源换成 **App prefs 权威字段**（`im.prefs_identity()` → sp_info_gather.userid /
    soul_startup.sp_key_crash_uid_name）。原因：老办法 `_device_me()` 走的是
    `active_sess()`「谁最后收到消息」——**切号后会持续猜错**（实测把主号抬头仰望星空
    96691646 猜成了账号2 离殇 402857053）。prefs 是 App 自己写的当前登录态，切号即时生效。
    结果缓存 60s（原来是 300s —— 切号后最多要等 5 分钟才跟上，太长）。
    """
    now = time.time()
    if not force and now - _ACCT_GATE["ts"] < 60:
        return _ACCT_GATE["ok"]
    dev_me = None
    try:
        # ① 权威探测（prefs）→ 同时给出 uid 与 sess
        try:
            p_uid, p_sess = im.prefs_identity(force=True)
        except Exception:
            p_uid, p_sess = None, None
        # ② 兜底探测（库内推断）
        try:
            dev_me = p_uid or im._device_me()
        except Exception:
            dev_me = p_uid
        if dev_me:
            cfg_me = str(im.ME)
            if dev_me != cfg_me:
                log("  🔄 账号跟随：设备当前账号 %s ≠ 配置 %s → 已自动同步（App 内切号）"
                    % (dev_me, cfg_me))
                try:
                    im.ME = str(dev_me)
                except Exception:
                    pass
            # ⭐ 2026-10-06 修（P0#3 切号串号）：把探测到的设备账号**同步进 `_acct` 缓存**。
            #   `_memdb()` / `_state_path()` 都经 `_acct.cur_uid()` 选库/状态文件；只改 `im.ME`
            #   而 `_acct` 里的 uid 还是旧号（30s 缓存或 override 过期前的旧值）时，`_memdb()`
            #   会读到**另一个号的累积库** → 串号。dev_me 为真才同步；None 时不动（不猜，fail-closed）。
            try:
                _acct.set_uid(str(dev_me))
            except Exception:
                pass
        # 会话密钥也跟随：切号后 IM-SDK-<SESS> 库名 / chat_<SESS> 表名都会变
        # ⭐ 2026-10-05：优先 prefs 的 crash_uid_name（权威，切号即时），其次库内推断
        try:
            dev_sess = p_sess or im._device_active_sess()
            if dev_sess and dev_sess != im.SESS:
                log("  🔄 账号跟随：会话密钥 %s… → %s…（切号）"
                    % (str(im.SESS)[:12], str(dev_sess)[:12]))
                try:
                    im.SESS = dev_sess
                except Exception:
                    pass
        except Exception:
            pass
    except Exception:
        pass
    _ACCT_GATE.update({"me": dev_me, "ok": True, "ts": now})
    return True


def _brain_within(hist, her_text, budget=None, stage_line="", stuck_line=""):
    """在 budget 秒内要智囊团候选；超时 / 异常 / 无候选 → None。

    ⭐ 2026-10-06 用户口径：「**先问智囊团，60s 拿不到才用预生成池**」（用于**老对话**）。
    新对话（她说过 ≤NEW_ROUNDS 轮）**根本不走智囊团**（直接本地层），所以这条预算只对老对话生效；
    老对话拿不到候选就**不发**（不降级），不会去用预生成池。
    用后台线程 + `join(budget)` 实现**墙钟硬预算**：智囊团内部的超时（快通道 1 次调用
    ≤40s；深通道 3 专家并行 + 裁判，最坏 ~80s）只是"单次 http"的上限，叠加起来会超 60s。
    超时后**不杀线程**（Python 杀不掉），让它自生自灭 —— 它是 daemon 线程，不阻塞退出；
    它若晚点回来了，结果也只写进 brain.log，不会回来污染这一轮。

    stage_line: 「当前关系阶段」一行摘要（soul_stage.turn_line），透传给智囊团提示词。
    stuck_line: 「死磕警报」（soul_rules.stuck_line），透传给智囊团提示词（空串 = 没死磕）。
    """
    budget = BRAIN_BUDGET if budget is None else budget
    import threading
    box = {}

    def _run():
        try:
            import soul_brain as _SB
            box["v"] = _SB.brain_reply(hist, her_text, stage_line=stage_line,
                                       stuck_line=stuck_line)
        except Exception as e:
            box["e"] = e

    t0 = time.time()
    th = threading.Thread(target=_run, daemon=True)
    th.start()
    th.join(budget)
    if th.is_alive():
        log("   ⏱ 智囊团 %.0fs 内没出结果 → 判「拿不到」（老对话→不发，不降级）" % budget)
        return None
    if "e" in box:
        log("     !! 在线智囊团异常（%r）" % (box["e"],))
        return None
    if box.get("v"):
        log("   ⏱ 智囊团耗时 %.1fs" % (time.time() - t0))
    return box.get("v")


def do_reply(name, her_text, st, sid_hint=None):
    """对单个待回：生成 → 发送。返回 'SENT'/'SKIP'/'FAIL'
    ⭐ 2026-10-03 用户方案：在线智囊团(三系辩论)先出多条短话术 → 本地挑一条 → 太长拆两条发
    降级保护：智囊团不可用 → 本地 jianghua 生成（原链路）
    """
    # ⭐ 2026-10-05 账号安全闸：设备账号≠配置账号 → 直接跳过，绝不代发（防串号）
    # ⭐ 2026-10-06 force=True：发送前强制刷新，杜绝 ME/SESS 串号导致的校验误判
    if not _account_gate_ok(force=True):
        return "SKIP"
    # ⭐ 2026-10-04（F1）**sid 优先**：pending() 带出的 sessionId 来自 Soul 自己的会话表，
    #   权威、不依赖 OCR 昵称。原来先按昵称猜 uid，后果有二：
    #     ① 同名多人（实测「小仙女」2 个精确同名）→ 猜错人 → 上下文取自别人；
    #     ② soul_reply 的 _uid_of 对此"拒绝猜" → 真人被自家防串台闸拒发。
    _wake_forgive(st, name)      # ⭐ 她主动回话了 → 唤醒日计数清零（重新给机会）
    uid = _uid_from_sid(sid_hint) if sid_hint else None
    _uid_nick = _uid_by_name(name)
    if not uid:
        uid = _uid_nick
        if uid:
            log("  ⚠ 会话 id 未解析出 uid → 退回昵称解析（%s）" % name)
    elif _uid_nick and str(_uid_nick) != str(uid):
        log("  ⚑ 昵称解析 uid=%s 与 sid 权威 uid=%s 不一致 → 以 sid 为准（%s）"
            % (_uid_nick, uid, name))
    sid = sid_hint or (_sid_of(uid) if uid else None)
    if not sid:
        log("  ⛓ 会话 id 与昵称都没解析出身份 → 本次无历史上下文（%s）" % name)
    # ⭐ 2026-10-06 用户口径「**没有就算了 不要死磕**」：
    #   设备端已无此会话（Soul 把本地会话删了）→ 直接放弃，别硬跑 ~100s 的 UI 全路径。
    #   实测 杨三岁/意中人♑️/甜心姐姐丶/💕小謎 在设备端 33 个库里零命中，每轮却各吃 ~100s，
    #   一轮 480s 里近一半被它们耗光，还饿死匹配/唤醒流程。
    #   只在 uid 来自 **sid 权威反查** 时才拦（纯昵称猜出来的 uid 可能是错的，不敢据此判死）；
    #   设备数据拿不到 → has_device_session 恒 True，照旧走原路径（绝不误杀）。
    if sid_hint and uid and not im.has_device_session(uid):
        n = _bump_try(st, name, her_text)
        log("  ⛔ 设备端已无「%s」的会话（uid=%s，已被 Soul 删除）→ 直接放弃，不硬跑 UI"
            "（已试 %d 次，够了就自动停手）" % (name, uid, n))
        return "SKIP"
    hist = hist_of(sid)
    my_recent = [h["text"] for h in hist if h["role"] == "me"]
    log("  「%s」她发来: %s" % (name, str(her_text)[:32]))
    # ⭐ 2026-10-06 用户口径（定稿）：
    #   · **匹配来的新用户**（她说过 ≤NEW_ROUNDS 轮）→ 允许用**本地模型**生成（上下文少，够用且快）
    #   · **超过 NEW_ROUNDS 轮的老对话** → **一律走智囊团**；智囊团拿不到可用候选就**不发**，
    #     **绝不降级本地**（老对话用弱提示词的本地模型＝死磕旧话题、干巴巴，实测就是这么聊崩的）
    #   计数口径：hist 里 role=="her" 的条数（她说过几句）。
    _her_n = sum(1 for h in hist if h.get("role") == "her")
    _is_new = _her_n <= NEW_ROUNDS
    _no_draft = False

    # ══ 生成链（用户 2026-10-06 定稿，按对话轮数**二分**）══
    #   · **新对话**（她说过 ≤NEW_ROUNDS 轮）→ **直接走本地层**：预生成池 → 本地模型
    #       「奇遇铃 / 匹配 / 第一次对话 一直都是本地模型，走本地模型」
    #       破冰期没有上下文，智囊团本来也用不上；开场白(profile_opening)同样是纯本地。
    #   · **老对话**（她说过 >NEW_ROUNDS 轮）→ **只走智囊团**（硬预算 BRAIN_BUDGET=60s）；
    #       拿不到候选就**不发**，绝不降级本地/预生成池。
    # ⭐ 2026-10-06：算一次「当前关系阶段」，智囊团与本地模型**共用同一行**
    #   （口径 = soul_stage，与 soul_progress / soul_review 两份报告同源）。
    _stage_line = _stage_line_of(sid, name)
    if _stage_line:
        log("   📊 当前阶段：%s" % _stage_line.replace("\n", " ｜ "))
    # ⭐ 2026-10-06 审计第 4 条：生成**前**跑一次死磕检测，命中就写进提示词
    #   （同一份实现 `soul_rules.stuck_hot`，发送侧那个老 _topic_stuck_warn 继续保留作告警）。
    _stuck_line = _stuck_line_of(hist)
    if _stuck_line:
        log("   ⚠️ 死磕警报（注入提示词）：%s" % _stuck_line.replace("\n", " ｜ "))
    gated, raw, dropped = [], "", ""
    # ⭐ 2026-10-06 修：跨轮 banned 列表（发送失败/未知过的文本指纹）两条分支都要拿到，
    #   新对话喂给 gen_reply 的 banned 参数；老对话即使本轮不走本地 gen_reply，
    #   也先取好（避免后续重构/回退路径漏掉）。
    import soul_pregen as _pregen
    try:
        _banned_extra = list(_pregen.banned_list(name))
    except Exception:
        _banned_extra = []
    if _is_new:
        log("   ↳ 新对话（她说过 %d 轮 ≤ %d）→ 走本地层（预生成池 → 本地模型），不问智囊团"
            % (_her_n, NEW_ROUNDS))
        _pg = None
        try:
            _pg = _pregen.take(name, her_text)
        except Exception as _e:
            log("     !! 预生成池异常（%r）→ 走本地模型" % (_e,))
        if _pg:
            # ⭐ 2026-10-06（P2）：预生成命中候选先过跨轮 banned（发送失败/未知过的文本不再发）
            _pg = [c for c in _pg if _pregen._fp(c) not in set(_banned_extra)]
            gated, dropped = gate("\n".join(_pg), her_text, N_MSG, my_recent)
            log("   ⚡预生成池命中（%s）→ 闸后: %s | 剔: %s" % (name, gated, dropped))
        if not gated:
            for attempt in range(RETRY_MAX):
                try:
                    raw, ms = gen_reply(her_text, hist, attempt=attempt,
                                        banned=(raw.splitlines() + my_recent[-3:]
                                                + _banned_extra),
                                        stage_line=_stage_line,
                                        stuck_line=_stuck_line)
                except Exception as e:
                    log("     !! 生成失败(第%d次) %s: %r" % (attempt + 1, name, e))
                    continue
                gated, dropped = gate(raw, her_text, N_MSG, my_recent)
                log("     [第%d次·本地] %r | 闸后: %s | 剔: %s"
                    % (attempt + 1, raw, gated, dropped))
                if gated:
                    break
    else:
        log("   ↳ 老对话（她说过 %d 轮 > %d）→ 只走智囊团" % (_her_n, NEW_ROUNDS))
        _bt = _brain_within(hist, her_text, stage_line=_stage_line,
                            stuck_line=_stuck_line)
        if _bt:
            try:
                raw, _ms = gen_pick(_bt, her_text, hist)
            except Exception as e:
                log("     !! 本地挑选失败（%r）→ 取第 1 条" % (e,))
                raw = _bt[0]
            gated, dropped = gate(raw, her_text, 1, my_recent)
            log("   ⭐ 智囊团 %d 条=%s | 本地挑: %r | 闸后: %s | 剔: %s"
                % (len(_bt), _bt, raw, gated, dropped))
            if not gated:
                for _t in _bt:            # 挑的这条被闸剔 → 逐条试
                    _g2, _d2 = gate(_t, her_text, 1, my_recent)
                    if _g2:
                        gated, dropped = _g2, _d2
                        break
        if not gated:
            # 老对话：**不降级本地**，到此为止（下面统一走「不发」分支）
            _no_draft = True
            log("   ⚠ 老对话（她说过 %d 轮 > %d）智囊团未出可用候选 → **不发**（不降级本地）"
                % (_her_n, NEW_ROUNDS))
    if not gated:
        n = _bump_try(st, name, her_text)
        if _no_draft:
            log("     ⛔ 老对话智囊团无可用候选 → **不发**（不降级本地）；已试 %d 次" % n)
        else:
            log("     ⛔ %d 稿全被闸剔空 → **不发**（宁可沉默）；已试 %d 次"
                % (RETRY_MAX, n))
        return "SKIP"

    # ⭐ 2026-10-03 用户方案：字数太多拆成两条短消息发送（拆分条豁免连发闸）
    _send = []
    for _t in gated:
        _send.extend(_split_msg(_t))
    send_name = _norm_nick(name)
    r = "FAIL"
    try:
        # ⭐ 2026-10-04（F1）：把 sid 权威 uid 注入 soul_reply —— 它内部所有
        #   "_uid_of() 拒绝猜 → 放弃/校验失败" 的分支（_on_session_of / _verify_by_content /
        #   verify_sent / _my_recent_texts / 频率闸）都会因此拿到正确的人。
        sr.set_uid_hint(uid)
        r = _deliver(send_name, _send, my_recent=my_recent, allow_chain=True)
    finally:
        sr.set_uid_hint(None)
    if r == "SENT":
        log("     ⚡已发 %s: %s" % (name, " / ".join(_send)))
        _spend(st, 1)
        return "SENT"
    # ⭐ 2026-10-06（P1/P2）：校验未知 = 可能已发出 → 记 banned 防重发，
    #   **不计重试闸**（不调 _bump_try），交由下一轮 banned 过滤兜底。
    if r == "UNKNOWN":
        log("     ⚠️校验未知（可能已发）→ 记 banned 防重发，不计重试闸")
        try:
            import soul_pregen as _pregen
            _pregen.banned_add(name, _send)
        except Exception as _e:
            log("     !! banned 落盘异常: %r" % (_e,))
        return "UNKNOWN"
    # ⭐ 2026-10-06（P2）：确认发送失败的文本记 banned（跨轮不再死磕同句）；SKIP 硬闸不写。
    if r == "FAIL":
        try:
            import soul_pregen as _pregen
            _pregen.banned_add(name, _send)
        except Exception as _e:
            log("     !! banned 落盘异常: %r" % (_e,))
    n = _bump_try(st, name, her_text)
    log("     %s（第 %d 次未成，计入重试闸）"
        % ("⛔硬闸拦截" if r == "SKIP" else "⚠️发送未成功", n))
    return "SKIP" if r == "SKIP" else "FAIL"


def _wake_day(st, name):
    """该人「最近一次被唤醒」的日期（YYYY-MM-DD）；从未唤醒 → None。"""
    try:
        rec = (st.get("wake_days") or {}).get(name)
        return rec[1] if rec else None
    except Exception:
        return None


def _mark_wake_day(st, name):
    """记一次「今天唤醒过 TA」。累计达 WAKE_MAX_DAYS 天 → 永久放弃主动唤醒。

    返回 (累计天数, 是否刚被永久放弃)；被放弃时从 wake_days 移入 wake_off。
    用户口径：今天唤没回、明天唤没回、后天唤还是没回 → 就别理了。
    """
    wd = st.setdefault("wake_days", {})
    try:
        n = int((wd.get(name) or [0, ""])[0]) + 1
    except Exception:
        n = 1
    wd[name] = [n, _today()]
    if n >= WAKE_MAX_DAYS:
        off = st.setdefault("wake_off", {})
        off[name] = _today()
        wd.pop(name, None)
        return n, True
    return n, False


def _wake_forgive(st, name):
    """她主动回话了 → 唤醒日计数清零（重新给机会）。
    **不清** wake_off：已永久放弃的人即使回话也不再被主动唤醒（只照常回复）。"""
    wd = st.get("wake_days")
    if isinstance(wd, dict):
        wd.pop(name, None)


# ══════════════════════ 🔔 奇遇铃：全局最高优先级 ══════════════════════
# ⭐ 2026-10-05 用户口径（优先级，**抢占式**）；2026-10-06 补「已读待回」：
#     ① 奇遇铃  >  ② 待回消息（未读待回 > **已读待回**）  >  ③ 星球匹配  >  ④ 唤醒老联系人
#   含义：任何时候只要弹出奇遇铃，都要**立刻中断**当前动作（回消息 / 匹配 / 唤醒）
#   去把铃处理掉（点「立即私聊」进会话 + 发一句开场白），处理完再回到原流程。
#   铃的判定必须用 soul.is_love_bell()（**当前真的弹着**），不能用聊天列表里的
#   「奇遇铃-稍后再聊」会话条目 —— 那只表示"最近弹过"，会把已处理的铃一直误判。
def _bell_live():
    """当前是否弹着奇遇铃。任何异常一律当"无铃"（绝不因它阻断主流程）。"""
    try:
        return bool(soul.is_love_bell())
    except Exception:
        return False


def _do_love_bell(st, where="轮首"):
    """第 1 优先级动作：处理奇遇铃。返回 True = 确实处理了一个铃。

    步骤（用户 2026-09-29 铁律「挡路了也要先奇遇铃」）：
      点「立即私聊」→ 进入与该人的会话 → 发一句开场白（个性化，抓不到走兜底）。
    """
    try:
        name, ok = soul.accept_love_bell()
    except Exception as e:
        log("  !! 奇遇铃处理异常: %r" % (e,))
        return False
    if not ok:
        return False
    st["last_bell"] = time.time()
    log("  🔔 奇遇铃（%s）→ 已点「立即私聊」，对方「%s」" % (where, name))
    # ⭐ 2026-10-06 修（P1#4 根因②）：读不出对方昵称 → **不发送，且不静默当已处理**。
    #   fail-closed：绝不猜人发错对象；返回 False 让上层看到"这个铃本次没处理成"。
    if not name:
        log("  ⚠️ 奇遇铃：读不出对方昵称（love_bell_name=None）→ 本次不发送（绝不猜人）")
        return False
    # ⭐ 2026-10-06 整改③：开场白**发送前必须过 `gate()`**（红线/违禁/长度等），
    #   未过闸的句子一律不发（fail-closed）；profile 不过 → 回退 fallback 的**过闸版本**。
    def _passed(txt):
        if not txt:
            return None
        try:
            _kept, _dropped = gate(txt, "", 1)
        except Exception as _e:
            log("     !! 奇遇铃开场白过闸异常（按不过处理）: %r" % (_e,))
            return None
        if _dropped:
            log("     · 奇遇铃开场白被闸剔除：%s" % (_dropped,))
        return _kept[0] if _kept else None
    msg = None
    try:
        import soul_match as M
        msg = _passed(M.profile_opening()) or _passed(M.fallback_opening(name))
    except Exception as e:
        log("     !! 奇遇铃开场白生成异常: %r" % (e,))
    if not msg:
        log("  ⚠️ 奇遇铃开场白未过闸（profile/fallback 皆不可用）→ 本轮不发送")
        return True
    try:
        with _Tick("奇遇铃·%s" % name, st):
            # ⭐ 2026-10-06 修（P1#4 根因①）：奇遇铃点完「立即私聊」**已在该人会话页**，
            #   必须走 `allow_chain=True` 全路径 —— 命中 soul_reply `_on_session_of` 短路
            #   「已在她的会话页 → 直接发」。走快速路径会先回聊天列表、find 找不到人 → 发不出。
            _r = _deliver(name, [msg], allow_chain=True)
        log("     开场白「%s」→ %s" % (msg, _r))
        if _r == "SENT":
            _spend(st, 1)
    except Exception as e:
        log("     !! 奇遇铃开场白发送异常: %r" % (e,))
    return True


def _bell_preempt(st, where):
    """抢占检查：动作进行中弹铃 → 立即处理，返回 True（表示被抢占）。
    在回复循环 / 匹配 / 唤醒的长流程里周期性调用。"""
    if not _bell_live():
        return False
    log("  ⚡ %s 中发现奇遇铃 → 中断当前动作，优先处理铃（第 1 优先级）" % where)
    return _do_love_bell(st, where=where)


# ══════════════════ ghost 补偿（2026-10-06 用户口径）══════════════════
# 背景：SESSION_HINT 过时导致「匹配成功却判 no_match 直接退出」的一批人**一条消息都没发**
#   （累计 23 个）。根因已修（commit f8446a8），但**存量 ghost 必须补发**
#   —— 项目铁律「匹配到人必须发」（见 soul_match.match_once）。
# 判定（稳健，**不依赖 ME**，切号后 ME 陈旧也不误判）：
#   会话存在 ∧ 全程 **0 条** msgType='1' 且 text 非空的文本消息（只带 27/35 系统卡片）∧
#   首条消息 ≤ GHOST_DAYS 天内（更老的不复活，避免突兀）。
# 排除：官方号/平台通知（复用 im._is_official / im._is_official_uid）。
# 节流：每轮最多补发 1 人（绝不批量刷 23 条，防封号）；发出后该会话即有 msgType=1
#   → 下轮判定自然不再命中，**无需额外状态文件**。
GHOST_DAYS = 3.0
_GHOST_SYS = ("平台通知", "Soul")     # 昵称含这些 → 平台通知/官方号，不补发


def _ghost_cands(days=GHOST_DAYS):
    """累积库里「匹配后从未发过开场白」的候选 [(t0, uid, name), ...]（最近优先）。
    只读；任何异常返回 []（上层绝不因此中断）。"""
    out = []
    now = time.time()
    c = sqlite3.connect("file:%s?mode=ro" % _memdb().replace("\\", "/"), uri=True, timeout=5)
    try:
        nicks = {str(u): n for u, n in c.execute("SELECT uid, name FROM nick")}
        sid2uid = {str(s): str(u) for s, u in c.execute("SELECT sessionId, toUserId FROM session")}
        rows = c.execute(
            "SELECT sessionId, MIN(localTime) AS t0 FROM chatmsg "
            "GROUP BY sessionId ORDER BY t0 DESC LIMIT 400").fetchall()
        for sid, t0 in rows:
            try:
                t = float(t0 or 0)
                if t > 1e12:                # localTime 有毫秒/秒两种口径
                    t /= 1000.0
            except Exception:
                continue
            if t <= 0 or (now - t) > days * 86400:
                continue                    # 无时间 / 太老 → 不复活
            txt = c.execute(
                "SELECT COUNT(*) FROM chatmsg WHERE sessionId=? AND msgType='1' "
                "AND text IS NOT NULL AND text!=''", (sid,)).fetchone()
            if txt and int(txt[0]) > 0:
                continue                    # 已聊过 → 不是 ghost
            uid = sid2uid.get(str(sid), "")
            name = str(nicks.get(uid) or "").strip()
            if not name:
                continue                    # 纯 uid 无昵称（平台通知常见）→ 无法定位/发送
            if im._is_official(name) or im._is_official_uid(uid):
                continue
            if any(k in name for k in _GHOST_SYS):
                continue
            out.append((t, uid, name))
    finally:
        c.close()
    return out


def _opening_passed(txt):
    """开场白过闸（红线/违禁/长度…）→ 返回到手句，未过返回 None。与奇遇铃同一道闸。"""
    if not txt:
        return None
    try:
        kept, dropped = gate(txt, "", 1)
    except Exception as e:
        log("     !! ghost 开场白过闸异常（按不过处理）: %r" % (e,))
        return None
    if dropped:
        log("     · ghost 开场白被闸剔除：%s" % (dropped,))
    return kept[0] if kept else None


def wake_ghost(st):
    """补发「匹配成功但从未发开场白」的 ghost 会话。每轮最多 1 人。
    整段异常只记日志，**绝不影响主循环**。返回补发数。"""
    try:
        cands = _ghost_cands()
    except Exception as e:
        log("  !! ghost 补偿扫描异常（跳过）: %r" % (e,))
        return 0
    if not cands:
        return 0
    log("  🔎 ghost 补偿候选 %d 人（匹配后未发开场白，首条 ≤%.0f 天）" % (len(cands), GHOST_DAYS))
    for _t0, uid, name in cands:
        try:
            if not _hour_budget(st):
                log("     … 已达本小时发送上限 → ghost 补偿暂停")
                return 0
            if not _reachable(name):
                continue                    # 全 emoji/不可定位 → 跳过
            if _cooling(st, name, "wake"):
                continue                    # 复用唤醒冷却闸
            try:
                import soul_match as M
                msg = _opening_passed(M.profile_opening()) or _opening_passed(M.fallback_opening(name))
            except Exception as e:
                log("     !! ghost 开场白生成异常 %s: %r" % (name, e))
                continue
            if not msg:
                log("     … ghost「%s」无可用开场白（未过闸）→ 跳过" % name)
                continue
            r = _deliver(name, [msg], allow_chain=False)
            if r == "SENT":
                log("  🩹 ghost 补偿：匹配后未发开场白 %d 人 → 补发 %s「%s」→ SENT"
                    % (len(cands), name, msg))
                _spend(st, 1)
                return 1
            log("     ⏭ ghost 补偿「%s」未达成（%s）→ 记一次冷却，本轮不再试" % (name, r))
            _bump_try(st, name, "wake")
            return 0
        except Exception as e:
            log("     !! ghost 补偿异常 %s: %r" % (name, e))
            continue
    return 0


def wake_old(st):
    """唤醒老联系人：_follow_mem 从**累积库**选可推进的人 → 生成新话题开场"""
    fol = _follow_mem(min_msgs=10, cool_h=WAKE_MIN_H, max_idle_h=WAKE_MAX_H,
                      skip=set((st.get("wake_off") or {}).keys()))
    if not fol:
        log("  唤醒：暂无符合条件的人（冷 %dh~%.0f天、聊过≥10句）"
            % (WAKE_MIN_H, WAKE_MAX_H / 24.0))
        return 0
    log("  唤醒候选 %d 人（冷 %.0f~%.0f 小时）" % (len(fol), fol[-1][0], fol[0][0]))
    sent = 0
    for idle_h, name, hers, total, last_text, lt in fol:
        # 🔔 第 1 优先级抢占：唤醒途中弹铃 → 中断唤醒，优先处理铃
        if _bell_live():
            log("  ⚡ 唤醒中发现奇遇铃 → 中断唤醒优先处理铃（第 1 优先级）")
            _do_love_bell(st, where="唤醒中断")
            break
        # ⭐ 2026-10-03 抢占：唤醒中途发现真待回 → 立即中断，回去处理新消息
        #   （判据与 do_match._interrupted_daemon 完全一致：可达+非假末条+可发）
        try:
            _rows = im.pending() or []
            _reach = [r for r in _rows
                      if _reachable(r[1]) and not _is_fake_last(r[3]) and _sendable(r[1])
                      and not _zombie(r)]          # ⭐ F2：僵尸待回不让位
        except Exception:
            _reach = []
        if _reach:
            log("  ⚡ 唤醒中发现 %d 个真待回 → 中断唤醒优先回消息" % len(_reach))
            break
        if sent >= WAKE_N or not _hour_budget(st):
            break
        if not _reachable(name):
            continue
        if _wake_day(st, name) == _today():
            continue                  # ⭐ 一天最多唤醒同一人一次（用户 2026-10-04）
        if _cooling(st, name, "wake"):
            continue                      # 上次唤醒没成的人，冷却期内不再试
        uid = _uid_by_name(name)
        hist = hist_of(_sid_of(uid) if uid else None)
        try:
            raw, ms = gen_wake(name, hist, idle_h)
        except Exception as e:
            log("  !! 唤醒生成失败 %s: %r" % (name, e))
            continue
        my_recent = [h["text"] for h in hist if h["role"] == "me"]
        gated, dropped = gate(raw, "", 1, my_recent)
        log("  唤醒「%s」(冷%.0fh/共%d句) 模型: %r | 闸后: %s"
            % (name, idle_h, total, raw, gated))
        if not gated:
            continue
        # ⭐ 唤醒是「顺手做的事」：只用快速路径（~25s），找不到人就跳过。
        #   实测走 soul_reply 全路径要 1~8 分钟/人（搜索通道反复读屏），
        #   会把整个巡检拖死（期间来消息也要等它），收益不划算。
        # ⭐ 2026-10-04 用户口径：唤醒「一天一次」；累计 WAKE_MAX_DAYS 天都没回
        #   → 永久放弃主动唤醒。本次仍照发，放弃自下一轮起生效。
        _dn, _giveup = _mark_wake_day(st, name)
        # ⭐ 2026-10-04：**立即落盘**。整轮结束才 save_state 是不够的——
        #   唤醒一轮可能长达 6~7 分钟（逐个 MISS 很慢，实测 406s），
        #   此刻若被守护重启/强杀，只在轮末写盘会丢掉「今天已唤 / 累计天数」
        #   → 同一天重复唤醒、3 天放弃阈值永远到不了。
        #   （实测：13:18:59 标了第 1/3 天，13:23 重启后又记成 1/3。）
        save_state(st)
        if _giveup:
            log("  🚫 唤醒「%s」累计 %d 天无回复 → 永久放弃主动唤醒（她若主动来信仍照常回复）"
                % (name, _dn))
        else:
            log("  🗓 唤醒「%s」记为第 %d/%d 天（今天不再重复唤）"
                % (name, _dn, WAKE_MAX_DAYS))
        r = None
        if USE_FAST:
            try:
                import soul_fast as sf
                r = sf.fast_reply(_norm_nick(name), gated[:1], my_recent=my_recent)
            except Exception as e:
                log("     !! 唤醒快速路径异常: %r" % (e,))
        if r == "SENT":
            log("     ✅ 已唤醒 %s: %s" % (name, gated[0]))
            _spend(st, 1)
            sent += 1
        else:
            log("     ⏭ 唤醒跳过「%s」（%s）"
                % (name, {"GATED": "硬闸拦截", "MISS": "列表 1 页内没找到（不翻全表）",
                          "FAIL": "发送失败", "DRY": "干跑"}.get(r, "快速路径未启用")))
            _bump_try(st, name, "wake")
        time.sleep(3)
    return sent


# ══════════════════ 聊天导航红点（用户 2026-10-06 口径）══════════════════
# 用户原话：
#   「匹配之前记得把聊天导航的红点消除完了之后再匹配」
#   「匹配 3 次之后需要检测一下聊天导航那里有没有红点」
# 为什么用底导航角标：它是**全局可见**的"还有未读"信号（任何主页面都看得到），
#   比 `im.pending()`（数据库口径：只看"最后一条真人消息是不是她发的"）更直接 ——
#   系统卡片能让角标常亮而 pending() 完全看不见。
# 检测在 `soul_clear_unread.nav_dot()`，自带「底导航必须在屏幕上」的守卫
#   （否则会话页同一位置的红色「礼物」图标会假阳性，实测 182 红像素）。
def _chat_nav_dot():
    """返回 (状态, 红像素数)。状态 ∈ {True=有, False=无, **None=判不了**}。

    None 的场合 = **不在主框架**（会话页/搜索页/官方号消息页）→ 底导航不在屏幕上，
    既不能说"有"也不能说"没有"。异常一律按 None（判不了）处理，绝不误报、绝不影响主链路。
    """
    try:
        import soul_clear_unread as CU
        return CU.nav_dot(), CU.nav_badge()
    except Exception as e:
        log("     !! 聊天导航红点检测异常（当'判不了'处理）: %r" % (e,))
        return None, -1


def _dot_desc(state):
    return {True: "有", False: "无", None: "判不了（不在主框架）",
            "unresolved": "进了会话却没回成（未处理）"}.get(state, "判不了")


def _last_her_text(sid):
    """从正式库(im_data.db)取该会话「她」最新一条可读文本（语音取 Soul 自带转写）。

    仅用于红点「进入式判定」的兜底（`im.pending()` 里按昵称匹配不到时）。
    取不到返回 None（调用方放弃该行，**绝不瞎编**）。
    """
    if not sid:
        return None
    try:
        c = sqlite3.connect(im.IMDB)
        rows = c.execute("SELECT senderId, text, msgContent, msgType FROM chatmsg "
                         "WHERE sessionId=? ORDER BY localTime DESC LIMIT 8",
                         (str(sid),)).fetchall()
        c.close()
    except Exception as e:
        log("  !! _last_her_text 查库失败: %r" % (e,))
        return None
    for sender, text, content, mt in rows:
        eff = str(text).strip() if (text and str(text).strip()) else ""
        if not eff and int(mt or 0) == im.VOICE_MT:
            eff = im._voice_text(content)          # 语音 → 自带转写
        if not eff or im._is_sys(eff, content):    # 系统卡片/图片/转写失败 → 不算她说话
            continue
        if str(sender) != str(im.ME):
            return eff
    return None


# ══════════════════ 红点「进入式判定」前置守卫（M1，2026-10-06）══════════════════
# 🔴 铁律：**宁可不动，绝不标已读不回**。tap 进会话 = 把她的消息标成已读，
#   所以**tap 之前**必须先从 `pending()` 快照确认「这一行是可回复的真人待回」；
#   解析不出来（系统卡片/官方号/拿不准）→ 直接跳过、**绝不 tap**，留在未读
#   交下一轮正常 pending()→回复流程处理。
_re_dot_time = re.compile(r"^\d{1,2}[:：]\d{2}$")     # 列表里的时间戳 14:03
_re_dot_num = re.compile(r"^[\d\W_]+$")                # 纯数字/纯符号（未读角标等）


def _dot_row_name(items, y):
    """聊天列表 OCR → 红点行 y 附近的**昵称候选**（左侧列，排除时间/角标）。

    只认与红点 y 同行（±60）且 x<560（右侧是时间/红点/未读，不是昵称）的文本项。"""
    out = []
    for t, x, yy in items:
        s = str(t or "").strip()
        if not s or len(s) > 20:
            continue
        if x > 560:
            continue
        if abs(yy - y) > 60:
            continue
        if _re_dot_time.match(s) or _re_dot_num.match(s):
            continue
        out.append((abs(yy - y), s))
    out.sort(key=lambda z: z[0])
    return [s for _, s in out]


def _resolve_row(items, y, pend, st):
    """前置守卫：把「红点行 y」解析成可回复的待回条目 `(name, her_text, sid)`。

    只在**唯一命中**时才认（多个候选/命中多人都判失败）——宁可不回，绝不发错人。"""
    cands = _dot_row_name(items, y)
    if not cands:
        return None
    hit = []
    for p in (pend or []):
        try:
            pn, pt = str(p[1] or "").strip(), str(p[3] or "").strip()
        except Exception:
            continue
        if not pn or not pt:
            continue
        if not any(pn == c or pn in c or c in pn for c in cands):
            continue
        if not _reachable(pn) or _is_fake_last(pt):
            continue
        try:
            if not _sendable(pn) or _cooling(st, pn, pt):
                continue
        except Exception:
            pass
        hit.append(p)
    if len(hit) != 1:
        return None
    p = hit[0]
    return (p[1], p[3], p[5])


def _dot_sweep(st, max_rows=3, max_pages=3):
    """红点「进入式判定」清扫（2026-10-06 用户口径 + M1 前置守卫）。

      原话：「点进去如果是正常的聊天框，那就证明可以对话，正常对话就行；
             如果不是那么返回退出。」

    🔴 三个历史坑（保留本段，防止有人改回去）：
      ① **绝不能"只进入就返回"**：tap 进会话 = 把她的消息标成**已读**。旧
         `soul_clear_unread.main()` 把每行红点 tap 进去再 BACK —— 那是**标已读却不回**。
         ⇒ 本函数 **tap 之前先用 `_resolve_row()` 做前置守卫**：只有这一行能从 `pending()`
           快照解析出**可回复的真人待回**时才 tap，且 tap 后**必真回复**（复用 `do_reply`）。
           解析不出 → 跳过、绝不 tap ⇒「已读不回」在源头不可达。
      ② **不是正常聊天框必须能退出**：官方号页/WebView 里 BACK 不回列表 → 卡住出不来。
         ⇒ BACK 最多 4 次，仍回不去就 `_goto_chat_list(force=True)` 清栈兜底。
      ③ **后置兜底**：万一 tap 后身份/回复仍失败（前置守卫漏网），**绝不静默当已清** ——
         记 warning（含会话页 OCR 原文便于取证）+ 返回 `"unresolved"`，交调用方判"未清"。

    限额 max_rows=3 行 / max_pages=3 屏（防系统卡片连点把一轮拖死）。
    返回：`False`=红点已清 ／ `True`/`None`=仍在/判不了 ／ `"unresolved"`=进了会话却没回成
          （**调用方不得当成已清**）。
    """
    try:
        import soul_clear_unread as CU
        import soul_read as rd
        import soul_send as S
    except Exception as e:
        log("  !! _dot_sweep 依赖加载失败: %r" % (e,))
        return _chat_nav_dot()[0]

    def _backs():
        """BACK(62,131) 最多 4 次直到回到聊天列表；仍失败 → 清栈兜底。"""
        for _i in range(4):
            try:
                if sr._on_chat_list():
                    return True
            except Exception:
                pass
            try:
                soul.tap(62, 131)
            except Exception as e:
                log("  !! _dot_sweep BACK 失败: %r" % (e,))
            time.sleep(1.1)
        try:
            if not sr._on_chat_list():
                sr._goto_chat_list(force=True)     # 官方号页/WebView 兜底（慢但彻底）
            return bool(sr._on_chat_list())
        except Exception as e:
            log("  !! _dot_sweep 兜底回聊天列表失败: %r" % (e,))
            return False

    # a. 确保在聊天列表页
    try:
        if not sr._on_chat_list():
            sr._goto_chat_list()
    except Exception as e:
        log("  !! _dot_sweep 导航到聊天列表失败: %r" % (e,))
    # b. 回顶（红点行才在当前屏被检到）
    try:
        CU.scroll_to_top()
    except Exception as e:
        log("  !! _dot_sweep 回顶失败: %r" % (e,))
    # 待回快照（前置守卫按它解析身份；进 _dot_sweep 时红点=有新消息，必然该查一次）
    try:
        _pend_cache = im.pending()
    except Exception as e:
        log("  !! _dot_sweep pending() 失败: %r" % (e,))
        _pend_cache = []

    _unresolved = False        # ⭐ tap 了却没回成 → 红点没真正处理（调用方须按"未清"处理）
    rows = 0
    page = 0
    while page < max_pages and rows < max_rows:
        try:
            if CU.nav_dot() is False:               # g. 红点已清 → 立即停
                log("  ✅ 聊天导航红点已清 → 停止清扫")
                break
        except Exception as e:
            log("  !! _dot_sweep nav_dot() 失败: %r" % (e,))
        try:
            soul.ensure_foreground()
            soul.screenshot()
        except Exception:
            pass
        try:
            bs = CU.badges()
        except Exception as e:
            log("  !! _dot_sweep badges() 失败: %r" % (e,))
            bs = []
        if not bs:
            try:
                soul.swipe_up()
                time.sleep(0.9)
            except Exception:
                pass
            page += 1
            continue
        acted = False
        for z in bs:
            if rows >= max_rows:
                break
            try:
                if CU.nav_dot() is False:           # g. 每处理一行前复查
                    break
            except Exception:
                pass
            y = int(z["y"])
            # ── 🔴 前置守卫（tap **之前**）：解析不出「可回复的待回身份」就跳过、绝不 tap ──
            #    ⚠️ 跳过不改列表布局 → 同屏坐标仍有效；一旦真 tap 立即 break 重截图
            #       （坐标绝不跨"进入/返回"复用，防点到别人）。
            try:
                _items = rd.items()
            except Exception as e:
                log("  !! _dot_sweep 读屏失败: %r" % (e,))
                _items = []
            _pre = _resolve_row(_items, y, _pend_cache, st)
            if not _pre:
                log("  ⏭ 红点行 y=%d 解析不出可回复的待回身份 → 跳过（不 tap、留在未读，"
                    "交下一轮 pending 回复流程）" % y)
                continue
            _pname, _ptext, _psid = _pre
            rows += 1
            acted = True
            try:
                soul.ensure_foreground()
                soul.tap(180, y)                    # 点头像区进会话（避开名字后的❤️）
            except Exception as e:
                log("  !! _dot_sweep tap 失败（y=%d）: %r" % (y, e))
                _unresolved = True
                break
            time.sleep(1.9)
            # ── d. 判定「是否正常聊天框」 ──────────────────────────────
            # 正常聊天框 = 已不在主框架 + 底部输入框一带出现「发送/发消息/按住说话/录音」
            # 即 `_mode()` ∈ text/voice/rec（`_bottom_texts()` 已按 y>1050*DEV_H/1600 过滤）。
            try:
                _m = S._mode(S._bottom_texts())
            except Exception as e:
                log("  !! 读输入框模式失败: %r" % (e,))
                _m = "unknown"
            try:
                _main = bool(soul.on_main())
            except Exception:
                _main = True
            if (not _main) and _m in ("text", "voice", "rec"):
                # e. 正常聊天框 → 用**前置守卫已确认**的身份真回复（绝不只进入就返回）
                name, sid6 = _pname, _psid
                # 她末句**以数据库为准**（pending 的 p[3]，或用会话 id 从正式库刷新）——
                # 刻意**不用**全屏 OCR 猜"她最后一条气泡"：OCR 无发送者归属，猜错就会把
                # 内容发给错的人；宁可不取（拿不到就不回复、记为未处理），绝不发错内容。
                try:
                    her_text = _last_her_text(sid6) or _ptext
                except Exception:
                    her_text = _ptext
                try:
                    _items2 = rd.items()
                except Exception:
                    _items2 = _items
                _tops = [t for t, _x, _y in _items2 if _y < 200]
                if name and her_text:
                    log("  💬 红点进「%s」= 正常聊天框 → 正常对话回复：%r（标题区=%r）"
                        % (name, str(her_text)[:24], str(_tops[:2])))
                    try:
                        with _Tick("红点回复·%s" % name, st):
                            do_reply(name, her_text, st, sid_hint=sid6)
                    except Exception as e:
                        log("  !! _dot_sweep do_reply 异常（%s）: %r" % (name, e))
                        _unresolved = True
                    time.sleep(0.5)
                    break                               # do_reply 末尾已回聊天列表
                # 后置兜底：身份竟为空（前置守卫漏网）——**绝不当已清**，记取证信息
                log("  ⚠ 红点进了正常聊天框却拿不到身份（前置守卫漏网）标题区=%r 全屏OCR前8=%r"
                    " → 记为未处理（返回 unresolved）"
                    % (str(_tops[:2]), str([t for t, _x, _y in _items2][:8])))
                _unresolved = True
            else:
                # ⭐ 2026-10-07 M1 补：前置守卫已确认该行是**真人可回待回**，
                #   tap 进会话本身就可能把她的消息标已读 → 任何「没回复」的结局
                #   都必须记 unresolved（否则红点若因进入被消，会被当"已清"，
                #   形成用户明令禁止的「标已读却不回」静默路径）。
                log("  ↩ 红点进 y=%d 不是正常聊天框（mode=%s, on_main=%s）→ 记未处理"
                    % (y, _m, _main))
                _unresolved = True
            try:
                _backs()                                # f. 不是正常聊天框 → 返回退出
            except Exception as e:
                log("  !! _dot_sweep _backs 异常: %r" % (e,))
                _unresolved = True
            break
        if acted:
            continue                                    # 已 tap → 重新截图取最新红点
        # 本屏没有"可回复"的红点（全是系统卡片/官方号/拿不准）→ 下翻一屏
        try:
            soul.swipe_up()
            time.sleep(0.9)
        except Exception:
            pass
        page += 1

    if _unresolved:
        return "unresolved"
    try:
        return CU.nav_dot()
    except Exception:
        return _chat_nav_dot()[0]


def dot_block_match(st):
    """匹配前的红点闸。返回 True = 本轮**不匹配**。

    ⭐ 2026-10-06 用户口径（**进入式判定**，取代"连判 3 轮放行"）：
      「红点可以这样处理：点进去如果是正常的聊天框，那就证明可以对话，正常对话就行；
        如果不是那么返回退出。」

    🔴 为什么改：旧版检测到红点只做「本轮不匹配 + streak+1」，连拦 DOT_BLOCK_MAX 轮后
       才判"系统卡片回不了"放行匹配 —— 结果是**每轮匹配前空转 3 轮**，真人新消息被反复
       延后（实测 23:29 来的消息到 23:32 才回）。现在当轮直接 `_dot_sweep()` 进去判定：
         · 是正常聊天框 → 当场真回复（回复本身即消红点）；
         · 不是（官方号/系统卡片页/没进去）→ 返回退出。
       ⭐ M1：`_dot_sweep` 只 tap「前置守卫确认可回复」的行；若出现「进了却没回成」
         （返回 `"unresolved"`）→ **不得当成已清**，按"未见效"计 streak，交放行兜底。
       两个历史坑见 `_dot_sweep` 注释（"只进入就返回"= 已读不回；官方号页 BACK 出不来）。

    保留 `_blk >= DOT_BLOCK_MAX` 的放行兜底：防止 `_dot_sweep` 本身卡住 → 匹配被永久饿死。
    """
    state, npx = _chat_nav_dot()
    if state is not True:
        if state is False:
            st["dot_block_streak"] = 0          # 确认无红点 → 清零
        return False                             # 无红点 / 判不了 → 不拦
    _blk = int(st.get("dot_block_streak", 0))
    if _blk >= DOT_BLOCK_MAX:
        st["dot_block_streak"] = 0
        log("  🔴 聊天导航红点连 %d 轮没被消除 → 放行匹配（防 _dot_sweep 卡死饿死匹配）"
            % _blk)
        return False
    # ⭐ 本轮：进入式清扫（进去判定 / 正常对话），不再是空转等 3 轮
    log("  🔴 匹配前：聊天导航红点（红像素 %s）→ 进入式清扫（进去判定，是正常聊天框就正常对话）"
        % npx)
    try:
        after = _dot_sweep(st)
    except Exception as e:
        # 清扫本身异常**绝不能吃掉整轮**（否则主循环 fail+1，几次就熔断暂停）：
        # 按"没清掉"处理，走下面的 streak 计数与原放行兜底。
        log("  !! _dot_sweep 异常（按'红点未清'处理）: %r" % (e,))
        after = True
    if after is False:
        st["dot_block_streak"] = 0
        log("  ✅ 红点已清 → 不拦，正常去匹配")
        return False
    if after == "unresolved":
        # ⭐ M1：`_dot_sweep` 进了会话却没回成 → 红点可能已被误标已读，**绝不当成已清**。
        st["dot_block_streak"] = _blk + 1
        log("  🔴 清扫后仍有「进了会话却没回成」的未处理行 → 本轮不匹配"
            "（第 %d/%d 次；到上限仍消不掉按系统卡片放行）" % (_blk + 1, DOT_BLOCK_MAX))
        return True
    st["dot_block_streak"] = _blk + 1
    log("  🔴 清扫后红点仍在（%s）→ 本轮不匹配（第 %d/%d 次；到上限仍消不掉按系统卡片放行）"
        % (_dot_desc(after), _blk + 1, DOT_BLOCK_MAX))
    return True


def do_match(st):
    """匹配新人（复用 soul_match，抢占式：中途来消息立刻中断）"""
    try:
        import soul_match as M
    except Exception as e:
        log("  !! 无法加载 soul_match: %r" % (e,))
        return 0
    # ⭐ 2026-10-03 修「匹配永远空转」：soul_match.interrupted 的让位判据只看昵称可达，
    #   被陌生人预检跳过的人（如 岁岁安然🌸）也算"待回" → 每次匹配 0.5s 就让位。
    #   统一成守护口径：只有「我们真的会回的人」才打断匹配。
    _bell_hit = {"v": False}

    def _interrupted_daemon():
        # 🔔 第 1 优先级：奇遇铃 > 待回消息。检测到就中断匹配；**不在这里做 UI**，
        #   交给 match_batch 返回后统一处理（避免打断匹配自身的 UI 操作序列）。
        if _bell_live():
            _bell_hit["v"] = True
            log("  🔔 匹配中发现奇遇铃 → 中断匹配（第 1 优先级）")
            return (True, [])
        try:
            rows = im.pending() or []
        except Exception:
            rows = []
        # ⭐ 2026-10-03 修「匹配永远空转」第二处：冷却中的人（已试满 RETRY_MAX 次、
        #   40 分钟内不再回）不该打断匹配。体检实证：连续两轮都是
        #   「匹配完成 [] | 结束原因: pending」——2 秒就被打断，星球匹配从没真跑过。
        reach = [r for r in rows
                 if _reachable(r[1]) and not _is_fake_last(r[3]) and _sendable(r[1])
                 and not _cooling(st, r[1], r[3])
                 and not _zombie(r) and not _abandoned(st, r[1], r[3])]   # ⭐ F2
        if reach:
            log("  ⚡ 匹配中发现 %d 个真待回 → 中断匹配优先回消息" % len(reach))
        return (bool(reach), reach)
    M.interrupted = _interrupted_daemon
    # ⭐ 2026-10-06（用户口径：「匹配的时候 OCR 分析一下剩余次数」）
    #   点「开始匹配」之前**先读一次星球页额度** —— 用完就别白点 3 次按钮。
    #   旧行为：连点 3 次都 no_match 才"推断"用完 → 白空转一整轮（5~8 分钟）。
    #   现在的判据是**读出来**的（soul.soul_quota_left），不是猜的：
    #     ① 灵魂剩余读到 0             → 用完
    #     ② 灵魂那行读不到 + 用完弹层在 → 用完（实测：用完时「今日剩余N次」这行会消失）
    #     ③ 读不到 且 无弹层           → **未知**，行为完全不变（回退 no_match 推断）
    try:
        _ok = M.to_planet()
        _sq, _vq, _sheet = M.planet_quota() if _ok else (None, None, False)
        log("  🎫 匹配额度：灵魂 %s / 语音 %s%s"
            % ("未知" if _sq is None else "%d 次" % _sq,
               "未知" if _vq is None else "%d 次" % _vq,
               " ｜ 用完弹层在屏上" if _sheet else ""))
        if _sq == 0 or (_sq is None and _sheet):
            log("  ⛔ 灵魂匹配额度已用完（%s）→ 本轮跳过匹配，立刻转唤醒"
                % ("OCR 读到 0 次" if _sq == 0 else "读不到次数 + 用完弹层在屏"))
            if _sheet:
                # 顺手把弹层清掉：它盖住底导航，不清会让后面的导航判定继续失败
                try:
                    soul.close_quota_popup()
                except Exception as e:
                    log("     !! 清额度弹层异常: %r" % (e,))
            return (0, ["quota_out"])
    except Exception as e:
        log("  !! 匹配额度读取异常（按未知处理，行为不变）: %r" % (e,))
    try:
        names, why, reasons = M.match_batch(MATCH_N, dry=False)
        log("  匹配完成 %s | 结束原因: %s | 细分: %s" % (names, why, reasons))
        if _bell_hit["v"]:
            _bell_hit["v"] = False
            _do_love_bell(st, where="匹配中断")
            return (0, reasons)
        # ⭐ 2026-10-04 用户口径（改）：没额度**不再停当天匹配**——弹层里点「去聊天」
        #   走免费出口（点它落到聊天列表，聊满一颗心 +5 次）。所以这里只清弹层，
        #   不再记「今天不匹配」的标记。
        if not names and _screen_has_quota_sheet():
            try:
                soul.close_quota_popup()
            except Exception as e:
                log("     !! 清额度弹层异常: %r" % (e,))
        if names:
            _spend(st, len(names))
        return (len(names), reasons)
    except Exception as e:
        log("  !! 匹配异常: %r" % (e,))
        return (0, ["exception"])


# ══════════════════════ 账号轮转（切号决策）══════════════════════
# 用户口径（2026-10-05 拍板）：
#   · 切号前提 = **三条同时成立**，且持续 `dry_hold_min`(30) 分钟：
#       ① 无人可聊（真待回 = 0）
#       ② **没人可唤醒**（唤醒池 = 0 —— 唤醒不耗匹配次数，池里还有人就没必要切）
#       ③ 没有匹配次数（灵魂 & 语音「今日剩余」都为 0）
#   · 连续在线 ≥ `online_max_h`(8h) → **强制切号**（不等三条件）
#   · 切走后 `cool_h`(4h) 内不得切回该号；两次切号间隔 ≥ `min_gap_min`(30min)
#   · 只有 2 个号 → 目标即"另一个"；无号可切则原地不动（宁可空转，绝不乱切）
# 参数在 `soul_accounts.json` 的 rules 里改，不用动代码。
FORCE_SWITCH_GRACE_MIN = 0     # 8h 到点后允许把手头动作做完的宽限（0 = 立即切）
QUOTA_CHECK_EVERY = 10 * 60    # 「匹配次数」要开 UI 读，最密 10 分钟一次
# ⭐ 2026-10-06（P1#11）：切号 UI 连续失败 → 指数退避（防每轮都去点同一个点不到的按钮、狂点模拟器）。
SWITCH_FAIL_N = 2              # 连续失败达到 N 次开始退避
SWITCH_FAIL_COOL = 600.0       # 退避基数（秒）：第 N 次失败后 600s，之后每多失败 1 次翻倍
SWITCH_FAIL_CAP = 7200.0       # 退避上限（秒，2h）


def _wake_pool_size(st):
    """**够得着**的唤醒候选数（排除 永久放弃 / 今天已唤 / 冷却中）。=0 → 没人可唤醒。"""
    try:
        fol = _follow_mem(min_msgs=10, cool_h=WAKE_MIN_H, max_idle_h=WAKE_MAX_H,
                          skip=set((st.get("wake_off") or {}).keys()))
    except Exception as e:
        log("  !! 唤醒池统计异常: %r" % (e,))
        return -1          # 未知 → 调用方不得据此切号
    # ⭐ 2026-10-06 修（P1#10）：`_follow_mem` 内部异常会返回 None（原为 []，被当 0）→
    #   这里必须转成 -1（未知），否则"读库失败"会被误判成"唤醒池空了"→ 触发切号。
    if fol is None:
        log("  !! 唤醒池未知（_follow_mem 内部异常）→ 本轮不得据此切号")
        return -1
    n = 0
    for idle_h, name, hers, total, last_text, lt in (fol or []):
        try:
            if _wake_day(st, name) == _today():
                continue
            if _cooling(st, name, "wake"):
                continue
        except Exception:
            pass
        n += 1
    return n


def _online_hours(book):
    t0 = book.get("online_since")
    if not t0:
        return 0.0
    try:
        return (time.time() - float(t0)) / 3600.0
    except Exception:
        return 0.0


def _dry_eval(st, book, cur):
    """三条件评估 → (dry, detail)。第③条要开 UI 读星球页，用 QUOTA_CHECK_EVERY 限频。"""
    # ① 无人可聊
    try:
        if _has_real_pending(im.pending() or []):
            return (False, "有人可聊")
    except Exception:
        return (False, "pending 探测失败（未知）")
    # ② 没人可唤醒 —— ⭐ 2026-10-06 修（用户：「唤醒好友冷却中 或 无人唤醒 → 切号」）：
    #   旧判据只看「池里有没有人」→ 池里有 8 人但**唤醒还在冷却里**就算"有产能"，
    #   结果匹配没额度、唤醒又唤不了，机器原地干等 30 分钟（实测 06:47 起纯巡检）。
    #   正确判据是「**当下**走不走得通」：池空、或池有人但在冷却中 → 都算这条路堵了。
    wk = _wake_pool_size(st)
    _lw = float(st.get("last_wake", 0) or 0)
    _gap = WAKE_EVERY_IDLE if str(st.get("quota_out_date") or "") == _today() else WAKE_EVERY
    _wk_blocked = ""
    if wk > 0:
        if _lw > 0 and (time.time() - _lw) < _gap:
            _wk_blocked = "唤醒池 %d 人但在冷却中（%d 分钟后才到）" % (
                wk, int((_gap - (time.time() - _lw)) // 60) + 1)
        else:
            return (False, "唤醒池 %d 人（可唤）" % wk)
    elif wk < 0:
        # ⭐ 2026-10-06 修（P1#10）：唤醒池**未知**（读库失败）不等于"池空了"→ 不得据此切号。
        return (False, "唤醒池未知→不切号")
    else:
        _wk_blocked = "唤醒池 0 人"
    # ③ 没有匹配次数（UI，限频）
    rec = book["acct"].setdefault(cur, {})
    now = time.time()
    cached = rec.get("quota")
    if cached is not None and (now - float(rec.get("quota_at") or 0)) < QUOTA_CHECK_EVERY:
        sq, vq = cached[0], cached[1]
    else:
        try:
            sq, vq = soul.planet_match_quota()
        except Exception as e:
            log("  !! 匹配次数读取异常: %r" % (e,))
            sq, vq = (None, None)
        rec["quota"] = [sq, vq]
        rec["quota_at"] = now
    if sq is None and vq is None:
        return (False, "匹配次数未知（读不到星球页额度）")
    # ⭐ 2026-10-06 修：匹配通道只走**灵魂匹配** → 判"还有没有匹配次数"只看灵魂那一项。
    #   旧写法取 max(灵魂,语音) → 语音还剩 3 次就判"还有额度" → 干旱不成立 → 不切号，
    #   可灵魂早就 0 次了（实测 07:03：灵魂读完为 0、语音 3 次，机器就这么原地卡住）。
    left = sq if sq is not None else 0
    if left > 0:
        return (False, "灵魂匹配还有 %s 次（语音 %s）" % (sq, vq))
    return (True, "待回0 + %s + 灵魂匹配 0 次（语音 %s）" % (_wk_blocked, vq))


def _switch_target(cur, book):
    """下一站账号：冷却期外的其他号（2 个号时即"另一个"）。无 → None。"""
    R = _acct.rules()
    cool_s = float(R.get("cool_h", 4)) * 3600
    now = time.time()
    for a in _acct.accounts():
        uid = a["uid"]
        if uid == cur:
            continue
        la = (book.get("acct", {}).get(uid) or {}).get("leave_at")
        if la and (now - float(la)) < cool_s:
            log("  ⏸ 账号「%s」冷却中（距切走 %.1fh < %sh）"
                % (a["nickname"] or uid, (now - float(la)) / 3600.0, R.get("cool_h", 4)))
            continue
        return uid
    return None


def _do_switch_account(st, tgt_uid, book, reason, force=False):
    """App 内切号（自己 → 左上角小人 → 点目标账号行）+ 校验 + 状态迁移。

    ⭐ 2026-10-06 用户定稿：「**8小时优先切号 高于一切**，然后正常切号 排在最后」
      → force=True（在线超 `online_max_h`）时**跳过下面①的「切号前有人可聊」复查**。
      实测卡了 6.5 小时（14.4h 远超 8h）却一直「有人可聊 → 放弃切号」。
    """
    nick = _acct.nickname_of(tgt_uid)
    old = book.get("current_uid") or _acct.cur_uid()
    # ① 切号前复查：这一刻真的没人可聊（防"刚决定切号她就来消息"）
    # ⭐ 2026-10-06 修「切号死循环」（实测 14:00~14:03 每 16 秒转一圈、切了 8 次都没切走）：
    #   这里用 `_has_real_pending`（**不看冷却**）→ 两个「重试冷却中（40 分钟内不为它忙）」
    #   的人（桃桃入梦、梦一场）永远算"有人可聊" → 切号每次被复查否决；
    #   而回复流程因为冷却又**不会真去回她们** → 切不走也回不了，死循环到天亮。
    #   判据必须和"我们真的会回的人"一致 → 改用 `_real_pending(st)`（含冷却/僵尸/放弃过滤）。
    # （`_real_pending` 已含 reachable / sendable / 僵尸 / 冷却 / 已放弃 全套过滤，别再抄一遍）
    if force:
        log("  ⛔ 强制切号（在线已超 %sh）→ **跳过「切号前有人可聊」复查**，直接切"
            % float(_acct.rules().get("online_max_h") or 8))
    else:
        try:
            reach = _real_pending(st)
        except Exception:
            reach = []
        if reach:
            log("  ⏸ 切号前复查：突然有人可聊（%s）→ 放弃本次切号"
                % "、".join(str(r[1]) for r in reach[:3]))
            return False
    # ② UI
    ok = False
    try:
        if soul.acct_switch_open():
            ok = soul.acct_switch_pick(nick)
        else:
            log("  !! 没进到「切换账号」页（OCR 没看到标题）")
    except Exception as e:
        log("  !! 切号 UI 异常: %r" % (e,))
    if not ok:
        log("  !! 切号 UI 未完成（没点到「%s」那一行）" % nick)
        # ⭐ 2026-10-06 现场取证：soul.py 里全是 print，守护是 pythonw（无 stdout）→ 全丢。
        #   切号失败时把「当前 Activity + 页面文字」抓进守护日志，下次失败不用再盲猜。
        try:
            import soul_read as _rd
            _txt = " | ".join(str(t) for t, _, _ in (_rd.items() or [])[:16])
            log("     现场：activity=%s" % (soul.activity() or "?"))
            log("     现场页面文字：%s" % _txt[:260])
        except Exception as e:
            log("     (现场取证失败: %r)" % (e,))
        # ⭐ 2026-10-06 修（P1#11 强制切号失败无退避）：UI 失败 → **记一次连续失败计数**
        #   （写 switch_state.json），供 `_maybe_switch_account` 做指数退避；否则每轮都去点
        #   同一个点不到的按钮（狂点模拟器、且毫无进展）。
        try:
            _cur = old or _acct.cur_uid()
            _rec = book.setdefault("acct", {}).setdefault(_cur, {})
            _rec["switch_fail"] = int(_rec.get("switch_fail") or 0) + 1
            _rec["switch_fail_at"] = time.time()
            _acct.save_switch(book)
            log("     切号失败计数 →「%s」第 %d 次（将进入退避）"
                % (_acct.nickname_of(_cur), _rec["switch_fail"]))
        except Exception as _e2:
            log("     (切号失败计数写入异常: %r)" % (_e2,))
        return False
    # ③ 校验：App prefs 必须变成目标号（最多等 20s）
    got = got_sess = None
    for _i in range(10):
        time.sleep(2.0)
        try:
            got, got_sess = im.prefs_identity(force=True)
        except Exception:
            got, got_sess = (None, None)
        if str(got) == str(tgt_uid):
            break
    if str(got) != str(tgt_uid):
        log("  ⚠️ 切号校验未通过：prefs 仍为 uid=%s（期望 %s）→ 记失败，下轮重试"
            % (got, tgt_uid))
        return False
    # ④ 状态迁移：先落**旧号** state，再切路径、载**新号** state
    try:
        save_state(st)
    except Exception:
        pass
    _acct.set_uid(tgt_uid)
    try:
        im.ME = str(tgt_uid)
        if got_sess:
            im.SESS = str(got_sess)
    except Exception:
        pass
    try:
        st.clear()
        st.update(load_state())
    except Exception:
        pass
    # ⭐ 2026-10-06 用户：「切号后匹配次数要按新号重新算，旧号用完 ≠ 新号用完」。
    #   旧号留下的「匹配额度用完」标记必须清空，下一轮 do_match 会用 planet_quota()
    #   重新 OCR **当前账号**的真实剩余次数 → 新号有额度就继续匹配，没额度再重新标记。
    for _k in ("quota_out_date", "quota_out_at", "silent_note_at"):
        st.pop(_k, None)
    try:
        save_state(st)
    except Exception:
        pass
    # ⭐ 2026-10-06 修（主 bug）：同时清掉**旧号** state 文件里的三个额度/静默标记。
    #   daemon 重启时 App 不在前台 → cur_uid() 探测失败回退主号 → 若旧号 state 还残留
    #   quota_out_*，加载后会被随后的 save_state 传播进**新号** state 文件。
    #   这里直接按 uid 显式读写旧号文件（不走 load_state/save_state，避免路由歧义）。
    try:
        _old_state_f = _acct.for_uid(OUTD, "state.%s.json" % VM, old)
        if os.path.isfile(_old_state_f):
            with io.open(_old_state_f, encoding="utf-8") as _f:
                _old_st = json.load(_f) or {}
            _dirty = False
            for _k in ("quota_out_date", "quota_out_at", "silent_note_at"):
                if _k in _old_st:
                    _old_st.pop(_k, None)
                    _dirty = True
            if _dirty:
                with io.open(_old_state_f, "w", encoding="utf-8") as _f:
                    json.dump(_old_st, _f, ensure_ascii=False, indent=1)
                log("     旧号「%s」state 残留额度标记已清" % _acct.nickname_of(old))
    except Exception as _e:
        log("     (旧号 state 清理异常: %r)" % (_e,))
    now = time.time()
    for uid in (old, tgt_uid):
        rec = book["acct"].setdefault(uid, {})
        rec["dry_since"] = None
        rec["quota"] = None
        # ⭐ 2026-10-06（P1#11）：切号**成功 → 清零失败退避计数**（本号/新号都清）。
        rec["switch_fail"] = 0
        rec["switch_fail_at"] = None
    book["acct"][old]["leave_at"] = now
    book["acct"][tgt_uid]["switches"] = int(book["acct"][tgt_uid].get("switches") or 0) + 1
    book["current_uid"] = tgt_uid
    book["online_since"] = now
    book["last_switch_at"] = now
    _acct.save_switch(book)
    log("  ✅ 已切号：「%s」→「%s」｜原因：%s"
        % (_acct.nickname_of(old), nick, reason))
    log("     旧号「%s」进入 %sh 冷却；新号在线计时从 0 重新起算"
        % (_acct.nickname_of(old), _acct.rules().get("cool_h", 4)))
    # ⑤ 切完先"装真人"：回星球页停一会儿，别立刻发消息
    try:
        import soul_match as _M2
        _M2.to_planet()
    except Exception:
        pass
    time.sleep(8)
    return True


def _maybe_switch_account(st):
    """每轮评估一次。返回 True = 本轮已切号（调用方应立即结束本轮）。"""
    try:
        R = _acct.rules()
        book = _acct.switch_book()
        cur = _acct.cur_uid()
        now = time.time()
        # ⭐ 2026-10-06 修（P1#11）：切号 UI 连续失败 → **指数退避**（退避期内直接跳过并写日志）。
        #   否则每轮都去点同一个点不到的按钮（狂点模拟器、毫无进展）。成功会在
        #   `_do_switch_account` 里清零 → 退避自动解除。
        _frec = book["acct"].setdefault(cur, {})
        _nf = int(_frec.get("switch_fail") or 0)
        if _nf >= SWITCH_FAIL_N:
            _cool = min(SWITCH_FAIL_COOL * (2 ** (_nf - SWITCH_FAIL_N)), SWITCH_FAIL_CAP)
            _lastf = float(_frec.get("switch_fail_at") or 0)
            if _lastf and (now - _lastf) < _cool:
                log("  ⏸ 切号连续失败 %d 次 → 退避至多 %.0f 分钟内不再尝试（剩 %.0f 分钟）"
                    % (_nf, _cool / 60.0, (_cool - (now - _lastf)) / 60.0))
                return False
        # 首次/换号后记录在线起点
        if book.get("current_uid") != cur or not book.get("online_since"):
            book["current_uid"] = cur
            book["online_since"] = now
            _acct.save_switch(book)
            log("  🕒 账号「%s」在线计时起点已记录" % _acct.nickname_of(cur))
            return False
        online_h = _online_hours(book)
        max_h = float(R.get("online_max_h", 8))
        # ⭐ 2026-10-06 用户定稿优先级：「**8小时优先切号 高于一切**，然后正常切号 排在最后」
        forced = online_h >= (max_h + FORCE_SWITCH_GRACE_MIN / 60.0)
        if forced:
            reason = "连续在线 %.1fh ≥ %sh → 强制切号（**优先于一切**）" % (online_h, max_h)
        else:
            dry, why = _dry_eval(st, book, cur)
            rec = book["acct"].setdefault(cur, {})
            if not dry:
                if rec.get("dry_since"):
                    log("  🌱 恢复产能（%s）→ 干旱计时清零" % why)
                rec["dry_since"] = None
                _acct.save_switch(book)
                return False
            if not rec.get("dry_since"):
                rec["dry_since"] = now
                # ⭐ 2026-10-06（用户：「唤醒冷却中 / 无人唤醒 → 切号」，嫌等太久）：
                #   当天匹配额度已读完为 0（匹配整天没戏）+ 唤醒也堵着 → 这台机器上
                #   已经没有活可干，干旱时阀从 30 分钟**收紧**到 dry_hold_fast_min，
                #   赶紧换号干活，而不是原地空转到天亮。
                # ⚠️ rules() 里这些键**存在但值可能是 None** → float(None) 直接 TypeError，
                #    被外层 except 吞掉后整个切号评估静默失效（实测 07:31 撞到）。
                #    所以统一用 `or` 兜底，不能只给 get 默认值。
                hold_min = float(R.get("dry_hold_min") or 30)
                if str(st.get("quota_out_date") or "") == _today():
                    hold_min = float(R.get("dry_hold_fast_min") or 3)
                    why += "｜额度用完→快切（%d 分钟）" % hold_min
                log("  🌵 三条件成立（%s）→ 开始干旱计时（满 %.0f 分钟才切号）"
                    % (why, hold_min))
                rec["dry_hold_min_used"] = hold_min
            else:
                hold_min = float(rec.get("dry_hold_min_used") or R.get("dry_hold_min") or 30)
            held = (now - float(rec["dry_since"])) / 60.0
            _acct.save_switch(book)
            if held < hold_min:
                log("  ⏳ 干旱持续 %.0f/%.0f 分钟（等满再切）｜本号在线 %.1fh"
                    % (held, hold_min, online_h))
                return False
            reason = "干旱 %.0f 分钟（%s）" % (held, why)
        # 切号间隔下限 —— ⭐ 强制切号**不受**它约束（用户：「8小时优先切号 高于一切」）。
        #   否则在线早就超 8h 的号会被 min_gap 挡住，实测卡了 6 小时没切走。
        #   唯一还兜着的是 `_switch_target` 的账号冷却（另一个号在冷却中就不切，防来回横跳）。
        if not forced:
            last = book.get("last_switch_at")
            gap_min = float(R.get("min_gap_min") or 30)
            if last and (now - float(last)) < gap_min * 60:
                log("  ⏸ 距上次切号仅 %.0f 分钟（下限 %.0f）→ 本次不切"
                    % ((now - float(last)) / 60.0, gap_min))
                return False
        tgt = _switch_target(cur, book)
        if not tgt:
            log("  ⏸ 无可切账号（另一个仍在冷却内）→ 原地不动")
            return False
        if forced:
            log("  🔄 触发切号 →「%s」(%s)｜%s" % (_acct.nickname_of(tgt), tgt, reason))
            log("     ⛔ 强制切号：跳过「距上次切号下限」与「切号前有人可聊」两道拦截")
        else:
            log("  🔄 触发切号 →「%s」(%s)｜%s" % (_acct.nickname_of(tgt), tgt, reason))
        return _do_switch_account(st, tgt, book, reason, force=forced)
    except Exception as e:
        log("  !! 切号评估异常: %r" % (e,))
        return False


# ══════════════════════ 主循环 ══════════════════════
def _cycle(st):
    import soul_global_lock as GL

    busy, info = GL.lock_status()
    if busy:
        log("[锁] 被占用：%s → 等待下一轮" % info.get("why", "?"))
        time.sleep(POLL_IDLE)
        return

    ok, info = GL.start_round("daemon")
    _WD["in_round"] = True          # ⭐ 看门狗开始盯心跳（仅持锁期间）
    if not ok:
        log("[锁] 抢占失败：%s" % info.get("why", "?"))
        time.sleep(60)
        return
    try:
        with _Tick("设备就绪", st):
            dev_ok = _ensure_device()
        if not dev_ok:
            st["fail"] = int(st.get("fail", 0)) + 1
            log("[设备] 不可用 → 失败计数 %d" % st["fail"])
            time.sleep(180)
            return

        # ⭐ 2026-10-04 画面健康闸：截图不可用就**不做任何 UI 操作**（防"失败→猛敲模拟器"正反馈）
        with _Tick("画面健康", st):
            shot_ok = _display_guard()
        if not shot_ok:
            log("  ⏸ 画面不健康 → 本轮跳过 UI 操作（已触发分级恢复）")
            time.sleep(120)
            return

        # ⭐ 2026-10-05 用户优先级第 1 档：奇遇铃最高，抢占一切（> 消息 > 匹配 > 唤醒）。
        #   处理完**继续**本轮后续（已进入该人会话，可能马上有新消息），不 return。
        if _bell_live():
            with _Tick("奇遇铃", st):
                _do_love_bell(st, where="轮首")

        # ⭐ 2026-10-06 修（P1#9 切号后 ME 滞后）：进主流程前补一次**非强制**账号闸
        #   （cached 60s）→ 让 ME/SESS 与 `_acct` 缓存先跟上设备，再去 merge_memory()/
        #   pending()（它们经 `_memdb()` 选库）。发送前 `_deliver` 仍是 force=True，无回归。
        with _Tick("账号跟随", st):
            try:
                _account_gate_ok(force=False)
            except Exception as e:
                log("  !! 账号跟随闸异常: %r" % (e,))

        with _Tick("累积库合并", st):
            merge_memory()
        GL.touch("daemon:pull")
        with _Tick("pending()", st):
            try:
                pend = im.pending()
            except Exception as e:
                log("!! pending() 失败: %r" % (e,))
                pend = []

        real = [p for p in pend
                if _reachable(p[1]) and not _is_fake_last(p[3])]
        skipped_stranger = [p[1] for p in real if not _sendable(p[1])]
        if skipped_stranger:
            log("  ⏭ 陌生人预检跳过（库内<3句，省 ~60s/人 UI 白跑）: %s"
                % "、".join(skipped_stranger))
        real = [p for p in real if _sendable(p[1])]

        # ⭐ 重试冷却：已被折腾过 RETRY_MAX 次仍不成的「她这一句」，暂时让位给匹配/唤醒，
        #   否则永远有 pending → 三态里的「匹配」「唤醒」根本轮不到（2026-10-03 实测）
        cooled = [p[1] for p in real if _cooling(st, p[1], p[3])]
        if cooled:
            log("  ⏸ 重试冷却中（已试 %d 次，%d 分钟内不再为它忙）: %s"
                % (RETRY_MAX, RETRY_COOL // 60, "、".join(cooled)))
        real = [p for p in real if not _cooling(st, p[1], p[3])]

        # ⭐ 2026-10-04（F2-a）僵尸待回降级：已读 + 她这句挂起 ≥ZOMBIE_H 小时
        #   → 本轮不占"真待回"名额、不重试（原来每 10s 就把空闲等待打断一次）。
        zom = [p for p in real if _zombie(p)]
        if zom:
            log("  🧟 僵尸待回降级（已读且挂起≥%dh，本轮不占位/不重试）: %s"
                % (ZOMBIE_H, "、".join(p[1] for p in zom)))
        real = [p for p in real if not _zombie(p)]

        # ⭐ 2026-10-04（F2-b）结构性失败停手转人工：连续 ≥COOL_CYCLE_MAX 个冷却周期
        #   仍发不出去 → 落档案 status=skipped + 写人工复核清单，不再无限"冷却→重试"。
        dead = [p for p in real if _abandoned(st, p[1], p[3])]
        if dead:
            _retire(st, dead)
            real = [p for p in real if not _abandoned(st, p[1], p[3])]

        # ⭐ 2026-10-06 用户口径：「② 待回消息 …… 这里加一个**已读待回**」
        #   → **未读待回优先**（她刚发、我还没看），再排「已读待回」；同组内新→旧。
        real.sort(key=_pend_key)

        # 🔄 账号轮转评估（三条件 / 8h 强制 / 4h 冷却 / 30min 间隔）
        #   有真待回时只做「8h 强制」这一条廉价判定；无待回才做完整三条件（含读匹配次数）。
        with _Tick("账号轮转", st):
            if _maybe_switch_account(st):
                time.sleep(3)
                return          # 本轮已切号 → 下一轮用新账号重新开始

        if real:
            log("── 有人在聊：%d 个待回 ──" % len(real))
            # ⭐ 2026-10-06 用户口径「**有新消息来了 该优先回复对方**」：
            #   原来这里是 `for ... in real:` —— 对**轮首快照**遍历，中途她再发新消息
            #   根本看不到，要等下一轮（实测 ~8min）才轮到；再叠加几个幽灵各占 ~100s，
            #   新消息实际可能十几分钟没人理。
            #   现在改成「队列 + 每回完一个就复查一次 pending」：谁刚发来就立刻插到队首。
            _q = list(real)
            _tried = set()          # (name, text) 已处理过的 → 同一句不重复回
            _loop = 0
            _jumped = False         # ⭐ A：同一轮最多插队 1 次，防「插队风暴」让最老的永远等不到
            while _q and _loop < 25:
                _loop += 1
                ts, name, unread, text, lt, _sid6, _mt = _q.pop(0)
                # 🔔 第 1 优先级抢占：回消息途中弹铃 → 立即中断去处理铃
                if _bell_preempt(st, "回复中"):
                    break
                if not _hour_budget(st):
                    log("  ⛔ 本小时发送已达上限 %d → 暂停回复" % SEND_CAP_H)
                    break
                if _mt == getattr(im, "VOICE_MT", 5):
                    # ⭐ 2026-10-04 她末条是语音（文字来自 Soul 自带转写）→ 标记，供语音出站通道决策
                    log("  🎙 %s 末条是语音（转写: %r）" % (name, str(text)[:24]))
                with _Tick("回复·%s" % name, st):
                    do_reply(name, text, st, sid_hint=_sid6)
                GL.touch("daemon:reply:%s" % name)
                _tried.add((str(name), str(text)))
                time.sleep(0.5)     # ⭐ D：队列紧凑连发（原 1.5s；do_reply 末尾已返回聊天列表，无需再等）
                # ⚡ 插队复查：这期间她/别人又发来新消息 → 立刻优先回，不等下一轮
                try:
                    _fresh = [p for p in _real_pending(st)
                              if (str(p[1]), str(p[3])) not in _tried]
                except Exception as _e:
                    log("  !! 插队复查异常: %r" % (_e,))
                    _fresh = []
                if _fresh and not _jumped:
                    _fresh.sort(key=_pend_key)
                    log("  ⚡ 期间来了新消息（%s）→ 插队优先回复（本轮仅此一次，防风暴）" % _fresh[0][1])
                    _q = _fresh + _q
                    _jumped = True
            st["fail"] = 0
            st["last_match"] = 0        # ⭐ 2026-10-03 首要目标：回复完立即去匹配新人
            time.sleep(POLL_HOT)          # 趁热再看一眼
        else:
            now = time.time()
            # ⭐ 2026-10-04 用户口径（改）：**取消**「额度用尽就当天不匹配」。
            #   没额度时照样进星球页匹配；弹出额度弹层就点「去聊天」（见 do_match）。
            # ⭐ 2026-10-06（用户：「怎么还是在一直走匹配啊」）
            #   确认「匹配这会儿没戏」→ **整段静默**，期间连 to_planet 都不做（省掉 ~100s 无效导航）。
            #   静默源：① 额度读完为 0（quota_out_at）② 匹配通道故障（match_broken_at）。
            #   取两者里**最新**的那个；到期（MATCH_SILENT_COOL）自动再试一次。
            _qo_today = str(st.get("quota_out_date") or "") == _today()
            _brk_src = float(st.get("match_broken_at", 0) or 0)
            _brk_silent = _brk_src > 0 and (now - _brk_src) < MATCH_SILENT_COOL
            _silent = _qo_today or _brk_silent
            if _silent:
                # 只在静默开始那一轮说一次，别每分钟刷屏
                _tag = "额度:" + _today() if _qo_today else "故障:%d" % int(_brk_src)
                if st.get("silent_note_at") != _tag:
                    if _qo_today:
                        log("  ⏸ 今天匹配额度已读完为 0（每日配额，当天不会再有新次数）"
                            " → **当天不再进匹配**，只走唤醒/巡检；次日自动恢复")
                    else:
                        log("  ⏸ 匹配通道故障静默中 → %d 分钟内不再进匹配，只走唤醒/巡检；"
                            "%d 分钟后再试一次"
                            % (MATCH_SILENT_COOL // 60,
                               int((MATCH_SILENT_COOL - (now - _brk_src)) // 60) + 1))
                    st["silent_note_at"] = _tag
                st["last_match"] = now      # ⭐ 不刷新则下面条件恒成立 → 每轮都白判一次
            elif now - float(st.get("last_match", 0)) >= MATCH_EVERY:
                # ⭐ 2026-10-06 用户口径（两轮定稿）：「匹配前先把聊天导航红点消除完了再匹配」
                #   +「清红点**不是进入返回**，是**进入然后回消息**」
                #   → 有红点 = 有新消息：本轮**不匹配**，先回去回消息（回消息本身即消红点）。
                #   ⭐ 进入式清扫（M1 定稿）：tap 进会话=把消息标已读，所以 `_dot_sweep`
                #      **tap 之前先用 pending() 快照确认这行是可回复的真人待回**；解析不出就
                #      跳过、绝不 tap ⇒「标已读却不回」在源头不可达（宁可不进，绝不盲扫）。
                if dot_block_match(st):
                    st["fail"] = 0
                    return            # 立刻进下一轮 → 去看消息（不等 150s 空转）
                log("── 无人聊 → 星球匹配认识新人 ──")
                with _Tick("星球匹配", st):
                    _n, _rs = do_match(st)
                st["last_match"] = time.time()
                # ⭐ 2026-10-06 用户口径：「匹配 3 次之后需要检测一下聊天导航那里有没有红点」
                #   有红点 = 匹配期间来了新消息 → 消息优先（沿用"有新消息优先回复"的定稿口径）：
                #   本轮**不唤醒/不切号**，直接结束这一轮 → 下一轮立刻回查消息。
                _dot_has, _dot_n = _chat_nav_dot()
                log("  🔎 匹配 %d 次后检测聊天导航红点：%s（红像素 %s）"
                    % (MATCH_N,
                       "有 → 优先回去看消息" if _dot_has is True else _dot_desc(_dot_has),
                       _dot_n))
                if _dot_has is True:
                    st["fail"] = 0
                    return
                # ⭐ 2026-10-03 额度联动（用户口径：星球页有匹配次数显示，
                #   额度耗尽就该立刻转唤醒，别干等 30min 定时器）：
                #   连续 2 轮匹配空手 → 判定额度/候选耗尽 → 本轮立即唤醒。
                #
                # 🔴 2026-10-06 修「误判额度耗尽」（用户当场质疑：
                #   「匹配次数还有啊 怎么就到了唤醒好友的流程去了」）：
                #   旧版只看 `_n <= 0` → 把**导航失败**也当成"没额度"。
                #   实测 00:34 连续 3 次 `no_planet`（**压根没进到星球页**，
                #   匹配按钮都没点到）却被报成"额度/候选耗尽"→ 本轮匹配白白放弃。
                #   现在按**细分原因**分流：
                #     · 全是导航类（no_planet/no_button）→ **不是额度问题**，
                #       不累加 empty_streak（避免误判耗尽），日志如实写"没进到星球页"
                #     · 否则（no_match / 混合 / 弹层）→ 才计时耗尽
                _NAV = ("no_planet", "no_button")
                _nav_only = bool(_rs) and all(r in _NAV for r in _rs)
                # ⭐ 2026-10-06（用户：「匹配的时候 OCR 分析一下剩余次数」）
                #   do_match 已经**读出来**额度用完 → **立刻**转唤醒，不用等「连续 2 轮空手」
                #   （那是旧的推断路径）。冷却 WAKE_EVERY：匹配间隔才 60s，没冷却会每轮唤醒刷屏。
                #   两条路都算「读出来的用完」：
                #     · quota_out = 点按钮**之前**就看到弹层/读到 0 次（do_match 里读的）
                #     · no_quota  = 点按钮**之后**弹出「今日免费匹配机会已用完」（match_once 读的）
                _quota_out = ("quota_out" in (_rs or [])) or ("no_quota" in (_rs or []))
                # ⭐ 2026-10-06 修：当天标记**不能**受下面唤醒冷却的牵连。
                #   旧写法把它塞进 `if _qo_fire:` 里 → 冷却期内 `_qo_fire` 恒 False
                #   → `quota_out_date` 永远是 None → 静默**从来没生效过**（实测 06:39~06:42
                #   连续 3 轮照进匹配，每轮白烧 ~23s）。状态标记与节流必须解耦。
                if _quota_out and str(st.get("quota_out_date") or "") != _today():
                    st["quota_out_date"] = _today()
                _qo_today = str(st.get("quota_out_date") or "") == _today()
                # 匹配当天没戏 → 唤醒是唯一活儿，间隔也跟着收紧（WAKE_EVERY → WAKE_EVERY_IDLE）
                _wake_gap = WAKE_EVERY_IDLE if _qo_today else WAKE_EVERY
                _qo_since = float(st.get("quota_out_at", 0) or 0)
                _qo_cool = (_qo_since > 0) and (now - _qo_since < _wake_gap)
                _qo_fire = _quota_out and (not _qo_cool)
                # ⭐ 2026-10-06 兜底（用户现场报「匹配用完了不走唤醒」）：
                #   `match_nav_fail_streak` 以前是个**只写不读的死变量** —— 连续二十几次
                #   `no_planet` 也判不出「耗尽」，匹配就这么空转到天亮。
                #   现在：连续 ≥MATCH_NAV_FAIL_MAX 次导航失败 = **匹配通道故障** → 转唤醒。
                # ⚠️ 防刷屏：故障态下不能每轮都唤醒（匹配间隔才 60s）。判故障后记
                #   `match_broken_at`，冷却 WAKE_EVERY(30min) 内只简写一行，不再重复唤起。
                _broken_since = float(st.get("match_broken_at", 0) or 0)
                _broken_cool = (_broken_since > 0) and (now - _broken_since < WAKE_EVERY)
                if _n <= 0:
                    if _nav_only:
                        st["match_nav_fail_streak"] = int(st.get("match_nav_fail_streak", 0)) + 1
                        _s = st["match_nav_fail_streak"]
                        if _broken_cool:
                            log("  ⚠ 匹配仍进不去星球页（%s）｜故障态冷却中，%d 分钟后才再唤起"
                                % (",".join(sorted(set(_rs))),
                                   int((WAKE_EVERY - (now - _broken_since)) // 60) + 1))
                        else:
                            log("  ⚠ 匹配**没执行**（%s）→ 不是额度问题：没进到星球页/点不到按钮"
                                "（连续第 %d 次，满 %d 次判故障→转唤醒，不再干等 30min）"
                                % (",".join(sorted(set(_rs))), _s, MATCH_NAV_FAIL_MAX))
                    elif _quota_out:
                        # 额度用完是**读出来**的确定结论，不进"空手连击"计数（那套是猜的）
                        st["match_empty_streak"] = 0
                        st["match_nav_fail_streak"] = 0
                        if _qo_cool:
                            log("  ⚠ 灵魂匹配额度仍为 0 ｜冷却中，%d 分钟后才再唤起"
                                % (int((WAKE_EVERY - (now - _qo_since)) // 60) + 1))
                    else:
                        st["match_empty_streak"] = int(st.get("match_empty_streak", 0)) + 1
                        st["match_nav_fail_streak"] = 0
                else:
                    st["match_empty_streak"] = 0
                    st["match_nav_fail_streak"] = 0
                    if "match_broken_at" in st:
                        del st["match_broken_at"]     # ⭐ 匹配恢复 → 解除故障态
                _exhausted = int(st.get("match_empty_streak", 0)) >= 2
                _nav_streak = int(st.get("match_nav_fail_streak", 0))
                _nav_broken = (_nav_streak >= MATCH_NAV_FAIL_MAX) and (not _broken_cool)
                if _n <= 0 and ((not _nav_only and (_exhausted or now - float(st.get("last_wake", 0)) >= WAKE_EVERY))
                                or _nav_broken or _qo_fire):
                    if _qo_fire:
                        log("  ⛔ 灵魂匹配额度已用完（OCR 读出来的，不是猜的）→ 立刻转唤醒老联系人")
                        st["quota_out_at"] = now
                        # （当天标记 quota_out_date 已在上面**无条件**设置，不随冷却走）
                    elif _nav_broken:
                        log("  ⛔ 匹配通道故障：连续 %d 次进不到星球页/点不到按钮"
                            "（根因可能是额度用完、星球页改版、或 App 卡死）"
                            " → 不再干等，立刻转唤醒老联系人" % _nav_streak)
                        st["match_broken_at"] = now
                        st["match_nav_fail_streak"] = 0     # ⭐ 本次已响应，重新数
                    elif _exhausted:
                        log("  ⤵ 连续 %d 轮匹配无结果 → 判定额度耗尽 → 立即唤醒老联系人"
                            % st["match_empty_streak"])
                    else:
                        log("  ⤵ 匹配无结果（额度/候选耗尽）→ 唤醒老联系人")
                    with _Tick("唤醒老人", st):
                        wake_old(st)
                    st["last_wake"] = time.time()
                    st["match_empty_streak"] = 0      # ⭐ 唤醒后重新计数
            # ⭐ 2026-10-03 口径更新（用户）：不管匹配有无额度，唤醒照常推进——
            #   匹配、唤醒各自到点就跑，互不互斥（额度联动仅作"提前唤醒"加速器）
            # 当天额度读完为 0（匹配整天不跑）→ 唤醒间隔收紧，别让机器空转
            _wgap = WAKE_EVERY_IDLE if str(st.get("quota_out_date") or "") == _today() else WAKE_EVERY
            if now - float(st.get("last_wake", 0)) >= _wgap:
                log("── 无人聊 → 唤醒老联系人（与匹配并行推进）──")
                with _Tick("唤醒老人", st):
                    # ⭐ 2026-10-06：优先补发 ghost（匹配成功却从没发过开场白的人，每轮≤1）
                    wake_ghost(st)
                    wake_old(st)
                st["last_wake"] = time.time()
            else:
                log("── 无人聊（限频中）→ 静默巡检 ──")
            st["fail"] = 0
            # ⭐ 2026-10-03 空闲等待改可中断轮询：有新消息 10s 内醒来处理，
            #   不再干等整个 POLL_IDLE（150s）。pending() 有设备端指纹快路径，
            #   无变化时仅几 ms，代价可忽略。
            _t0 = time.time()
            while time.time() - _t0 < POLL_IDLE:
                time.sleep(10)
                try:
                    _rows = im.pending() or []
                    # ⭐ 2026-10-04（F2）：统一口径 —— 僵尸待回不再把等待打断
                    #   （原来几天前的已读消息每 10s 唤醒一次，系统永远进不了安静巡检）
                    if _has_real_pending(_rows):
                        log("  ⚡ 空闲等待中来新消息 → 提前结束等待")
                        break
                except Exception:
                    pass
    finally:
        GL.end_round()
        _WD["in_round"] = False     # ⭐ 锁已还，看门狗停止盯（防替别人自杀）


_WD = {"in_round": False}   # ⭐ 2026-10-03 看门狗状态（dict 免 global 声明）


def _watchdog():
    """⭐ 看门狗线程：**仅在本进程持有整轮锁期间**盯全局锁心跳。

    为什么必须有它（2026-10-03 实测死结）：
      daemon 卡在共享目录 IO（MuMu 映射假死，Python 文件 IO 无超时可救）
      → 进程"活着但不干活" → guard 看 pid 存活不重启 → 新 daemon 被单例锁挡住
      → 全系统永久卡死，消息没人处理（锁心跳 14 分钟停在 daemon:pull）。
    修法：心跳 >8 分钟没更新 → 判定卡死 → os._exit(9) → guard 60s 内自动重拉。
    8 分钟阈值安全：持锁期间最长 sleep 只有 180s（设备失败分支）。
    没持锁时不检查（锁是别人的，不能替别人自杀）。

    ⭐ 2026-10-04（N15）补**第二道闸**：持锁心跳闸管不到「启动阶段」
    （main() 里的 calibrate / 预热）——实测 daemon 正是卡在那里，静默 80 分钟。
    故增加**进程级存活脉冲** `_PULSE`（log()/_pulse() 刷新）：超 HANG_LIMIT 一律自杀，
    与锁归属无关（卡死的是"我"自己）。
    """
    import soul_global_lock as _GL
    while True:
        time.sleep(60)
        try:
            # ⭐ N15：全局存活脉冲（覆盖持锁之前的启动/长等待阶段）
            age_all = time.time() - _PULSE["t"]
            if age_all > HANG_LIMIT:
                # ⚠️ 自杀路径**必须无条件执行**：log()/读锁都可能抛异常，
                #   若被下面的 except 吞掉就成了"该死不死的看门狗"（实测踩到）。
                #   故先尽力记录现场，再无条件 os._exit(9)。
                try:
                    info = _GL.read_lock()
                    log("⛔ 看门狗：%.1f 分钟无任何动作（锁步骤 %s）→ 判定卡死，自杀等 guard 重拉"
                        % (age_all / 60.0, (info or {}).get("step", "?")))
                    if info and int(info.get("pid") or 0) == os.getpid():
                        _GL.end_round()
                        log("    · 己锁已释放，新 daemon 可立即接管")
                except Exception:
                    pass
                os._exit(9)

            if not _WD.get("in_round"):
                continue
            info = _GL.read_lock()
            if not info:
                continue
            hb = info.get("heartbeat") or info.get("ts") or 0
            if not hb:
                continue
            age = time.time() - hb
            if age > 8 * 60:
                # ⚠️ 同样：记录归记录，退出必须无条件（见上 N15 处说明）。
                try:
                    log("⛔ 看门狗：锁心跳 %.1f 分钟没更新（步骤 %s）→ 判定卡死，"
                        "自杀等 guard 重拉" % (age / 60.0, info.get("step", "?")))
                    # ⭐ N13：锁是**本进程自己**的 → 先释放再死；否则新 daemon 要等
                    #   心跳 15min/TTL 55min 才能接管（实测空窗数十分钟）。
                    if int(info.get("pid") or 0) == os.getpid():
                        _GL.end_round()
                        log("    · 己锁已释放，新 daemon 可立即接管")
                except Exception:
                    pass
                os._exit(9)
        except Exception:
            pass


def _singleton():
    """守护单例（**独立锁文件版**）。

    ⭐ 2026-10-04 修「双守护」根因：
      原实现拿 PIDF（信息文件）当锁文件，而重启/清理脚本会 os.remove(PIDF)；
      文件一删，锁随旧 inode 一起失效 → 新进程在**新建的**文件上重新加锁
      → 两个 daemon 同时活着（实测 00:32:09 起了 11340/7092，两个都在跑）。
      改为**独立锁文件 LOCKD（从不删除）**承载互斥；PIDF 只存 pid 供排查，
      随便删都不影响单例。
    ⭐ 2026-10-05 再加一层：内核命名互斥锁（CreateMutexW）先行。
      实测本机 msvcrt 文件锁存在竞态（同文件可被多进程同时 a+ 打开，
      出现双 guard/双 daemon 都活着、第二个进程卡在 open/locking 不退出），
      内核互斥由操作系统保证全局唯一，先于文件锁判定。
    """
    try:
        import ctypes
        # ⭐ 2026-10-06 修（P1#12）：照抄 `_daemon_guard.py:192-196` 的正确写法。
        #   旧写法 `ctypes.windll.kernel32.CreateMutexW(...)` 之后再调 `GetLastError()`：
        #   ctypes 默认不保存 last-error，两次调用之间 Python 自身的 Win32 调用会把错误码
        #   冲掉 → ERROR_ALREADY_EXISTS(183) 经常读不到 → 第二个 daemon 以为自己是唯一
        #   → 双守护同时写同一份 wshot.png + 同时驱动同一台设备互相打架。
        #   正确姿势：use_last_error=True + ctypes.get_last_error()。
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        h = k32.CreateMutexW(None, False, "SoulDaemon_%s" % VM)
        if not h:
            return False
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            k32.CloseHandle(h)
            log("已有守护进程（内核互斥）在跑 → 本进程退出")
            return False
        globals()["_MUTEXH"] = h
    except Exception as _e:
        log("内核互斥异常 %r → 退化文件锁" % (_e,))
    try:
        import msvcrt
        fh = open(LOCKD, "a+")
        fh.write("x")
        fh.flush()
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        globals()["_LOCKFH"] = fh       # 保持引用 → 进程退出自动释放锁
    except Exception as _e:
        log("已有守护进程在跑 → 本进程退出 (%r)" % (_e,))
        return False
    try:
        io.open(PIDF, "w", encoding="utf-8").write(str(os.getpid()))
    except Exception:
        pass
    return True

def summary(t0):
    log("⏱ 本轮总耗时 %.1f s ｜ sleep 省下 %.1f s ｜ pull 变化检测跳过 %d 次(探针 %.0fms)"
        % (time.time() - t0, _ACC["saved"], _STAMP["skips"], _STAMP["probe_ms"]))
    log(_acc_line())


def main():
    once = "--once" in sys.argv
    if not once and not _singleton():
        # ⭐ 2026-10-04：必须**硬退出**。实测「return」后进程不消失——
        #   解释器收尾会卡在 onnxruntime 的非守护线程上，残留成 CPU=0/4MB 空壳
        #   （00:44:59 pid=12732 两分钟不退）。os._exit 跳过线程收尾/atexit，立即终止。
        os._exit(0)
    # ⭐ 2026-10-06 崩溃取证：23:39:44 守护在匹配途中**静默消失**（无 traceback、无日志、
    #   Windows 事件日志也无记录），132s 后才被 guard 拉回 —— 是硬死，但**没有任何证据**，
    #   无法定位。这里开 faulthandler：下次硬死（段错误/栈溢出/致命信号）会把**所有线程**的
    #   Python 栈 dump 到 crash 文件。句柄挂模块级防被回收；整段失败即跳过（取证不能拖垮启动）。
    #   必须在看门狗线程启动之前启用。
    global _CRASH_FH
    try:
        import faulthandler as _fh
        _crash = os.path.join(OUTD, "crash.%s.log" % VM)
        _CRASH_FH = io.open(_crash, "a", encoding="utf-8", errors="replace")
        _fh.enable(file=_CRASH_FH, all_threads=True)
        log("[取证] faulthandler 已启用 → %s（下次硬死留现场）" % _crash)
    except Exception as _fe:
        try:
            log("  (崩溃取证 faulthandler 跳过: %r)" % (_fe,))
        except Exception:
            pass
    # ⭐ 2026-10-04（N15）：看门狗**提前上线**，必须早于 soul.calibrate() / 预热 ——
    #   否则卡在启动阶段时看门狗线程还没创建（实测静默 80 分钟的成因）。
    import threading as _th
    _th.Thread(target=_watchdog, daemon=True).start()
    try:
        _cw, _chh = soul.calibrate()
        log("📐 屏幕分辨率校准：%dx%d（坐标按实测换算）" % (_cw, _chh))
    except Exception as _ce:
        log("  (分辨率校准跳过: %r)" % (_ce,))
    log("=" * 64)
    log("Soul 24h 守护启动  vm=%s  pid=%d  model=%s" % (VM, os.getpid(), MODEL))
    log("节奏：空闲巡检 %ds ｜ 匹配间隔 %dmin ｜ 唤醒间隔 %dmin ｜ 小时上限 %d"
        % (POLL_IDLE, MATCH_EVERY // 60, WAKE_EVERY // 60, SEND_CAP_H))
    log("全天不分昼夜（用户 2026-10-03 口径）")
    install_accel()
    # ⭐ 看门狗已在启动阶段提前上线（见本函数开头），此处不再重复启动
    log("⚡ 提速：SEARCH_TRACE=%s（取证截图已关，省约 20s）｜ FIND_PAGES=%s"
        % (os.environ.get("SOUL_SEARCH_TRACE"), os.environ.get("SOUL_FIND_PAGES")))
    log("=" * 64)

    # ⚡ 预热：首次 OCR 要加载 onnxruntime+模型（约 3s），先热一遍，
    #    免得这笔开销落在真实交互里被误判成"卡了"
    try:
        import soul_read
        t0 = time.time()
        soul_read.items()
        log("⚡ 预热 OCR 完成 %.1fs（后续每帧仅 ~1.2s）" % (time.time() - t0))
    except Exception as e:
        log("  (预热 OCR 跳过: %r)" % (e,))
    try:
        _llm("说一个字：好", temp=0.1, timeout=60)
        log("⚡ 预热模型完成")
    except Exception as e:
        log("  (预热模型跳过: %r)" % (e,))

    st = load_state()
    # ⭐ 2026-10-05：启动时清僵尸锁——上轮 daemon 崩溃/被杀后锁文件残留，
    #   新 daemon 启动看到「锁被占用」就 sleep(POLL_IDLE) 等待，但如果锁在
    #   sleep 期间被外部删除（或心跳超时），daemon 不会重试直接退出。
    #   启动时强制删一次锁文件：能删说明是僵尸锁（真在跑的轮次会持文件句柄，
    #   删不掉），删完下一轮 cycle 就能正常 start_round。
    try:
        import soul_global_lock as _GL
        _lf = _GL.lock_file()
        if os.path.exists(_lf):
            os.remove(_lf)
            log("[锁] 启动时清理残留锁文件")
    except OSError:
        pass  # 删不掉 = 真有人在跑，不动它
    if once:
        log("（--once 模式：只跑一轮）")
        t0 = time.time()
        try:
            _cycle(st)
        except Exception:
            log("!! 单轮异常:\n" + traceback.format_exc())
        summary(t0)
        save_state(st)
        log("（--once 结束）")
        return
    while True:
        t0 = time.time()
        try:
            if int(st.get("fail", 0)) >= FAIL_MAX:
                log("⛔ 连续失败 %d 次 → 熔断暂停 %d 分钟" % (st["fail"], FAIL_PAUSE // 60))
                _pause_loop(FAIL_PAUSE)      # ⭐ 分片睡：持续刷新存活脉冲，避免被看门狗误判卡死
                st["fail"] = 0
                save_state(st)
                continue
            _cycle(st)
            summary(t0)
            _ACC["saved"] = 0.0
            for k in ("shot", "ocr", "sh", "tap"):
                _ACC[k][0] = 0
                _ACC[k][1] = 0.0
        except KeyboardInterrupt:
            log("收到中断 → 退出")
            break
        except Exception:
            st["fail"] = int(st.get("fail", 0)) + 1
            log("!! 主循环异常（第 %d 次）:\n%s" % (st["fail"], traceback.format_exc()))
            time.sleep(60)
        save_state(st)


if __name__ == "__main__":
    main()
