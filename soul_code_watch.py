# -*- coding: utf-8 -*-
"""soul_code_watch.py —— 生产脚本「代码变更」24h 实时监视器（只读，绝不改任何文件）

职责：持续追踪 E:\\soul 与 E:\\soul_worker 下 *.py 的新增 / 修改 / 删除，
      变更即刻落日志 + 跑 py_compile，并对关键生产脚本标注高危。

用法：
    python soul_code_watch.py                 # 常驻（默认 20s 一轮）
    python soul_code_watch.py --once          # 单轮巡检（首次自动建立基线）
    python soul_code_watch.py --stop          # 停止常驻实例
    python soul_code_watch.py --interval 30   # 自定义轮询间隔（秒）
    python soul_code_watch.py --once -v       # 无变化也打印（verbose）

产物：
    .code_watch_state.json    基线（相对路径 -> sha1）
    .code_watch.pid           常驻实例 pid
    logs/code_watch.log       变更日志
"""
import hashlib
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
WATCH_DIRS = [ROOT, os.path.join(os.path.dirname(ROOT), "soul_worker")]
STATE = os.path.join(ROOT, ".code_watch_state.json")
LOG = os.path.join(ROOT, "logs", "code_watch.log")
PIDF = os.path.join(ROOT, ".code_watch.pid")
PY = sys.executable

# 生产关键脚本：一旦变更即高危（需立即评审）
CRITICAL = {
    "soul_daemon.py", "soul_reply.py", "soul_send.py", "soul.py", "soul_im.py",
    "soul_fast.py", "soul_match.py", "soul_acct.py", "soul_rules.py", "soul_db.py",
    "soul_brain.py", "soul_llm.py", "soul_stage.py", "soul_direction.py",
    "_daemon_guard.py", "soul_global_lock.py", "soul_lock.py", "soul_watchdog.py",
    "soul_watcher.py", "soul_read.py", "winshot.py", "soul_pick.py",
}
SKIP = ("__pycache__", ".bak", "_tmp_", ".old-")


def log(msg, echo=True):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    if echo:
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            print(line, flush=True)
        except Exception:
            pass


def sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(65536), b""):
            h.update(blk)
    return h.hexdigest()


def scan():
    cur = {}
    for d in WATCH_DIRS:
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            if not name.endswith(".py") or any(s in name for s in SKIP):
                continue
            p = os.path.join(d, name)
            if not os.path.isfile(p):
                continue
            try:
                cur[os.path.relpath(p, ROOT)] = sha1(p)
            except Exception as e:
                log("!! 读取失败 %s: %r" % (p, e))
    return cur


def load_state():
    try:
        with open(STATE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(s):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=1, sort_keys=True)


def compile_check(rel):
    p = os.path.join(ROOT, rel)
    if not os.path.isfile(p):
        return "文件已删除"
    try:
        r = subprocess.run([PY, "-m", "py_compile", p], capture_output=True, text=True, timeout=30)
    except Exception as e:
        return "py_compile 异常: %r" % (e,)
    if r.returncode == 0:
        return "py_compile OK"
    return "py_compile FAIL: " + (r.stderr or "").strip().replace("\n", " ")[:300]


def one_round(base, verbose=False):
    cur = scan()
    if not base:
        save_state(cur)
        log("基线建立：%d 个 .py 纳入监控 ｜ 目录=%s" % (len(cur), WATCH_DIRS))
        return cur
    added = [k for k in cur if k not in base]
    removed = [k for k in base if k not in cur]
    changed = [k for k in cur if k in base and cur[k] != base[k]]
    for k in sorted(added):
        log("🆕 新增 %s ｜ %s" % (k, compile_check(k)))
    for k in sorted(removed):
        log("🗑 删除 %s" % k)
    for k in sorted(changed):
        risk = "【关键生产脚本·高危】" if os.path.basename(k) in CRITICAL else ""
        log("✏️ 变更 %s %s ｜ %s ｜ %s→%s"
            % (k, risk, compile_check(k), base[k][:8], cur[k][:8]))
    if (added or removed or changed):
        save_state(cur)
    elif verbose:
        log("巡检正常：%d 文件无变化" % len(cur))
    return cur


def stop():
    try:
        with open(PIDF, encoding="utf-8") as f:
            pid = int(f.read().strip())
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        log("已停止监视器 pid=%d" % pid)
    except Exception as e:
        log("停止失败（可能未运行）: %r" % (e,))


def alive(pid):
    try:
        out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid],
                             capture_output=True, text=True).stdout
        return str(pid) in out
    except Exception:
        return False


def main():
    args = sys.argv[1:]
    if "--stop" in args:
        return stop()
    if "--once" in args:
        one_round(load_state(), verbose=("-v" in args))
        return

    iv = 20
    if "--interval" in args:
        iv = max(5, int(args[args.index("--interval") + 1]))
    if os.path.exists(PIDF):
        try:
            old = int(open(PIDF, encoding="utf-8").read().strip())
            if alive(old):
                log("已有实例在跑 pid=%d → 退出" % old)
                return
        except Exception:
            pass
    with open(PIDF, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    log("代码监视器启动 pid=%d ｜ 间隔=%ds ｜ 监控目录=%s" % (os.getpid(), iv, WATCH_DIRS))
    base = load_state()
    while True:
        try:
            base = one_round(base)
        except Exception as e:
            log("!! 巡检异常: %r" % (e,))
        time.sleep(iv)


if __name__ == "__main__":
    main()
