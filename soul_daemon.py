# -*- coding: utf-8 -*-
"""
Soul 24 小时守护进程 —— 本地模型干活，云端可复盘
================================================
用户口径（2026-10-03）：
  「让本地模型24小时干活，24小时监控 soul 对话数据库：
     有人聊的时候优先聊天；没人聊天的时候去星球匹配认识新人；
     顺便把之前的聊天对象重新唤醒。」
  · 全天不分昼夜（节假日也不分）—— 时段两档（白天只回/晚上全流程）已作废。

三态调度（优先级从高到低）：
  ① 有真人待回 → 优先回复（jianghua 生成 + 确定性闸 + soul_reply 发送）
  ② 空闲      → 星球匹配认识新人（复用 soul_match，抢占式：中途来消息立刻中断）
  ③ 间歇      → 唤醒老联系人（im.follow 选「聊过≥10句且冷≥12h」的人 + jianghua 开场）

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
import os, sys, io, json, time, sqlite3, subprocess, urllib.request, traceback
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

MEMDB  = _sp(BASE, "soul_memory.db")          # 累积库（只增不减）
STATE  = os.path.join(OUTD, "state.%s.json" % VM)
LOGF   = os.path.join(OUTD, "daemon.%s.log" % VM)
PIDF   = os.path.join(OUTD, "daemon.%s.pid" % VM)
LOCKD  = os.path.join(OUTD, "daemon.%s.lock" % VM)   # ⭐ 独立锁文件（永不删除）
STDOUT = os.path.join(OUTD, "stdout.%s.txt" % VM)

HOST  = "http://192.168.10.210:11434"          # 本机 Ollama
MODEL = "jianghua"                             # 人设模型
N_MSG = 2                                      # 每条待回最多发几条
USE_FAST = os.environ.get("SOUL_FAST_REPLY", "1") == "1"   # 快速回复路径（soul_fast）

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
MATCH_N     = 3          # 每次匹配几个
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
        with io.open(STATE, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def save_state(st):
    try:
        with io.open(STATE, "w", encoding="utf-8") as f:
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
        c = sqlite3.connect(MEMDB)
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
    返回 "SENT"/"SKIP"(硬闸) /"FAIL"。do_reply 与 wake_old 共用。"""
    # ⭐ 2026-10-05 账号安全闸（放这里：do_reply 与 wake_old 两条发送路径都覆盖）
    if not _account_gate_ok():
        return "SKIP"
    if allow_chain:
        # ⭐ 2026-10-03 拆分条只走全路径（快速路径不认连发豁免）
        try:
            return "SENT" if sr.reply(name, texts, allow_chain=True) else "FAIL"
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
        return "SENT" if sr.reply(name, texts) else "FAIL"
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
        c = sqlite3.connect(MEMDB)
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
        return []
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
    """把正式库(im.IMDB)增量并入累积库 —— Soul 会清库，这里只增不减。"""
    src = im.IMDB
    if not os.path.exists(src):
        return 0
    ME = str(im.ME)
    m = sqlite3.connect(MEMDB)
    m.execute("""CREATE TABLE IF NOT EXISTS chatmsg(
      sessionId TEXT, msgId TEXT, senderId TEXT, receiverId TEXT, localTime INTEGER,
      msgType INTEGER, text TEXT, msgContent TEXT, PRIMARY KEY(sessionId, msgId))""")
    m.execute("""CREATE TABLE IF NOT EXISTS session(
      sessionId TEXT PRIMARY KEY, toUserId TEXT, chatType INTEGER, unReadCount INTEGER,
      timestamp INTEGER, lastMsgText TEXT)""")
    m.execute("CREATE TABLE IF NOT EXISTS nick(uid TEXT PRIMARY KEY, name TEXT)")
    m.execute("CREATE INDEX IF NOT EXISTS idx_cm_sid ON chatmsg(sessionId, localTime)")
    m.commit()
    added = 0
    try:
        s = sqlite3.connect(src)
        ccols = set(x[1] for x in s.execute("PRAGMA table_info(chatmsg)").fetchall())
        use = [c for c in ("sessionId", "msgId", "senderId", "receiverId", "localTime",
                           "msgType", "text", "msgContent") if c in ccols]
        if use:
            for r in s.execute("SELECT %s FROM chatmsg" % ",".join(use)).fetchall():
                d = dict(zip(use, r))
                if not str(d.get("sessionId") or "").startswith(ME):
                    continue
                try:
                    cur = m.execute("INSERT OR IGNORE INTO chatmsg(%s) VALUES(%s)"
                                    % (",".join(use), ",".join("?" * len(use))),
                                    [d.get(k) for k in use])
                    added += cur.rowcount or 0
                except Exception:
                    pass
        scols = set(x[1] for x in s.execute("PRAGMA table_info(session)").fetchall())
        suse = [c for c in ("sessionId", "toUserId", "chatType", "unReadCount",
                            "timestamp", "lastMsgText") if c in scols]
        if suse:
            for r in s.execute("SELECT %s FROM session" % ",".join(suse)).fetchall():
                d = dict(zip(suse, r))
                if not str(d.get("sessionId") or "").startswith(ME):
                    continue
                try:
                    m.execute("INSERT OR REPLACE INTO session(%s) VALUES(%s)"
                              % (",".join(suse), ",".join("?" * len(suse))),
                              [d.get(k) for k in suse])
                except Exception:
                    pass
        s.close()
        m.commit()
    except Exception as e:
        log("  !! merge 出错: %r" % (e,))
    # 昵称映射
    try:
        for uid, name in (im.names() or {}).items():
            m.execute("INSERT OR REPLACE INTO nick(uid,name) VALUES(?,?)", (str(uid), str(name)))
        m.commit()
    except Exception:
        pass
    m.close()
    return added


def _sid_of(uid):
    try:
        c = sqlite3.connect(MEMDB)
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
        c = sqlite3.connect(MEMDB)
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
        c = sqlite3.connect(MEMDB)
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
        c = sqlite3.connect(MEMDB)
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


def gate(raw, incoming, n, my_recent=None):
    """确定性闸：剔复述她/复述我/违禁词/说教词。返回 (可用句, 剔除明细)"""
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
        rich = [b for b in BAN_RICH if b in ln]
        if rich:
            dropped.append(["装富%s" % rich, ln]); continue
        ph = [p for p in PREACH_WORDS if p in ln]
        if ph:
            dropped.append(["说教%s" % ph, ln]); continue
        if len(ln) > 22:
            dropped.append(["超长%d" % len(ln), ln]); continue
        kept.append(ln)
    return kept[:n], dropped


# ⭐ 2026-10-04 双开人设：实例 N>0 用独立身份；实例0 返回**原字面量**（逐字不变）。
def _who(kind):
    """返回「我是谁」身份串。kind ∈ {reply, wake, pick}。"""
    try:
        import soul_persona as _sp
        if _sp.vm_index() > 0:
            a = _sp.ident()
            if kind == "reply":
                return "%s（男，%s，穷、不装富、不吹牛）" % (a["name"], a["job"])
            if kind == "wake":
                return "%s（男，%s，%s人）" % (a["name"], a["job"], a["city"])
            if kind == "pick":
                return "%s（男，%s，穷、不装富、不吹牛，%s人）" % (a["name"], a["job"], a["city"])
    except Exception:
        pass
    return {"reply": "江华（男，在厂里上班，穷、不装富、不吹牛）",
            "wake": "江华（男，在厂里上班，重庆人）",
            "pick": "江华（男，在厂里上班，穷、不装富、不吹牛，重庆人）"}[kind]


def gen_reply(her_msg, hist, attempt=0, banned=None):
    """生成回复。attempt>0 = 上一稿被闸剔掉了，换要求重来（**换话题/换说法**）。"""
    ctx = "".join(("我: " if h["role"] == "me" else "她: ") + h["text"] + "\n" for h in hist)
    scarce = "" if len(hist) >= 3 else "（上下文很少，别硬接、别乱猜，回得短一点）\n"
    if attempt > 0:
        scarce += ("⚠️ 你上一稿被判为「炒冷饭/复述/不符人设」被砍掉了。\n"
                   "**必须换完全不同的话**：可以反问她、说自己这边的事、或换个新话题，\n"
                   "绝不能再出现下面这些句子（包括意思相近的）：\n%s\n"
                   % ("\n".join("· " + str(b)[:30] for b in (banned or [])[:6]) or "· （你自己刚说过的）"))
    p = ("【你俩最近的对话，按时间顺序】\n%s\n"
         "【她刚发来的这一句】\n%s\n\n"
         "你是%s。请**接着上下文**回复她："
         "直接输出 %d 条消息，每行一条、≤20 字、口语、不要编号、不要解释、"
         "**不要重复你自己刚说过的话**、不要复述她的话、不要编造上下文里没有的人和事。%s"
         % (ctx or "(这是你俩首次对话)\n", her_msg, _who("reply"), N_MSG, scarce))
    return _llm(p, temp=(0.8 if attempt == 0 else 0.95))


def gen_wake(name, hist, idle_h):
    ctx = "".join(("我: " if h["role"] == "me" else "她: ") + h["text"] + "\n" for h in hist)
    p = ("【你和「%s」之前聊过（按时间序）】\n%s\n"
         "（这段对话已经冷了约 %.0f 小时，最后是我说话、她没接。）\n\n"
         "你是%s。用你的口吻**换个新话题**自然开口一句，"
         "≤18 字，口语、轻松、不刻意。**不要问「在吗」「最近好吗」「怎么不理我」**，"
         "别重复上面出现过的内容，不要说教，不要编造。"
         "直接输出要发的 1 句，不要引号、不要解释。"
         % (name, ctx or "(几乎没有聊天记录)", idle_h, _who("wake")))
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
            if not soul.app_running():
                log("  设备：Soul 未运行 → 启动")
                soul.launch_app(wait=12)
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
    p = ("【你俩最近的对话】\n%s\n【她刚发来的这一句】\n%s\n\n"
         "【智囊团给出的候选话术（每条很短）】\n%s\n\n"
         "你是%s。"
         "从候选里**挑 1 条最贴合你人设和当前语境的**，可微调语气但别大改，"
         "优先挑能勾她主动找你、能推进关系一步、不跪舔不倒贴的那种；"
         "直接输出那一条（<=20 字），不要解释、不要编号。"
         % (ctx or "(这是你俩首次对话)\n", her_msg,
            "\n".join("· " + str(c) for c in cand), _who("pick")))
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

def _account_gate_ok():
    """⭐ 2026-10-05 账号安全闸：设备当前登录账号必须等于本实例配置账号，
    否则**绝不代发**（模拟器重启/重登后实测会从账号2 切到账号1 —— 串号级事故）。
    结果缓存 300s，避免每轮都吃 mumu-cli 超时。
    """
    now = time.time()
    if now - _ACCT_GATE["ts"] < 300:
        return _ACCT_GATE["ok"]
    ok, dev_me = True, None
    try:
        dev_me = im._device_me()
        cfg_me = str(im.ME)
        if dev_me and dev_me != cfg_me:
            log("  ⛔ 账号安全闸：设备当前账号 %s ≠ 本实例配置账号 %s → 本轮全部跳过（防串号）"
                % (dev_me, cfg_me))
            ok = False
    except Exception:
        pass
    _ACCT_GATE.update({"me": dev_me, "ok": ok, "ts": now})
    return ok


def do_reply(name, her_text, st, sid_hint=None):
    """对单个待回：生成 → 发送。返回 'SENT'/'SKIP'/'FAIL'
    ⭐ 2026-10-03 用户方案：在线智囊团(三系辩论)先出多条短话术 → 本地挑一条 → 太长拆两条发
    降级保护：智囊团不可用 → 本地 jianghua 生成（原链路）
    """
    # ⭐ 2026-10-05 账号安全闸：设备账号≠配置账号 → 直接跳过，绝不代发（防串号）
    if not _account_gate_ok():
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
    hist = hist_of(sid)
    my_recent = [h["text"] for h in hist if h["role"] == "me"]
    log("  「%s」她发来: %s" % (name, str(her_text)[:32]))

    # ⭐ 2026-10-05 预生成话术池（soul_pregen 夜间批量）——命中即秒回，仍过确定性闸
    #   匹配键=昵称+她最后一句原文；任何异常 → None → 走原生成链路（零风险）
    _pg = None
    try:
        import soul_pregen as _pregen
        _pg = _pregen.take(name, her_text)
    except Exception as _e:
        log("     !! 预生成池异常（%r）→ 走原链路" % (_e,))
    if _pg:
        gated, dropped = gate("\n".join(_pg), her_text, N_MSG, my_recent)
        log("   ⚡预生成池命中（%s）→ 闸后: %s | 剔: %s" % (name, gated, dropped))
    else:
        gated, raw, dropped = [], "", []
        _brain_ok = False
        try:
            import soul_brain as _SB
            _bt = _SB.brain_reply(hist, her_text)
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
                _brain_ok = bool(gated)
        except Exception as e:
            log("     !! 在线智囊团异常（%r）→ 降级本地模型" % (e,))

        if not _brain_ok:
            for attempt in range(RETRY_MAX):
                try:
                    raw, ms = gen_reply(her_text, hist, attempt=attempt,
                                        banned=(raw.splitlines() + my_recent[-3:]))
                except Exception as e:
                    log("     !! 生成失败(第%d次) %s: %r" % (attempt + 1, name, e))
                    continue
                gated, dropped = gate(raw, her_text, N_MSG, my_recent)
                log("     [第%d稿] %r | 闸后: %s | 剔: %s" % (attempt + 1, raw, gated, dropped))
                if gated:
                    break
    if not gated:
        n = _bump_try(st, name, her_text)
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
    def _interrupted_daemon():
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
    try:
        names, why = M.match_batch(MATCH_N, dry=False)
        log("  匹配完成 %s | 结束原因: %s" % (names, why))
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
        return len(names)
    except Exception as e:
        log("  !! 匹配异常: %r" % (e,))
        return 0


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

        if real:
            log("── 有人在聊：%d 个待回 ──" % len(real))
            for ts, name, unread, text, lt, _sid6, _mt in real:
                if not _hour_budget(st):
                    log("  ⛔ 本小时发送已达上限 %d → 暂停回复" % SEND_CAP_H)
                    break
                if _mt == getattr(im, "VOICE_MT", 5):
                    # ⭐ 2026-10-04 她末条是语音（文字来自 Soul 自带转写）→ 标记，供语音出站通道决策
                    log("  🎙 %s 末条是语音（转写: %r）" % (name, str(text)[:24]))
                with _Tick("回复·%s" % name, st):
                    do_reply(name, text, st, sid_hint=_sid6)
                GL.touch("daemon:reply:%s" % name)
                time.sleep(1.5)
            st["fail"] = 0
            st["last_match"] = 0        # ⭐ 2026-10-03 首要目标：回复完立即去匹配新人
            time.sleep(POLL_HOT)          # 趁热再看一眼
        else:
            now = time.time()
            # ⭐ 2026-10-04 用户口径（改）：**取消**「额度用尽就当天不匹配」。
            #   没额度时照样进星球页匹配；弹出额度弹层就点「去聊天」（见 do_match）。
            if now - float(st.get("last_match", 0)) >= MATCH_EVERY:
                log("── 无人聊 → 星球匹配认识新人 ──")
                with _Tick("星球匹配", st):
                    _n = do_match(st)
                st["last_match"] = time.time()
                # ⭐ 2026-10-03 额度联动（用户口径：星球页有匹配次数显示，
                #   额度耗尽就该立刻转唤醒，别干等 30min 定时器）：
                #   连续 2 轮匹配空手 → 判定额度/候选耗尽 → 本轮立即唤醒。
                #   （OCR 精确读"匹配次数"待拿到星球页截图后接入 do_match）
                if _n <= 0:
                    st["match_empty_streak"] = int(st.get("match_empty_streak", 0)) + 1
                else:
                    st["match_empty_streak"] = 0
                _exhausted = int(st.get("match_empty_streak", 0)) >= 2
                if _n <= 0 and (_exhausted or now - float(st.get("last_wake", 0)) >= WAKE_EVERY):
                    if _exhausted:
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
            if now - float(st.get("last_wake", 0)) >= WAKE_EVERY:
                log("── 无人聊 → 唤醒老联系人（与匹配并行推进）──")
                with _Tick("唤醒老人", st):
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
    """守护单例（**独立锁文件**版）。

    ⭐ 2026-10-04 修「双守护」根因：
      原实现拿 PIDF（信息文件）当锁文件，而重启/清理脚本会 os.remove(PIDF)；
      文件一删，锁随旧 inode 一起失效 → 新进程在**新建的**文件上重新加锁
      → 两个 daemon 同时活着（实测 00:32:09 起了 11340/7092，两个都在跑）。
      改为**独立锁文件 LOCKD（从不删除）**承载互斥；PIDF 只存 pid 供排查，
      随便删都不影响单例。
    """
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
