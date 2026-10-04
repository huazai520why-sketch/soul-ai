# -*- coding: utf-8 -*-
"""Soul 守护看护 —— daemon 挂了就自动拉起（保证 24h 不断）
跑法（副机 Session 1，pythonw 无窗）：pythonw E:\\soul\\_daemon_guard.py --vm N

2026-10-03 单例修复：guard 原来没有互斥，被重复拉起时会越积越多，
每个 guard 又各自拉起一个 daemon → 多 guard + 多 daemon 互相覆盖 state
（实测 last_match 被写回 0、匹配永远空转）。
用 msvcrt 文件锁：**原子**获取、进程退出自动释放，免疫并发启动竞态。

2026-10-04 昼夜时段调度（用户方案：“白天跑一个号 晚上跑一个号”）：
  硬件带不动双实例并发 → 时分复用。本 guard 只管**自己这个实例**：
  · 时段内：MuMuNxMain(session1) 在 → 本实例已启动 → daemon 在跑
  · 时段外：停本实例 daemon + shutdown 本实例模拟器，把资源让给对方
  两个 guard 各守一段（VM0 守白天、VM1 守晚上），互补即全天在线。
  ⚠ 模拟器必须在 **session 1** 启动（SSH/session 0 里 MuMuNxMain 15 秒自杀）。
    本脚本由计划任务以“交互方式”拉起 → 本身就在 session 1，Popen 即可。
  时段可用环境变量覆盖：SOUL_SLOT_DAY_START/DAY_END/DAY_VM/NIGHT_VM
"""
import os, sys, io, time, subprocess

# ⭐ 2026-10-04 双开：支持 `--vm N` 显式指定实例号（比用 .cmd 包环境变量干净）。
#   不带 --vm 时行为与原来**完全一致**（读环境变量、兜底 "0"）→ 账号1 守护不受影响。
VM = os.environ.get("SOUL_VMINDEX", "0")
if "--vm" in sys.argv:
    try:
        VM = str(sys.argv[sys.argv.index("--vm") + 1]) or VM
    except Exception:
        pass
BASE = r"E:\soul"
OUTD = os.path.join(BASE, "_uimap", "daemon")
try:
    os.makedirs(OUTD, exist_ok=True)
except Exception:
    pass
PIDF = os.path.join(OUTD, "daemon.%s.pid" % VM)
LOCKG = os.path.join(OUTD, "guard.%s.lock" % VM)
GLOG = os.path.join(OUTD, "guard.%s.log" % VM)
PYW = r"C:\Users\JIAN\.workbuddy\binaries\python\envs\soulocr\Scripts\pythonw.exe"
DAEMON = os.path.join(BASE, "soul_daemon.py")
DETACHED = 0x00000008
NO_WINDOW = 0x08000000

# ── MuMu 侧（2026-10-04 新增：guard 负责模拟器生命周期）────────────────────
CLI = r"D:\MuMuPlayer\nx_main\mumu-cli.exe"
NXMAIN = r"D:\MuMuPlayer\nx_main\MuMuNxMain.exe"

# ── 昼夜时段（用户 2026-10-04 方案；可用环境变量覆盖，改完重启 guard 生效）──
SLOT_DAY_START = int(os.environ.get("SOUL_SLOT_DAY_START", "8"))      # 白天起（含）
SLOT_DAY_END = int(os.environ.get("SOUL_SLOT_DAY_END", "20"))         # 白天止（不含）
SLOT_DAY_VM = str(os.environ.get("SOUL_SLOT_DAY_VM", "0"))            # 白天跑哪个实例
SLOT_NIGHT_VM = str(os.environ.get("SOUL_SLOT_NIGHT_VM", "1"))        # 晚上跑哪个实例

_LOCKFH = None


def log(s):
    try:
        with io.open(GLOG, "a", encoding="utf-8", errors="replace") as f:
            f.write("[%s] %s\n" % (time.strftime("%m-%d %H:%M:%S"), s))
    except Exception:
        pass


def _run(args, timeout=30):
    """跑一条命令并吞掉输出（绝不抛异常打断守护循环）。"""
    try:
        r = subprocess.run(args, capture_output=True, timeout=timeout,
                           creationflags=NO_WINDOW)
        return r.stdout.decode("utf-8", "ignore")
    except Exception:
        return ""


def alive(pid):
    if not pid:
        return False
    try:
        r = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/FO", "CSV", "/NH"],
                           capture_output=True, timeout=10,
                           creationflags=NO_WINDOW)
        return str(pid) in r.stdout.decode("utf-8", "ignore")
    except Exception:
        return False


def launch():
    env = dict(os.environ)
    env["SOUL_VMINDEX"] = VM
    try:
        subprocess.Popen([PYW, DAEMON], env=env,
                         creationflags=DETACHED | NO_WINDOW,
                         close_fds=True)
        return True
    except Exception as e:
        log("拉起失败: %r" % (e,))
        return False


def kill(pid):
    if pid:
        _run(["taskkill", "/PID", str(pid), "/T", "/F"], 20)


def read_pid():
    if os.path.exists(PIDF):
        try:
            return int(io.open(PIDF, encoding="utf-8").read().strip() or 0)
        except Exception:
            return 0
    return 0


# ── 模拟器 ────────────────────────────────────────────────────────────────
def proc_alive(img):
    out = _run(["tasklist", "/FI", "IMAGENAME eq %s" % img, "/FO", "CSV", "/NH"], 15)
    return img.lower() in out.lower()


def ensure_nxmain():
    """MuMuNxMain（管理器/Hypervisor 前端）必须在 session 1 存活，实例才有渲染窗口。"""
    if proc_alive("MuMuNxMain.exe"):
        return True
    log("MuMuNxMain 不在 → 拉起（本进程须在 session1）")
    try:
        subprocess.Popen([NXMAIN], creationflags=DETACHED | NO_WINDOW, close_fds=True)
    except Exception as e:
        log("拉 MuMuNxMain 失败: %r" % (e,))
        return False
    for _ in range(20):
        time.sleep(3)
        if proc_alive("MuMuNxMain.exe"):
            log("MuMuNxMain 已就绪，再等 8s 让服务起全")
            time.sleep(8)
            return True
    log("MuMuNxMain 拉起超时（60s）")
    return False


def vm_started(vm):
    return '"is_process_started": true' in _run([CLI, "info", "-v", vm], 25)


def vm_launch(vm):
    log("拉起实例 vm=%s（control launch）" % vm)
    _run([CLI, "control", "-v", vm, "launch"], 90)
    for i in range(45):
        time.sleep(2)
        if vm_started(vm):
            log("实例 vm=%s 已启动（约 %ds）" % (vm, (i + 1) * 2))
            return True
    log("实例 vm=%s 拉起超时（90s）" % vm)
    return False


def vm_shutdown(vm):
    log("关停实例 vm=%s（control shutdown，让资源给另一个号）" % vm)
    _run([CLI, "control", "-v", vm, "shutdown"], 60)


# ── 时段 ──────────────────────────────────────────────────────────────────
def in_slot(hour, vm):
    """本实例当前是否轮到它跑。白天窗口 [START, END)，其余算晚上。"""
    day = SLOT_DAY_START <= hour < SLOT_DAY_END
    return (day and vm == SLOT_DAY_VM) or ((not day) and vm == SLOT_NIGHT_VM)


def _drop_round_lock():
    """时段收工时清掉本实例的轮次锁：否则残留锁会让下一时段开局空转一轮
    （锁 TTL 55min，上一天残留的锁甚至能卡到次日）。"""
    name = ".soul_auto.lock" if VM == "0" else ".soul_auto.%s.lock" % VM
    p = os.path.join(BASE, name)
    try:
        if os.path.exists(p):
            os.remove(p)
            log("已清轮次锁 %s" % name)
    except OSError:
        pass


def _acquire():
    """文件锁单例：拿到返回 True；已被别的 guard 持有返回 False"""
    global _LOCKFH
    try:
        import msvcrt
        fh = open(LOCKG, "a+")
        fh.write("x")
        fh.flush()
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        fh.seek(0)
        fh.truncate()
        fh.write("%d" % os.getpid())
        fh.flush()
        _LOCKFH = fh          # 保持引用 → 进程存活期间一直持锁
        return True
    except Exception:
        return False


def main():
    if not _acquire():
        log("已有 guard 在跑 → 本进程退出")
        os._exit(0)      # ⭐ 2026-10-04 同上：return 会残留空壳，必须硬退出
    log("=== GUARD START vm=%s pid=%d ｜ 白天 %02d:00~%02d:00=VM%s ｜ 其余=VM%s ==="
        % (VM, os.getpid(), SLOT_DAY_START, SLOT_DAY_END, SLOT_DAY_VM, SLOT_NIGHT_VM))
    last_on = None
    while True:
        try:
            on = in_slot(time.localtime().tm_hour, VM)
            if on != last_on:
                log("⏰ 时段%s（%s）本实例 vm=%s" % ("开始" if on else "结束",
                                                  time.strftime("%H:%M"), VM))
                if not on:
                    _drop_round_lock()
                last_on = on

            if on:
                if not ensure_nxmain():
                    time.sleep(30)
                    continue
                if not vm_started(VM):
                    if not vm_launch(VM):
                        time.sleep(60)
                        continue
                pid = read_pid()
                if not alive(pid):
                    log("daemon 不在（pid=%s）→ 拉起" % pid)
                    launch()
                    time.sleep(20)
            else:
                pid = read_pid()
                if alive(pid):
                    log("时段外 → 停 daemon pid=%s" % pid)
                    kill(pid)
                    time.sleep(3)
                    try:
                        os.remove(PIDF)
                    except OSError:
                        pass
                if vm_started(VM):
                    vm_shutdown(VM)
                time.sleep(120)
                continue
        except Exception as e:
            log("guard 异常: %r" % (e,))
        time.sleep(60)


if __name__ == "__main__":
    main()
