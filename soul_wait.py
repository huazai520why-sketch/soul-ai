# -*- coding: utf-8 -*-
"""Soul 自动化「驻留等待器」（2026-09-26 用户要求："不要结束得太快"）

规则（用户 2026-09-26 00:51 原话）：
  · 每轮跑自动化时**不要马上结束**；
  · **等满 5 分钟**仍没人回复 → 才结束本轮；
  · **只要有人回复** → 就一直挂着继续聊，直到**满 1 小时**结束。

本脚本只负责「轮询 + 计时 + 判定」，不含任何 LLM 判断：
  有待回  → 立刻打印 NEW 并退出（exit 2），让上层起来读上下文回复；
  无待回  → 每 30 秒轮询一次，静默满 --idle 秒 → exit 4（本轮结束）；
  总时长  → 达到 --max 秒 → exit 3（本轮结束）。

用法：
  python soul_wait.py --reset                     # 新一轮开始：复位计时（清空已读记录）
  python soul_wait.py                             # 默认 idle=300 max=3600（前台阻塞）
  python soul_wait.py --idle 300 --max 3600
  python soul_wait.py --touch                     # 刚发完消息：静默计时从此刻重算
  python soul_wait.py --once                      # 只查一次，立即返回

退出码：0=once且无待回  2=有待回  3=总时长到  4=静默到时
"""
import sys, io, os, json, time, sqlite3, re
from datetime import datetime

sys.path.insert(0, r"E:\soul")
import soul_im as im
import soul_db  # noqa: F401  (档案/状态层，soul_notes.json)

STATE = r"E:\soul\.soul_wait_state.json"
NOISE_NAMES = ("我的遇见",)                 # 官方/VIP 付费墙
NOISE_TAILS = ("晚安", "睡了", "早点睡", "先睡", "我睡", "睡吧", "明天聊", "改天聊",
               "拜拜", "再见", "好", "好的", "好嘞", "嗯", "嗯嗯", "嗯呢", "ok", "OK",
               "哈哈", "呵呵", "行", "可以的")
_CLOSE_CLEAN = re.compile(r"[\s，。,.!！~～、？?…:：；;\"'“”‘’]")
_EMOJI = re.compile(r"\[[^\]]{1,8}\]|[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]")


def _is_closing(t, turns=None):
    """纯收尾语（晚安/睡了/嗯/好/哈哈…）：不超 6 字且以收尾词结尾 → 不必回
    ⚠️ 2026-09-26 补：**纯表情**在「刚开始聊」时不算收尾——
    新匹配的人对我开场只回一个 [笑哭]/[傻笑]，那是在回应我，不是告别（本轮实测漏了 2 个）。
    → 会话总条数 ≤ 8 时，纯表情一律当作**真实待回**；聊到后面再收到纯表情才算收尾。
    """
    s = _EMOJI.sub("", t or "")
    s = _CLOSE_CLEAN.sub("", s)
    s = _EMOJI.sub("", s)
    if not s:                                      # 纯表情（[笑哭]/😂）
        if turns is not None and turns <= 8:       # 早期对话 → 是回应，不是收尾
            return False
        return True
    if len(s) > 6:
        return False
    return any(s == k or s.endswith(k) for k in NOISE_TAILS)
BAD_STATUS = ("stopped", "skipped", "gift")  # 档案里已判定不再发的人


def _now():
    return datetime.now().strftime("%H:%M:%S")


def _fmt_hms(sec):
    sec = int(max(0, sec))
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def _status_map():
    """拿 nickname -> status（用于排除 stopped/skipped/gift）
    2026-09-26 起本地 SQLite 库废弃，改读 soul_notes.json"""
    try:
        import soul_db
        return soul_db.status_map()
    except Exception:
        return {}


_Q_MARK = ("？", "?", "吗", "嘛", "撒子", "啥子", "什么", "咋", "啷个", "好久",
           "哪", "几", "多少", "要不要", "在不在")


def _looks_like_question(t):
    """她那句是不是在**向我提问**（用于"收尾语豁免"判定）"""
    s = (t or "").strip()
    if not s:
        return False
    return any(k in s for k in _Q_MARK)


def _is_noise(name, text, status, turns=None, keep=False):
    """keep=True 时豁免「收尾语」判定（她刚问了问题，紧接着一个"哈哈"→ 在等我答）"""
    if not name:
        return True
    if name.strip().isdigit():                     # 纯数字 ID = 官方号
        return True
    if any(k in name for k in NOISE_NAMES):
        return True
    if status in BAD_STATUS:                       # 已婚/婉拒/2条没回/需付费
        return True
    t = (text or "").strip()
    if not t or t == "None":                       # 系统指引卡片
        return True
    if _is_closing(t, turns) and not keep:          # "晚安/睡了/嗯/好/哈哈" 纯收尾
        return True
    return False


def real_pending(verbose=False):
    """真实待回 = 「她最后发言」且**不是噪声**的会话。返回 [(昵称, 她的话, 时间)]"""
    nm = im.names()
    sm = _status_map()
    c = sqlite3.connect(im.IMDB, timeout=5)
    rows = c.execute("SELECT sessionId, toUserId, unReadCount, timestamp FROM session "
                     "ORDER BY timestamp DESC").fetchall()
    out = []
    for sid, uid, unread, ts in rows:
        r = c.execute("SELECT senderId, text, localTime FROM chatmsg WHERE sessionId=? "
                      "ORDER BY localTime DESC LIMIT 6", (sid,)).fetchall()
        if not r:
            continue
        # ⭐ 2026-09-26 修：末条可能是**她发的系统卡片**（mt=35/27，text 为空），
        # 旧逻辑只看 LIMIT 1 → 整段真实对话被当成"她: None"丢掉。改成取**最后一条真实文本**。
        real = [(s, (t or ""), l) for s, t, l in r if (t or "").strip()]
        if not real:
            continue
        sender, text, lt = real[0]
        if str(sender) == im.ME:                   # 最后是我说的 → 不待回
            continue
        name = nm.get(str(uid), str(uid))
        try:
            turns = c.execute("SELECT COUNT(*) FROM chatmsg WHERE sessionId=?",
                              (sid,)).fetchone()[0]
        except Exception:
            turns = None
        # ⭐ 2026-09-26 修：她「先提问 → 紧接着一个哈哈/嗯」= 在等我答，不算收尾。
        # 实测漏单：取什么名字好呢 11:22 先问"撒子叫正经的"、紧跟"哈哈哈" → 旧逻辑判收尾直接漏掉。
        keep = False
        prev_her = next(((s, t, l) for s, t, l in real[1:4] if str(s) == str(sender)), None)
        if prev_her and _looks_like_question(prev_her[1]) and (lt - prev_her[2]) <= 15 * 60 * 1000:
            keep = True
        if _is_noise(name, text, sm.get(name), turns, keep):
            if verbose:
                print(f"    · 忽略 {name}: {str(text)[:24]}")
            continue
        out.append((name, str(text), im._fmt(lt)))
    c.close()
    return out


def _load_state():
    try:
        with open(STATE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(d):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def main():
    args = sys.argv[1:]
    idle = 300
    maxt = 3600
    interval = 30
    once = "--once" in args
    for i, a in enumerate(args):
        if a == "--idle" and i + 1 < len(args):
            idle = int(args[i + 1])
        if a == "--max" and i + 1 < len(args):
            maxt = int(args[i + 1])
        if a == "--interval" and i + 1 < len(args):
            interval = int(args[i + 1])

    if "--reset" in args:
        st = {"round_start": time.time(), "last_activity": time.time(), "seen": {}}
        _save_state(st)
        print(f"[{_now()}] 新一轮计时已复位（静默上限 {idle}s / 总上限 {maxt}s）")
        if len(args) == 1:
            return 0

    if "--touch" in args:
        # 我刚发完消息 → 静默计时从此刻重算（但总时长不重置）
        st = _load_state()
        st["last_activity"] = time.time()
        _save_state(st)
        print(f"[{_now()}] 静默计时已重置（刚发过消息）")
        if len(args) == 1:
            return 0

    st = _load_state()
    if "round_start" not in st:
        st = {"round_start": time.time(), "last_activity": time.time(), "seen": {}}
        _save_state(st)
    st.setdefault("seen", {})

    first = True
    while True:
        try:
            im.pull()
        except Exception as e:
            print(f"[{_now()}] ⚠️ 拉库异常（继续重试）：{e}")
            time.sleep(interval)
            continue

        pend = real_pending(verbose=first)
        now = time.time()
        idle_sec = now - st["last_activity"]
        total_sec = now - st["round_start"]

        if pend:
            # ⭐ 只报「真新消息」：同一人的同一条消息报过一次后不再重复触发，
            #    否则上层决定"这条不必回"时会被无限唤起（2026-09-26 修）
            seen = st.setdefault("seen", {})
            fresh = [p for p in pend if seen.get(p[0]) != p[2]]
            if fresh:
                for n, t, lt in fresh:
                    seen[n] = lt
                st["last_activity"] = now             # 有人说话 → 重置静默计时
                _save_state(st)
                print(f"[{_now()}] NEW 待回 {len(fresh)} 人：")
                for n, t, lt in fresh:
                    print(f"    → {n}（{lt}）: {t[:34]}")
                print("→ 请读上下文（三步闸）后回复，回复完再跑本脚本继续等")
                return 2
            # 全是已报过的旧消息 → 视作静默，继续等

        if first:
            print(f"[{_now()}] 无待回。驻留等待中…（静默上限 {idle}s / 总上限 {maxt}s，每 {interval}s 轮询）")
        first = False

        if once:
            print(f"[{_now()}] --once：当前无真实待回，直接返回（不驻留）")
            return 0

        if total_sec >= maxt:
            print(f"[{_now()}] MAX_TIMEOUT 本轮总时长 {_fmt_hms(total_sec)} 已达上限 {_fmt_hms(maxt)} → 结束本轮")
            return 3
        if idle_sec >= idle:
            print(f"[{_now()}] IDLE_TIMEOUT 静默 {_fmt_hms(idle_sec)} 无人回复（上限 {_fmt_hms(idle)}）→ 结束本轮")
            return 4

        print(f"[{_now()}] 轮询中… 静默 {_fmt_hms(idle_sec)}/{_fmt_hms(idle)} ｜ 本轮已跑 {_fmt_hms(total_sec)}/{_fmt_hms(maxt)}")
        time.sleep(interval)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
