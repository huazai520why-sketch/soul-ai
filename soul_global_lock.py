# -*- coding: utf-8 -*-
"""全局单实例锁 —— 保证「同一时间只有一个 Soul 自动化在跑」

用户提的问题（2026-09-29）：
  「别人上一个小时自动化还没结束，这个小时的自动化就不能开始」
→ 上一轮要是没跑完（消息多/卡住），下一轮又起来，就会**两个实例同时操作模拟器**：
  抢同一个输入框、重复发送、点错人（串台）。`soul_lock` 只保护"单次发送"，护不住整轮流程。

本模块提供**整轮互斥**：
  · 拿不到锁 → **本轮直接跳过**（不操作模拟器，安全退出）
  · 锁会记录 {pid, 启动时间, 当前步骤}，供排查
  · **自动过期**：持有者进程已死 或 超过 TTL(默认 55 分钟) → 视为僵尸锁，可抢占
    （TTL 略小于 1 小时的调度间隔，保证卡死不会永久堵住后面的轮次）

用法：
    from soul_global_lock import hold_round
    with hold_round() as ok:
        if not ok:
            print("上一轮还在跑 → 本轮跳过"); sys.exit(0)
        ...本轮所有操作...
"""
import os, sys, json, time, atexit

BASE = os.path.dirname(os.path.abspath(__file__))

try:
    import soul_instance as _si
except Exception:                                    # 极端兜底：模块缺失也不能崩
    _si = None

DEFAULT_TTL_MIN = 55          # 超过这个时长未释放 → 视为僵尸锁（略小于 1 小时调度间隔）
DEFAULT_HB_MIN = 15           # 心跳超过这么久没更新 → 认为上一轮已经不在了

# ⚠️ 为什么用「心跳」而不是「PID 存活」判断：
#   拿到锁的是 soul_auto.py（短命进程，几秒就退出），
#   但真正占着模拟器的是**整轮自动化**（可能跑几十分钟）。
#   用 PID 判断会立刻误判成僵尸锁 → 并发又回来了。
#   所以：谁在跑谁 touch()，靠心跳新鲜度判断"这一轮是否还活着"。


def _alive(pid):
    """进程是否还活着（仅作辅助参考，不单独作为判据）"""
    try:
        if os.name == "nt":
            import subprocess
            r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                               capture_output=True, timeout=10,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
            return str(pid) in r.stdout.decode("utf-8", "ignore")
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def lock_file():
    """轮次锁文件路径 —— **按 MuMu 实例隔离**（2026-09-30 多开改造）。

    实例 0（默认，不设 SOUL_VMINDEX）→ `.soul_auto.lock`  ← 与改造前完全一致
    实例 1                          → `.soul_auto.1.lock`

    ⚠️ 为什么必须隔离：整轮互斥的本意是「防同一实例里两个自动化抢输入框」，
       而多实例后**两个实例本就该各跑各的**。共用一把锁 → 实例1 被实例0 的
       心跳堵死，多开直接失效（且是"静默失效"：只会看到"上一轮未结束→跳过"）。
    """
    if _si is None:
        return os.path.join(BASE, ".soul_auto.lock")
    return _si.state_path(BASE, ".soul_auto.lock")


def read_lock():
    try:
        with open(lock_file(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ---- 轮次起点标记（2026-10-01 新增，给 `soul_report.py` 用）----
# 为什么不用锁文件的 ts：锁是**会被别人写**的。2026-10-01 02:20 实测，
# 我释放锁后 30 分钟，另一个 Soul 实例跑了一次 `soul_auto.py` 并在锁里写了
# 它自己的 ts → 报告拿这个 ts 当"本轮起点"，统计窗口变成 0 分钟，整份报告空掉。
# 标记文件只在**本实例自己 start_round 成功**时写，且释放锁后**不清**，
# 于是"本轮从几点开始"这件事永远可回溯，与别人无关。
def _marker_file():
    if _si is None:
        return os.path.join(BASE, ".soul_round_start")
    return _si.state_path(BASE, ".soul_round_start")


def mark_round_start(ts=None):
    """写本轮起点（ts 缺省=现在）。"""
    try:
        with open(_marker_file(), "w", encoding="utf-8") as f:
            json.dump({"ts": float(ts if ts is not None else time.time())}, f)
    except Exception:
        pass


def read_round_start():
    """本轮起点（epoch 秒）；没有标记 → None。"""
    try:
        with open(_marker_file(), encoding="utf-8") as f:
            return float((json.load(f) or {}).get("ts") or 0) or None
    except Exception:
        return None


def lock_status(ttl_min=DEFAULT_TTL_MIN, hb_min=DEFAULT_HB_MIN):
    """返回 (是否被占用, 信息dict)

    判据（任一成立即视为**空闲/僵尸**，可抢占）：
      · 心跳距今 > hb_min 分钟（上一轮早就不在了）
      · 启动至今 > ttl_min 分钟（跑太久，按超时处理）
    """
    info = read_lock()
    if not info:
        # 文件**不存在** → 正常空闲；文件**存在却解析不出来**（半写/损坏）
        # → fail-closed，保守当成"有人"，绝不因为读不出来就放行。
        if os.path.exists(lock_file()):
            return (True, {"why": "锁文件存在但无法解析（疑似半写/损坏）→ 保守视为被占用"})
        return (False, {})
    now = time.time()
    hb = info.get("heartbeat") or info.get("ts") or 0
    ts = info.get("ts") or hb
    # ⭐ 2026-09-29 修 **fail-open**（实测踩到，最危险的一类）：
    #   锁文件存在但 ts/heartbeat 全丢时，旧实现 ts=get("ts",0)=0 →
    #   total_age≈5000万年 > ttl → 返回 (False, "视为超时") → **锁静默失效**。
    #   实测：03:20:50 本机 `soul_auto.py lock` 报"空闲（可开始）"，
    #   而另一实例正在 reply:委委佗佗 —— 若当时接手就会两个实例抢输入框 → 串台。
    #   安全方向永远是 fail-closed：判不出来就当作"有人"，绝不当作"没人"。
    if not hb:
        info["why"] = "锁文件字段缺失（无 ts/heartbeat），无法判断 → 保守视为被占用"
        return (True, info)
    hb_age = (now - hb) / 60.0
    total_age = (now - ts) / 60.0
    info["hb_age_min"] = round(hb_age, 1)
    info["age_min"] = round(total_age, 1)
    if total_age > ttl_min:
        info["why"] = f"已运行 {total_age:.1f} 分钟 > 上限 {ttl_min} → 视为超时"
        return (False, info)
    if hb_age > hb_min:
        info["why"] = f"心跳 {hb_age:.1f} 分钟没更新（> {hb_min}）→ 上一轮已不在"
        return (False, info)
    info["why"] = (f"上一轮仍在进行（心跳 {hb_age:.1f} 分钟前更新，"
                   f"已跑 {total_age:.1f} 分钟，当前步骤: {info.get('step','?')}）")
    return (True, info)


def start_round(step="start", ttl_min=DEFAULT_TTL_MIN, hb_min=DEFAULT_HB_MIN):
    """开始一轮。返回 (ok, info)；ok=False 表示**上一轮还没结束，本轮必须跳过**。"""
    busy, info = lock_status(ttl_min, hb_min)
    if busy:
        return (False, info)
    if info:
        print(f"[lock] 发现可抢占的旧锁（{info.get('why')}）→ 本轮接管")
    try:
        now = time.time()
        _write_lock({"pid": os.getpid(), "ts": now, "heartbeat": now, "step": step,
                     "at": time.strftime("%Y-%m-%d %H:%M:%S")})
        mark_round_start(now)   # 轮次报告要用（见 mark_round_start 注释）
        return (True, {"pid": os.getpid()})
    except OSError as e:
        return (False, {"why": f"写锁失败 {e!r}"})


def touch(step):
    """更新心跳与当前步骤 —— **每一步操作前都应该调一次**，
    这样别的实例能看出"这轮还活着、卡在哪一步"。

    ⭐ 2026-09-29 修 fail-open（与 lock_status 的修复配套）：
      旧实现 `info = read_lock() or {}` —— 锁文件缺失/半写时 info={}，
      于是写回去的只有 {"heartbeat":..,"step":..}，**丢掉 ts 和 pid**；
      而 lock_status() 是拿 ts 算 age 的 → ts 缺失被当成"超时" → 锁形同虚设。
      典型触发路径：`end_round()` 清锁后本轮若还在继续（例如又调 soul_reply.py），
      touch() 就会重新造出一个"无 ts"的锁 → 之后所有实例都判"空闲"。
      修法：缺失的 ts/pid 当场补齐，锁始终是"有效且新鲜"的。
    """
    info = read_lock() or {}
    now = time.time()
    if not info.get("ts"):
        info["ts"] = now
    if not info.get("pid"):
        info["pid"] = os.getpid()
    info["heartbeat"] = now
    info["step"] = step
    _write_lock(info)


def _write_lock(info):
    """**原子写**锁文件：先写临时文件再 os.replace。

    2026-09-29：原来直接 open(LOCK_F,'w') 写 —— 写到一半崩/被杀，
    就会留下一个**半截 JSON**；而读到半截 JSON 的实例会走
    "文件不存在→空闲" 的分支（read_lock 返回 None），又一次 fail-open。
    原子替换保证读到的永远是"完整的新版"或"完整的旧版"。
    """
    tmp = lock_file() + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False)
        os.replace(tmp, lock_file())
    except OSError:
        try:
            os.path.exists(tmp) and os.remove(tmp)
        except OSError:
            pass


def end_round():
    """本轮正常结束 → 清锁（让下一轮可以立刻开始）"""
    info = read_lock()
    if info:
        try:
            os.remove(lock_file())
        except OSError:
            pass


class hold_round:
    """with hold_round() as ok: ...
    拿不到锁 → ok=False（**调用方必须直接退出，不要碰模拟器**）
    正常退出时会释放锁；异常退出则靠心跳超时自动回收。
    """

    def __init__(self, step="start"):
        self.step = step
        self.ok = False

    def __enter__(self):
        self.ok, info = start_round(self.step)
        if not self.ok:
            print(f"[lock] ⛔ 上一轮自动化尚未结束 → 本轮跳过。原因: {info.get('why')}")
        return self.ok

    def __exit__(self, exc_type, exc, tb):
        if self.ok:
            end_round()
        return False


if __name__ == "__main__":
    try:
        sys.stdout = __import__("io").TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    except Exception:
        pass
    if len(sys.argv) > 1 and sys.argv[1] == "release":
        info = read_lock()
        print(f"锁文件: {lock_file()}")
        print(f"当前锁: {info}")
        try:
            os.remove(lock_file()); print("已强制释放")
        except OSError:
            print("无锁文件")
    else:
        busy, info = lock_status()
        print(f"锁状态: {'被占用' if busy else '空闲'}")
        print(f"  详情: {info}")
