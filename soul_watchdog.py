# -*- coding: utf-8 -*-
"""Soul 常驻监控「看门狗」—— 保证 `soul_round.py watch` 7×24 不死

为什么需要它：
  watch 进程可能因 adb 掉线、模拟器重启、系统休眠、会话结束等原因死掉。
  本脚本只做一件事：**发现 watch 死了就把它拉起来**，自己不碰模拟器。

用法：
  python soul_watchdog.py            # 检查一次，死了就拉起（适合被计划任务每 5 分钟调一次）
  python soul_watchdog.py --once     # 同上（默认即此）
  python soul_watchdog.py --loop 300 # 常驻，每 300 秒自检一次

日志：D:\\AI\\pl\\.soul_watchdog.log
"""
import os
import sys
import io
import time
import subprocess
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PY = r"C:\Users\JIAN\.workbuddy\binaries\python\versions\3.13.12\python.exe"
ROUND = r"E:\soul\soul_round.py"
BEAT = r"E:\soul\.soul_watch_beat"
LOG = r"E:\soul\.soul_watchdog.log"
MAX_AGE = 180          # 心跳超过 180s 没更新 = 认为 watch 已死

# ⭐ 无黑窗（2026-09-30）：所有子进程一律隐藏控制台窗口。
#   wmic/taskkill/ldconsole/python.exe 都是控制台程序，不带此标志每次调用都会闪黑窗。
_NW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
_DN = subprocess.DEVNULL

ADB = r"D:\leidian\LDPlayer14\adb.exe"
LDCONSOLE = r"D:\leidian\LDPlayer14\ldconsole.exe"
ROUND_STATE = r"E:\soul\.soul_round_state.json"
REBOOT_GAP = 600       # 两次"重启模拟器"最小间隔（秒），避免打转
_reboot_at = 0


# ─────────────────────────────────────────────────────────────
# ⭐ 模拟器健康检查（2026-09-27 新增）
#   监控再稳，模拟器崩了也是白搭 —— 今晚它自己掉过 3 次。
# ─────────────────────────────────────────────────────────────
def _run(cmd, timeout=30):
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=_NW)
        return (r.stdout or b"").decode("utf-8", "ignore")
    except subprocess.TimeoutExpired:
        return ""
    except Exception:
        return ""


def adb_devices():
    """当前在线的 adb 设备列表"""
    devs = []
    for line in _run([ADB, "devices"]).splitlines()[1:]:
        p = line.split()
        if len(p) >= 2 and p[1] == "device":
            devs.append(p[0])
    return devs


def proc_running(name):
    txt = _run(["tasklist", "/FI", f"IMAGENAME eq {name}"])
    return name.lower() in txt.lower()


def round_active():
    """有没有自动化轮次正在跑 —— **正在跑就绝不能重启模拟器**（会打断聊天）"""
    try:
        import json, time
        st = json.load(open(ROUND_STATE, encoding="utf-8"))
        t0 = st.get("start_ts")
        if not t0:
            return False
        age = time.time() - t0
        mx = st.get("max", 3600)
        return 0 <= age < mx + 300          # 多给 5 分钟余量
    except Exception:
        return False


def shell_alive(dev):
    """adb shell 通道是否还能执行命令。

    ⚠️ 2026-09-27 新增：Android 框架 wedge 时 `am`/`dumpsys`/`ps`(不带-A) 会**永久挂起**，
    但 `adb get-state`/`devices` 仍回 "device"（那只是传输层，不代表 shell 能跑）。
    看门狗原来看不出这一点，只会一遍遍 `am start`（am 自己也在挂）→ 卡死 35 分钟。
    `echo ok` 是 shell 内建、不碰 framework；它都超时 = 只能重启模拟器。
    """
    return _run([ADB, "-s", dev, "shell", "echo ok"], timeout=12).strip() == "ok"


def soul_running(dev):
    """Soul 进程是否在跑。

    ⚠️ 2026-09-27 修复：原用 `ps | grep soulapp` —— `ps` 不带 -A 在本设备**会挂起**
    （走 framework，wedge 时永不返回），20s 超时返回空 → 被误判成"Soul 没跑"
    → 每 5 分钟无脑 `am start`。改用 `pidof`（读 /proc，不碰 framework，快且稳）。
    """
    out = _run([ADB, "-s", dev, "shell", "pidof cn.soulapp.android"], timeout=15)
    return out.strip().isdigit()


def ensure_emulator():
    """模拟器健康 → 返回 (ok, 说明)。不健康就自愈（重启 LDPlayer）。"""
    global _reboot_at
    import time
    devs = adb_devices()
    if devs:
        dev = devs[0]
        # ⭐ shell 通道挂了 = 框架 wedge → 只有重启模拟器才有救（`am start` 无意义）。
        if not shell_alive(dev):
            if round_active():
                return False, "shell 无响应，但**有轮次在跑 → 本轮不重启**（避免打断聊天）"
            if time.time() - _reboot_at < REBOOT_GAP:
                left = int(REBOOT_GAP - (time.time() - _reboot_at))
                return False, f"shell 无响应，距上次重启不足 {REBOOT_GAP}s（剩 {left}s），暂缓"
            log("[模拟器] shell 无响应 → 判定框架 wedge，重启模拟器")
            _run([LDCONSOLE, "reboot", "--index", "0"], timeout=60)
            time.sleep(70)
            _reboot_at = time.time()
            for addr in ("127.0.0.1:5555", "emulator-5554"):
                _run([ADB, "connect", addr], timeout=20)
            ds = adb_devices()
            return (bool(ds), f"已重启模拟器（{ds[0] if ds else '仍无设备'}）")
        if soul_running(dev):
            return True, f"模拟器正常（{dev}），Soul 在运行"
        # adb 通但 Soul 没跑 → 只把 App 拉起来，不用重启模拟器
        _run([ADB, "-s", dev, "shell",
              "am start -n cn.soulapp.android/.component.startup.main.MainActivity "
              "--activity-clear-top"], timeout=30)
        return True, f"adb 正常（{dev}），已拉起 Soul App"

    # ── 不健康：需要重启 ──
    msg = "adb 无设备"
    if round_active():
        return False, f"{msg}，但**有轮次在跑 → 本轮不重启**（避免打断聊天）"
    if time.time() - _reboot_at < REBOOT_GAP:
        left = int(REBOOT_GAP - (time.time() - _reboot_at))
        return False, f"{msg}，距上次重启不足 {REBOOT_GAP}s（还剩 {left}s），暂缓"

    log(f"[模拟器] {msg} → 开始自愈")
    if proc_running("dnplayer.exe"):
        log("  杀掉 LDPlayer 进程")
        _run(["taskkill", "/F", "/IM", "dnplayer.exe"], timeout=30)
        time.sleep(5)
    log("  启动 LDPlayer index=0")
    _run([LDCONSOLE, "launch", "--index", "0"], timeout=120)
    time.sleep(70)                                  # 冷启动慢，给够时间
    _reboot_at = time.time()
    devs = adb_devices()
    if not devs:
        # 雷电重启后端口可能变，主动 connect 常见地址
        for addr in ("127.0.0.1:5555", "emulator-5554"):
            _run([ADB, "connect", addr], timeout=30)
        time.sleep(5)
        devs = adb_devices()
    if devs:
        log(f"  ✅ 模拟器已恢复（{devs[0]}）")
        return True, f"已重启模拟器（{devs[0]}）"
    log("  ❌ 重启后仍无设备")
    return False, "重启模拟器后仍无 adb 设备"


def log(msg):
    line = f"[{datetime.now().strftime('%m-%d %H:%M:%S')}] {msg}"
    print(line)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def beat_age():
    try:
        return time.time() - os.path.getmtime(BEAT)
    except OSError:
        return float("inf")


def kill_watch():
    """杀掉所有在跑 soul_round.py watch 的 python 进程。

    ⚠️ 关键（2026-09-27 实测踩坑）：**"进程还在但心跳过期"就是坏掉的状态**，
    不能"跳过避免重复实例"—— 那会和心跳误判形成死锁（永远不重启）。
    正确做法：先杀干净，再重新拉起。
    """
    killed = []
    try:
        out = subprocess.run(
            ["wmic", "process", "where", "name='python.exe'", "get", "processid,commandline"],
            capture_output=True, timeout=30, creationflags=_NW)
        txt = (out.stdout or b"").decode("utf-8", "ignore")
        import re
        for m in re.finditer(r"(\d+)\s*$", txt, re.M):
            pass                                     # 占位，下面用逐行解析
        for line in txt.splitlines():
            if "soul_round.py" in line and "watch" in line:
                pid = line.strip().split()[-1]
                if pid.isdigit():
                    try:
                        subprocess.run(["taskkill", "/F", "/PID", pid],
                                       capture_output=True, timeout=20, creationflags=_NW)
                        killed.append(pid)
                    except Exception:
                        pass
    except Exception as e:
        log(f"  清理进程时出错: {e!r}")
    return killed


def ensure():
    # ⓪ 用户是否已停用监控？（2026-09-29 加，最高优先级）
    #   教训：本任务由 Windows 计划任务 `SoulWatchdog` **每 5 分钟**调用，
    #   而它用 CREATE_NEW_CONSOLE 拉起的 watch 是**独立进程** ——
    #   只杀 watch 没用，5 分钟内必被本函数复活，
    #   表现得像"用户明明要求停掉监控，它却自己又跑起来了"。
    #   只要 `_watch_stop` 存在，本函数**什么都不做**（连模拟器都不碰）。
    if os.path.exists(os.path.join(r"E:\soul", "_watch_stop")):
        log("[停用] 检测到 _watch_stop → 监控已被停用，本次不拉起 watch")
        return False
    # ① 先保模拟器活着（监控的地基）
    ok, why = ensure_emulator()
    log(f"[模拟器] {why}")
    # ② 再保 watcher 活着
    age = beat_age()
    if age <= MAX_AGE:
        log(f"watch 正常（心跳 {age:.0f}s 前），无需处理")
        return True
    log(f"watch 已停/僵死（心跳 {age:.0f}s 前）→ 先杀后拉")
    killed = kill_watch()
    if killed:
        log(f"  已清掉僵死进程 {killed}")
        time.sleep(2)
    try:
        # 2026-09-30 修黑窗：原来用 CREATE_NEW_CONSOLE，每次拉起 watch 都弹一个常驻黑窗。
        # 改 CREATE_NO_WINDOW + 输出丢弃（watch 自己的日志/心跳照常写文件）。
        subprocess.Popen([PY, ROUND, "watch", "60"],
                         creationflags=_NW, stdout=_DN, stderr=_DN,
                         cwd=r"E:\soul")
        log("  已拉起 watch（60s 间隔）")
        return True
    except Exception as e:
        log(f"  拉起失败: {e!r}")
        return False


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--loop" in args:
        i = args.index("--loop")
        sec = int(args[i + 1]) if i + 1 < len(args) and args[i + 1].isdigit() else 300
        log(f"=== 看门狗常驻启动 interval={sec}s ===")
        while True:
            try:
                ensure()
            except Exception as e:
                log(f"自检异常: {e!r}")
            time.sleep(sec)
    else:
        ensure()
