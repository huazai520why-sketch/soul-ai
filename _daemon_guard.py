# -*- coding: utf-8 -*-
"""Soul 守护看护 —— daemon 挂了就自动拉起（保证 24h 不断）

跑法（Session 1，pythonw 无窗）：pythonw E:\\soul\\_daemon_guard.py

⛔ 2026-10-05：**多实例 / 昼夜换号 / 分身 全部废弃**。
   用户口径：只用一个 Soul App，多账号靠**在 App 内手动切号**
   （账号跟随见 soul_daemon._account_gate_ok）。故本 guard 为**单实例常驻**：
   · VM 恒为 "0"，不再支持 `--vm N` / SOUL_VMINDEX / SOUL_SLOT_* 环境变量
   · 删除昼夜时段调度（SLOT_* / in_slot）、实例停用开关（DISABLED_VMS）、
     "时段外关机"（vm_shutdown）—— 模拟器与 daemon 全天候常驻
   · 只做一件事：确保 MuMuNxMain 在 → 实例在 → daemon 在

历史（保留作参考）：
2026-10-03 单例修复：guard 原来没有互斥，被重复拉起时会越积越多，
每个 guard 又各自拉起一个 daemon → 多 guard + 多 daemon 互相覆盖 state
（实测 last_match 被写回 0、匹配永远空转）→ 命名内核互斥(CreateMutexW)+文件锁双重保险。

⚠ 模拟器必须在 **session 1** 启动（SSH/session 0 里 MuMuNxMain 15 秒自杀）。
  本脚本由计划任务以“交互方式”拉起 → 本身就在 session 1，Popen 即可。
"""
import os, sys, io, time, subprocess

# ⛔ 2026-10-05：**多实例已废弃** —— 用户放弃「多实例 / Soul 应用内分身」，
#   改为单实例 + 在 Soul App 内手动切号（账号跟随见 soul_daemon._account_gate_ok）。
#   故 VM 恒为 "0"，不再支持 `--vm N` / SOUL_VMINDEX。
VM = "0"
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

# ⛔ 2026-10-05：昼夜换号（SLOT_*）已删除 —— 单实例常驻，模拟器不再按时段开关机。

_LOCKFH = None
_MUTEXH = None


def log(s):
    try:
        with io.open(GLOG, "a", encoding="utf-8", errors="replace") as f:
            f.write("[%s] %s\n" % (time.strftime("%m-%d %H:%M:%S"), s))
    except Exception:
        pass


def _run(args, timeout=30):
    """跑一条命令并吞掉输出（绝不抛异常打断守护循环）。

    ⭐ 2026-10-05 修复：原 subprocess.run(timeout) 在 mumu-cli 桥挂死时
    （本机实测 NemuShell 桥坏死，mumu-cli 进程僵住不响应）kill() 也会卡住，
    guard 整循环冻结 → 不再拉起 daemon（守护形同虚设）。
    改为 Popen+communicate(timeout)+taskkill /T 强杀进程树，超时必返回。
    """
    try:
        p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             creationflags=NO_WINDOW)
    except Exception:
        return ""
    try:
        out, err = p.communicate(timeout=timeout)
        return ((out or b"").decode("utf-8", "ignore")
                + (err or b"").decode("utf-8", "ignore")).strip()
    except subprocess.TimeoutExpired:
        try:
            subprocess.run(["taskkill", "/PID", str(p.pid), "/F", "/T"],
                           capture_output=True, timeout=5, creationflags=NO_WINDOW)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
        try:
            p.wait(timeout=3)
        except Exception:
            pass
        return ""
    except Exception:
        try:
            p.kill()
        except Exception:
            pass
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
    try:
        subprocess.Popen([PYW, DAEMON], env=dict(os.environ),
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


def _acquire():
    """单例双重保险：先内核命名互斥锁（强互斥、无文件锁竞态），再文件锁。
    ⭐ 2026-10-05 修复：msvcrt 文件锁在本机实测不可靠（同文件可被多进程同时"a+"打开，
    卡死/双启动都出现过）→ 前置 CreateMutexW，跨进程由内核保证唯一。"""
    global _MUTEXH
    try:
        import ctypes
        # ⭐ 2026-10-05 晚 修复「双 guard → 双 daemon」：
        #   旧写法 `ctypes.windll.kernel32.CreateMutexW(...)` 之后再调
        #   `ctypes.windll.kernel32.GetLastError()` —— ctypes 默认不保存 last-error，
        #   两次调用之间 Python 自身的 Win32 调用会把错误码冲掉，于是
        #   ERROR_ALREADY_EXISTS(183) 经常读不到 → 第二、第三个 guard 都以为自己是唯一
        #   → 各拉起一个 daemon → 两个进程同时写同一份 wshot.png（读者拿到空帧
        #   OSError('image file is truncated')）+ 同时驱动同一台设备互相打架。
        #   正确姿势：use_last_error=True + ctypes.get_last_error()。
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _MUTEXH = k32.CreateMutexW(None, False, "SoulGuard_%s" % VM)
        if not _MUTEXH:
            return False
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            k32.CloseHandle(_MUTEXH)
            _MUTEXH = None
            log("已有 guard（内核互斥）在跑 → 本进程退出")
            return False
    except Exception as e:
        log("内核互斥异常 %r → 退化文件锁" % (e,))
        _MUTEXH = None
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
    """单实例常驻守护：确保 MuMuNxMain 在 → 实例在 → daemon 在。

    ⛔ 2026-10-05：删除昼夜换号 / 实例停用（DISABLED_VMS）/ 时段外关机 ——
    模拟器与 daemon 全天候常驻，不再有任何多实例调度。
    """
    if not _acquire():
        log("已有 guard 在跑 → 本进程退出")
        os._exit(0)      # ⭐ 2026-10-04：return 会残留空壳，必须硬退出
    log("=== GUARD START pid=%d（单实例常驻模式）===" % os.getpid())
    while True:
        try:
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
        except Exception as e:
            log("guard 异常: %r" % (e,))
        time.sleep(60)


if __name__ == "__main__":
    main()
