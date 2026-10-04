# -*- coding: utf-8 -*-
"""
soul_watcher.py —— 后台「只读」消息监控（读写分离，2026-09-29 落地）

为什么要有它
------------
用户要求「一边干活一边实时监控消息，有新消息就打断当前动作优先回复」。
但**模拟器窗口只有一个，GUI 操作是独占的**——不能在"正在输入/点击"的同时去看消息，
硬打断会留下半截草稿、甚至点错人。

拆开看只有两件事：
  · **读** = pull 手机库 → 本地 im_data.db → 算 pending。**不碰模拟器**，可独立跑
  · **操作** = 进会话/输入/发送。独占模拟器，只能串行

所以：**把「读」抽出来交给常驻后台进程**；主流程在动作间隙只跑 `check`（读一个 flag 文件，
毫秒级，不 pull、不动模拟器）。这就是读写分离。

⚠️ 与旧 `soul_watch.py` 的区别（旧文件保留但已废弃，勿用）：
   旧版走 adb（`am start`）+ `ensure_chat_page()` 会**抢模拟器**，与主流程冲突，
   且违反「2026-09-29 起全程不用 adb」的铁律。本文件**绝不 import soul、绝不 tap**。

用法
----
  python soul_watcher.py start [-i 20]   # 后台常驻，默认 20 秒一轮
  python soul_watcher.py once            # 只跑一次（调试）
  python soul_watcher.py check           # 毫秒级读 flag：有没有新消息
  python soul_watcher.py check --clear   # 读完并消费 flag
  python soul_watcher.py status          # 后台在不在跑 + 最近日志
  python soul_watcher.py stop            # 通知后台退出（下一轮生效）

设计要点
--------
1. **绝不动模拟器**：只 import soul_im（pull + 读库）。
2. **pull 撞车可容忍**：并发 pull 会报"正式库被其他进程占用"（已知），
   后果只是本次沿用旧数据，不会损坏 → 捕获并**显式打日志**，不静默吞。
3. **只报新增**：与上一轮快照比对，只有「新出现的待回项」或「她最后消息变了」才写 flag，
   避免每 20 秒把同一批待回重复报一遍。
4. **不许假装正常**：watcher 挂了不影响主流程（check 读不到 flag = 无新消息），
   但 `status` 会如实报"没在跑"。

flag: D:\\AI\\pl\\.soul_newmsg.json   日志: D:\\AI\\pl\\logs\\soul_watcher.log
"""
import os
import sys
import json
import time
import datetime

# ⚠️ 2026-09-30：守护进程必须能在**任何**控制台编码下存活。
#    实测：无控制台或用 GBK 控制台启动时，_log() 里的 print 遇到 emoji 直接抛
#    UnicodeEncodeError，把 watcher 打死（而它本该 7×24 常驻）。
#    errors="replace" 保证最坏情况只是日志里的 emoji 变成 ?，进程不受影响。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE = os.path.dirname(os.path.abspath(__file__))

# ---- 多实例隔离（2026-09-30 双实例）----
# 这 5 个文件原来都是全局单份 → 两个账号的 watcher 会互相污染：
#   · FLAG_F  两份新消息混在一个 flag 里（实例0/1 的新消息分不清谁是谁）
#   · LOCK_F  实例1 的 watcher 会因实例0 已持锁而**起不来**
#   · LOG_F   两份日志交错，事后无法判归属（今天就吃过这个亏）
# 约定：实例0 仍返回原文件名（与改造前逐字符相同），实例 N>0 加 `.N`。
try:
    from soul_instance import state_path as _sp
except Exception:
    def _sp(base, name):
        return os.path.join(base, name)

FLAG_F = _sp(BASE, ".soul_newmsg.json")
STOP_F = _sp(BASE, ".soul_watcher.stop")
LOG_D = os.path.join(BASE, "logs")
LOG_F = _sp(LOG_D, "soul_watcher.log")
PID_F = _sp(BASE, ".soul_watcher.pid")
LOCK_F = _sp(BASE, ".soul_watcher.lock")   # 原子单实例锁（msvcrt）

DEFAULT_INTERVAL = 20
# 锁文件心跳超时：活着的 watcher 每轮(15s)都会 touch，超过这个时间没动 = 僵尸锁。
# 比 interval 宽很多，避免一次慢 pull 就被误判成死。
LOCK_STALE_SEC = 180


def _acquire_singleton():
    """原子单实例锁（文件级 O_CREAT|O_EXCL）。拿到返回 True，已有实例返回 False。

    踩过的三个坑，别再回头：
    1. 「读 pid 文件 + tasklist 判断」——检查与写入之间有窗口，计划任务和手动触发
       撞一起时会**同时通过**（2026-09-29 18:39 实测堆出 2 个）。
    2. msvcrt.locking——对 0 字节文件加锁不保证跨进程互斥（18:41 实测照样双开）。
    3. Windows 命名互斥量——Global\\ 需要特权、Local\\ 又不跨会话，
       而计划任务进程与手动起的进程**可能不在同一会话** → 混用两个命名空间
       会让判定自相矛盾（18:50 实测：Global 报已存在、Local 却能拿到）。
    ⇒ 最终用 O_CREAT|O_EXCL：文件系统保证原子，只有第一个进程能创建成功；
       持锁进程死了则由下一个进程清理僵尸锁后重试（自愈）。
    """
    for _ in range(3):
        try:
            fd = os.open(LOCK_F, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            # ⚠️ 判"是不是僵尸锁"不能只看 pid 存不存在：
            # Windows 会复用 pid，一个**完全无关**的进程可能正好占用了旧的 20644
            # → _alive() 返回 True → 误判"有人跑" → watcher 永远起不来（19:02 实测）。
            # 所以叠加**锁文件心跳**：活着的 watcher 每轮循环都会 touch 锁文件，
            # 复用的 pid 不会去 touch → mtime 过期即判僵尸。
            try:
                old = int((open(LOCK_F, encoding="utf-8").read().strip() or 0))
                age = time.time() - os.path.getmtime(LOCK_F)
            except Exception:
                old, age = 0, 1e9
            if old and _alive(old) and age < LOCK_STALE_SEC:
                return False                 # 真有人在跑（pid 活着 + 心跳新鲜）
            try:
                os.remove(LOCK_F)            # 僵尸锁：清掉重试
            except Exception:
                return False
        except Exception:
            return False
    return False


def _touch_lock():
    """每轮循环刷新锁文件 mtime —— 这是"我还活着"的心跳。"""
    try:
        with open(LOCK_F, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass


def _release_singleton():
    try:
        os.remove(LOCK_F)
    except Exception:
        pass


def _still_owner():
    """运行期自检：锁里记的 pid 还是不是自己。

    启动期的锁挡不住所有竞态（僵尸锁清理、pid 复用都可能放第二个进来），
    那就让**运行期**来收敛：每轮循环都确认自己仍是持锁者，不是就退出。
    实测双开的后果是两边同时 pull 把 stage 冲掉 → 日志停更 = 监控静默失效。
    """
    try:
        return int((open(LOCK_F, encoding="utf-8").read().strip() or 0)) == os.getpid()
    except Exception:
        return False


def _isolate():
    """给自己的 pull 一条独立通道，避免和主流程抢同一份 stage/中转目录。

    2026-09-29 实测：watcher 与主流程同时 pull → 两边互删对方 stage 里的文件
    → 双双报 "stage 校验不过（本地库不存在）"，上层只看到"无新消息"（静默失败）。
    现在 soul_im.pull 支持 SOUL_PULL_TAG，各走各的目录。

    ⚠️ 2026-09-29 19:00 追加：tag 必须带上 pid。
    写死成 "watch" 时，**两个 watcher 实例会共用同一条通道** → 互删对方 stage
    → pull 双双失败/卡死；卡死在 once() 里就走不到运行期自检，于是永远收敛不了。
    按 pid 分开后，即便启动期漏进来第二个，它也能跑完一轮 → 自检发现不是持锁者 → 自杀。
    """
    os.environ.setdefault("SOUL_PULL_TAG", f"watch_{os.getpid()}")


def _log(msg):
    os.makedirs(LOG_D, exist_ok=True)
    ts = datetime.datetime.now().strftime("%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    try:
        with open(LOG_F, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    # ⚠️ 2026-09-30 实测：无控制台/GBK 控制台下，print 带 emoji 会抛
    #    UnicodeEncodeError('gbk' codec can't encode character '\U0001f680')
    #    —— 而这是**守护进程**，一行日志把整个 watcher 打死（实例1 就是这么没的，
    #    日志停在 "🚀 watcher 启动…" 之前，锁文件留在原地成为僵尸锁）。
    #    日志写不出去最多是少一行记录，**绝不能因此终止监控**。
    try:
        print(line, flush=True)
    except Exception:
        pass


def _snapshot():
    """{会话名: (她最后一条时间戳ms, 文本)} —— 全靠数据库，不读屏。

    soul_im.pending() 返回 [(ts, 昵称, 未读, 文本, ts2), ...]
    """
    _isolate()
    import soul_im as im
    out = {}
    for item in im.pending():
        try:
            ts, name, unread, txt, ts2 = item
        except Exception:
            continue
        if not name:
            continue
        out[str(name)] = (int(ts2 or ts or 0), str(txt or ""))
    return out


def _diff(prev, cur):
    new = {}
    for name, (ts, txt) in cur.items():
        p = prev.get(name)
        if p is None:
            new[name] = {"ts": ts, "text": txt, "why": "新出现的待回"}
        elif p[0] != ts:
            new[name] = {"ts": ts, "text": txt, "why": "她又发了新的"}
    return new


def _load_flag_items():
    """读回 flag 里**还没被消费**的项（供累积合并用）"""
    if not os.path.exists(FLAG_F):
        return []
    try:
        with open(FLAG_F, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d.get("items", []) or []
    except Exception:
        return []


def _write_flag(new):
    now = int(time.time() * 1000)
    items = [{"name": k, **v} for k, v in new.items()]
    data = {
        "checked_at": now,
        "checked_str": datetime.datetime.fromtimestamp(now / 1000).strftime("%H:%M:%S"),
        "count": len(items),
        "items": items,
    }
    tmp = FLAG_F + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, FLAG_F)


def _clear_flag():
    if os.path.exists(FLAG_F):
        try:
            os.remove(FLAG_F)
        except Exception:
            pass


def once(prev, do_pull=True):
    if do_pull:
        import soul_im as im
        t0 = time.time()
        try:
            n = im.pull(retry=2)
            _log(f"pull ok（{n} 个文件，{time.time()-t0:.1f}s）")
        except Exception as e:
            # 并发 pull 撞车是已知且可容忍的（沿用旧数据），但必须显式报出来
            _log(f"⚠️ pull 失败（可能主流程正在 pull，本次沿用旧数据）：{type(e).__name__}: {e}")
    try:
        cur = _snapshot()
    except Exception as e:
        _log(f"⚠️ 本次跳过（pending 异常）：{type(e).__name__}: {e}")
        return prev
    new = _diff(prev, cur)
    # ⭐ 2026-09-29 修**静默失败**（用户当场发现：pending 有 6 条，check 却说"无新消息"）：
    #   旧实现检测到新增才写 flag，**下一轮（20s 后）这批已进基线 → 判"无新增" → 把 flag 清掉**。
    #   结果 flag 只在写完后的 20 秒窗口内存在，主流程去 check 时早就没了 —— 监控形同虚设。
    #   现在改为**累积**：新项合并进来，只有主流程 `check --clear` 消费后才清；
    #   主流程回完消息后该项不再待回 → 自动从 flag 消失。
    keep = [it for it in _load_flag_items()
            if it.get("name") in cur and cur.get(it.get("name"), (0, ""))[0] == it.get("ts")]
    merged = {it["name"]: it for it in keep}
    for k, v in new.items():
        merged[k] = {"name": k, **v}
    if merged:
        _write_flag(merged)
        for k, v in new.items():
            _log(f"🔔 新消息 → {k}：{v['text'][:30]}（{v['why']}）")
    else:
        _clear_flag()
    return cur


def _alive(pid):
    """进程存活判断。

    ⚠️ 别用 tasklist：它要 fork 一个子进程再解析文本，慢且**在目标进程刚起来时
    可能查不到** → 被误判为"已死" → 上层删掉别人的锁 → 双开
    （2026-09-29 18:52 实测就是这么漏的）。
    OpenProcess 是同步系统调用，快且准。
    """
    if not pid:
        return False
    try:
        import ctypes
        k32 = ctypes.WinDLL("kernel32")
        k32.OpenProcess.restype = ctypes.c_void_p
        h = k32.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
        if h:
            k32.CloseHandle(ctypes.c_void_p(h))
            return True
        return False
    except Exception:
        pass
    try:                                   # 兜底
        import subprocess
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
        return str(pid) in (out.stdout or b"").decode("gbk", "ignore")
    except Exception:
        return False


def _log_fresh(sec=90):
    """日志心跳：活着的 watcher 每 15s 写一次日志，所以「日志新鲜」= 有活的实例。

    为什么需要它：仅靠文件锁挡不住"启动阶段卡死"的进程（实测有实例卡在 _snapshot()
    里，从不写日志、也不退出，自检够不着它）→ 计划任务每 5 分钟再起一个 → 僵死进程累积。
    放到 cmd_start **最前面**：发现已有活的就直接返回，不做任何可能卡住的动作。
    """
    try:
        return (time.time() - os.path.getmtime(LOG_F)) < sec
    except Exception:
        return False


def cmd_start(interval=DEFAULT_INTERVAL):
    # ⭐ 单实例保护（原子）：计划任务每 5 分钟会尝试拉起一次，已在跑就直接退出。
    #   没有这道闸会堆出一堆重复进程，一起 pull 又会把 stage 冲掉。
    if _log_fresh():
        print("已有 watcher 在跑（日志心跳新鲜）→ 本次不重复启动")
        return 0
    if not _acquire_singleton():
        print("已在运行（单实例锁占用）→ 本次不重复启动")
        return 0
    # ⚠️ 这里**不要**再用 .soul_watcher.pid 做第二道判据：
    #  pid 文件可能是上一轮被强杀留下的僵尸（实测 .soul_watcher.pid=11508，
    #  而 11508 早就不在了，甚至可能被系统复用给无关进程）
    #  → 新进程读到它就被误判"已在运行"，永远起不来（2026-09-29 18:55）。
    #  原子锁才是权威：拿得到锁 = 没有活着的 watcher。pid 文件只作记录用。
    if os.path.exists(STOP_F):
        os.remove(STOP_F)
    base = {}
    try:
        base = _snapshot()
        _log(f"基线建立：当前已有 {len(base)} 个待回（不计为新消息）")
    except Exception as e:
        _log(f"⚠️ 基线建立失败：{type(e).__name__}: {e}")
    with open(PID_F, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    _log(f"🚀 watcher 启动 pid={os.getpid()} 间隔={interval}s（只读库，不碰模拟器）")
    prev = base
    while True:
        # ⚠️ 顺序不能反：必须先判 owner 再 touch。
        # 反过来写的话，每个进程都先把自己写进锁、再检查 → 两边都"查到自己"
        # → 自检永远通过 → 双开永远收敛不了（2026-09-29 19:05 实测）。
        if not _still_owner():
            _log(f"⚠️ 检测到另一个实例接管了锁（本进程 pid={os.getpid()}）→ 自我退出")
            for p in (PID_F,):
                try:
                    os.remove(p)
                except Exception:
                    pass
            return 0
        _touch_lock()                      # 确认是 owner 后才刷新心跳
        if os.path.exists(STOP_F):
            _log("收到 stop 信号，退出")
            for p in (STOP_F, PID_F, LOCK_F):
                try:
                    os.remove(p)
                except Exception:
                    pass
            return 0
        try:
            prev = once(prev, do_pull=True)
        except Exception as e:
            _log(f"⚠️ 循环异常（继续下一轮）：{type(e).__name__}: {e}")
        time.sleep(interval)


def cmd_check(clear=False, show_all=False):
    # ⭐ `--all`：直接列出**当前全部待回**（不依赖 flag 基线）。
    #   为什么需要：watcher 每次启动会建一次基线，基线里已有的待回**不会**再当"新消息"报
    #   —— 重启后如果只看 flag，会漏掉重启前就在等的那批。主流程用 `--all` 就不用担心这个。
    if show_all:
        _isolate()
        import soul_im as im
        try:
            rows = im.pending()
        except Exception as e:
            print(f"⚠️ 读待回失败：{type(e).__name__}: {e}")
            return 0
        if not rows:
            print("当前 0 条待回")
            return 0
        print(f"📬 当前 {len(rows)} 条待回：")
        for item in rows:
            try:
                ts, name, unread, txt, ts2 = item
            except Exception:
                continue
            print(f"   {name}：{str(txt)[:40]}")
        return 1
    if not os.path.exists(FLAG_F):
        print("无新消息（无 flag）")
        return 0
    try:
        with open(FLAG_F, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception as e:
        print(f"⚠️ flag 读取失败：{e}")
        return 0
    n = d.get("count", 0)
    if not n:
        print("无新消息")
        if clear:
            _clear_flag()
        return 0
    print(f"🔔 {n} 条新消息（检查于 {d.get('checked_str')}）：")
    for it in d["items"]:
        print(f"   {it['name']}：{it['text'][:40]}")
    if clear:
        _clear_flag()
        print("（flag 已消费）")
    return 1


def cmd_status():
    if not os.path.exists(PID_F):
        print("⭕ watcher 没在跑（无 pid 文件）")
        return 1
    try:
        pid = int(open(PID_F, encoding="utf-8").read().strip())
    except Exception:
        print("⭕ pid 文件损坏")
        return 1
    alive = False
    try:
        import subprocess
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
        txt = (out.stdout or b"").decode("gbk", "ignore")
        alive = str(pid) in txt
    except Exception as e:
        print(f"⚠️ 无法确认进程状态：{type(e).__name__}: {e}")
    print(f"{'🟢 watcher 在跑' if alive else '⭕ watcher 已停止'} pid={pid}")
    if os.path.exists(LOG_F):
        try:
            lines = open(LOG_F, encoding="utf-8").read().strip().split("\n")
            print("最近日志：")
            for l in lines[-5:]:
                print("   " + l)
        except Exception:
            pass
    return 0 if alive else 1


def main():
    a = sys.argv[1:] or ["once"]
    cmd = a[0]
    if cmd == "start":
        iv = DEFAULT_INTERVAL
        if "-i" in a:
            try:
                iv = int(a[a.index("-i") + 1])
            except Exception:
                pass
        return cmd_start(iv)
    if cmd == "once":
        once({}, do_pull=True)
        return 0
    if cmd == "check":
        return cmd_check(clear=("--clear" in a), show_all=("--all" in a))
    if cmd == "stop":
        with open(STOP_F, "w", encoding="utf-8") as f:
            f.write("stop")
        print("已发送 stop 信号（watcher 会在下一轮退出）")
        return 0
    if cmd == "status":
        return cmd_status()
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
