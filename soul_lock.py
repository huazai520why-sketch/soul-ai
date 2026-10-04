# -*- coding: utf-8 -*-
"""跨进程互斥锁 —— 防止多个自动化实例同时操作同一台模拟器（2026-09-26 并发事故后新增）

背景（真实事故）：
  2026-09-26 02:32 定时自动化被重复触发，两个实例同时给「古灵精怪的小趴菜」发消息，
  双方都往同一个输入框写文本 → 发出去的消息变成
  「行 是我看走眼 你这车没闲着行 是我看走眼 你这车没闲着」（重复两遍），
  另一条则被覆盖丢失。对方是真人，看到的是乱码式重复消息。

设计：
  · 锁分两层（2026-09-26 修：原来两层共用同一个文件，导致轮次锁一持有、
    send 就永远抢不到 → 整个轮次一条消息都发不出去）：
      ROUND_LOCK `D:\\AI\\pl\\.soul_round.lock`  —— **轮次所有权**，长期持有（单轮 30~60 分钟）
      LOCK       `D:\\AI\\pl\\.soul_io.lock`     —— **短时操作锁**，一次发送/点击用完即放
    轮次主人凭 token 放行，不需要去抢自己的轮次锁；别的实例 token 对不上 → 拒绝碰屏幕。
  · `O_CREAT|O_EXCL` 原子创建；
  · 超过 stale 秒没更新的锁视为"进程已死"，可强行抢占（避免崩溃后永久死锁）；
  · 只做本地互斥，不依赖任何外部服务。

用法：
  python soul_lock.py acquire [tag] [--stale 90] [--wait 5]   # 抢到 exit 0 / 抢不到 exit 9
  python soul_lock.py release
  python soul_lock.py status

  # 代码里：
  from soul_lock import hold
  with hold("send") as ok:
      if not ok: ...   # 没抢到锁 → 不要动屏幕
"""
import os, sys, time, json

LOCK       = r"E:\soul\.soul_io.lock"       # 短时操作锁（发送/点击），用完即放
ROUND_LOCK = r"E:\soul\.soul_round.lock"    # 轮次所有权锁，整个轮次持有
STATE      = r"E:\soul\.soul_round_state.json"   # 本轮状态（含 token）

# ⭐ 2026-09-26 21:12 用户要求：**把锁去掉**（并发防护关闭）
#   背景：锁建成于 02:32 那次"两个实例同时发、消息发重"的事故之后。
#   但 2026-09-26 20:16 又暴露另一面 —— 轮次锁与发送锁曾共用同一文件，
#   导致整个轮次一条都发不出去（已修为分层锁）。用户权衡后决定直接关掉。
#   ⚠️ 关掉后：若同时有另一个实例/会话操作同一台模拟器，**仍会串台、消息发重**。
#   想恢复：把 ENABLED 改回 True。
ENABLED = False


def _disabled():
    return not ENABLED


def _read():
    try:
        with open(LOCK, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# ── 轮次所有权（长期锁）────────────────────────────────────────
def _read_round():
    try:
        with open(ROUND_LOCK, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _read_state():
    try:
        with open(STATE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def round_acquire(stale=3900):
    """抢轮次所有权（长期持有）。返回 token；抢不到返回 "" """
    if _disabled():
        return "nolock-%d" % os.getpid()   # 锁已关闭
    while True:
        try:
            fd = os.open(ROUND_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            token = "%d-%d" % (int(time.time() * 1000), os.getpid())
            os.write(fd, json.dumps(
                {"token": token, "pid": os.getpid(), "t": time.time()}).encode())
            os.close(fd)
            return token
        except FileExistsError:
            try:
                age = time.time() - os.path.getmtime(ROUND_LOCK)
            except OSError:
                age = 0.0
            if age > stale:
                try:
                    os.remove(ROUND_LOCK)
                except OSError:
                    pass
                continue
            return ""


def round_release():
    if _disabled():
        return
    try:
        os.remove(ROUND_LOCK)
    except OSError:
        pass


def round_touch():
    try:
        os.utime(ROUND_LOCK, None)
    except OSError:
        pass


def io_allowed():
    """本进程是否被允许碰屏幕。（锁关闭时恒为 True）"""
    if _disabled():
        return True
    r = _read_round()
    if not r:
        return True
    return r.get("token") and r.get("token") == _read_state().get("token")


def is_round_owner():
    r = _read_round()
    return bool(r.get("token")) and r.get("token") == _read_state().get("token")


def acquire(tag="io", stale=90, wait=0.0):
    """原子抢锁；stale 秒未更新的锁视为失效可抢占。抢到 True，超时 False"""
    if _disabled():
        return True                      # 锁已关闭：永远放行
    deadline = time.time() + max(0.0, wait)
    while True:
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, json.dumps({"pid": os.getpid(), "tag": tag, "t": time.time()}).encode())
            os.close(fd)
            return True
        except FileExistsError:
            try:
                age = time.time() - os.path.getmtime(LOCK)
            except OSError:
                age = 0.0
            if age > stale:
                try:
                    os.remove(LOCK)
                except OSError:
                    pass
                continue
            if time.time() >= deadline:
                return False
            time.sleep(0.4)


def release():
    if _disabled():
        return
    try:
        os.remove(LOCK)
    except OSError:
        pass


class hold(object):
    """with hold("send", wait=8) as ok: ... —— 退出时自动释放

    owner_ok=True（发送类操作必须开）：
      先看轮次所有权 —— 不是本轮主人就直接拒绝（别的实例在跑）；
      是本轮主人则**直接放行**，不再去抢自己持有的短锁（2026-09-26 死锁 bug 的修法）。
    """

    def __init__(self, tag="io", stale=90, wait=0.0, owner_ok=False):
        self.tag, self.stale, self.wait = tag, stale, wait
        self.owner_ok = owner_ok
        self.ok = False
        self._skip_release = False

    def __enter__(self):
        if _disabled():
            self.ok = True
            self._skip_release = True
            return True
        if self.owner_ok:
            if not io_allowed():
                return False                 # 别的实例持有轮次 → 不许碰屏幕
            if is_round_owner():
                self.ok = True               # 我是本轮主人 → 直接放行
                self._skip_release = True    # 别去删短锁（可能正被别人/别的步骤用）
                return True
        self.ok = acquire(self.tag, self.stale, self.wait)
        return self.ok

    def __exit__(self, *a):
        if self.ok and not self._skip_release:
            release()
        return False


if __name__ == "__main__":
    import io as _io
    sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    args = sys.argv[2:]
    tag, stale, wait = "cli", 90, 0.0
    if args and not args[0].startswith("--"):
        tag = args[0]
    for i, a in enumerate(args):
        if a == "--stale" and i + 1 < len(args):
            stale = float(args[i + 1])
        if a == "--wait" and i + 1 < len(args):
            wait = float(args[i + 1])
    if cmd == "acquire":
        if acquire(tag, stale, wait):
            print("LOCK OK  tag=%s pid=%d" % (tag, os.getpid()))
            sys.exit(0)
        print("LOCK BUSY  holder=%s" % _read())
        sys.exit(9)
    elif cmd == "release":
        release()
        round_release()          # 一并释放轮次锁（避免只清短锁留下死锁）
        print("LOCK released (io + round)")
    elif cmd == "round":
        r = _read_round()
        if r:
            print("ROUND HELD", r, "age=%.1fs" % (time.time() - os.path.getmtime(ROUND_LOCK)))
            print("io_allowed =", io_allowed())
        else:
            print("ROUND FREE")
    else:
        if os.path.exists(LOCK):
            print("IO    HELD", _read(), "age=%.1fs" % (time.time() - os.path.getmtime(LOCK)))
        else:
            print("IO    FREE")
        r = _read_round()
        if r:
            print("ROUND HELD", r, "age=%.1fs" % (time.time() - os.path.getmtime(ROUND_LOCK)))
        else:
            print("ROUND FREE")
