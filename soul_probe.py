# -*- coding: utf-8 -*-
"""Soul 轻量信号探测 —— **不截图、不 OCR、不拉库**，一条 shell 命令拿全部信号

为什么需要它：
  奇遇铃是**不定时弹窗**，新消息也随时来。用截图+OCR 轮询来发现它们，
  每次要 3~5 秒 + 大量 token；而其实模拟器本地就有现成的信号源。

三个信号源（全部实测定标）：
  1. `chatmsg.MAX(localTime)`            → 有新消息（含"已读未回"的情况，因为已读也会更新）
  2. `session.MAX(timestamp)`            → 会话状态变化（未读清零/新会话）
  3. `show_love_bell.xml` 的 mtime       → **奇遇铃展示过**
     （实测：该文件 00:01:48 被写入，与日志 `LoveBellGlobalWindowTask.kt` 时间完全吻合；
       内含 `show_love_time` 记录展示日期）

用法：
  python soul_probe.py            # 打印当前三个信号（一次，约 0.5s）
  python soul_probe.py watch 30   # 循环监控，每 30s 一次；有变化就打印
  python soul_probe.py watch 30 --json  # 同上，输出 JSON（便于被别的程序消费）

设计原则：
  · **快探只做"有没有变化"**，不判断内容；
    真要判断"谁在等我回"仍然走 `soul_im.pending()`（拉库 + 逐会话分析）。
    两级结构：快探（廉价、高频）→ 详查（昂贵、仅事件触发）。
  · 信号取不到时返回 None 并**明确告警**，绝不把"读失败"当成"没变化"——那是静默失败。
"""
import subprocess, sys, os, json, time

MUMU_CLI = r"D:\MuMuPlayer\nx_main\mumu-cli.exe"
VMINDEX = os.environ.get("SOUL_VMINDEX", "0")
DBDIR = "/data/data/cn.soulapp.android/databases"
SESS = "SmNjOUhiUUhZa1RWVlgvZUh1NEExdz09"
IMDB_DEV = f"{DBDIR}/IM-SDK-{SESS}-DATA.db"
LOVEBELL_XML = "/data/data/cn.soulapp.android/shared_prefs/show_love_bell.xml"

BASE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, ".soul_probe_state.json")


def _sh(cmd, timeout=25):
    try:
        p = subprocess.run([MUMU_CLI, "sh", "-v", VMINDEX, "-c", cmd],
                           capture_output=True, timeout=timeout)
        return (p.stdout or b"").decode("utf-8", "ignore").strip()
    except subprocess.TimeoutExpired:
        return "TIMEOUT"
    except Exception as e:
        return f"ERR {e!r}"


def _int_or_none(s):
    s = (s or "").strip()
    try:
        return int(s)
    except ValueError:
        return None


def probe():
    """一次探测，返回 dict。取不到的项为 None（**不等于"没变化"**）。"""
    out = {"ts": int(time.time())}

    # ① + ② ：合并成一条命令（sqlite3 在模拟器里现成的，见 /system/bin/sqlite3）
    r = _sh(f"sqlite3 {IMDB_DEV} 'SELECT MAX(localTime) FROM chatmsg' 2>&1")
    out["msg_max"] = _int_or_none(r)

    r2 = _sh(f"sqlite3 {IMDB_DEV} 'SELECT MAX(timestamp) FROM session' 2>&1")
    out["sess_max"] = _int_or_none(r2)

    # ③ 奇遇铃：文件 mtime 即"最近一次展示时间"
    r3 = _sh(f"stat -c %Y {LOVEBELL_XML} 2>/dev/null")
    out["love_m"] = _int_or_none(r3)

    # 告警：任何一项读不到，都要吵出来（不许静默当"无变化"）
    fails = [k for k in ("msg_max", "sess_max", "love_m") if out[k] is None]
    if fails:
        print(f"!! probe 读取失败: {fails}  (msg={r!r}, sess={r2!r}, love={r3!r})")
    return out


def load_state():
    try:
        with open(STATE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(d):
    try:
        with open(STATE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print(f"!! 保存探测状态失败: {e!r}")


def diff(old, new):
    """对比两次探测 → 变化说明列表（只报真实变化，读失败不算变化）"""
    ev = []
    if old.get("msg_max") is not None and new.get("msg_max") is not None \
            and new["msg_max"] != old["msg_max"]:
        ev.append("new_message")
    if old.get("sess_max") is not None and new.get("sess_max") is not None \
            and new["sess_max"] != old["sess_max"]:
        ev.append("session_change")
    if old.get("love_m") is not None and new.get("love_m") is not None \
            and new["love_m"] != old["love_m"]:
        ev.append("love_bell")          # ⭐ 奇遇铃来了
    return ev


def watch(interval=30, as_json=False):
    """循环监控：有变化就打印事件。**本函数只做感知，不自动回复**——
    回复策略由上层（soul_round / 自动化）决定，避免"监控一开就自动动手"的意外。"""
    prev = load_state()
    if not prev:
        prev = probe()
        save_state(prev)
        print(f"[probe] 基线已建立: {prev}")
    print(f"[probe] 开始监控，每 {interval}s 一轮（信号: 新消息 / 会话变化 / 奇遇铃）")
    while True:
        time.sleep(interval)
        if os.path.exists(os.path.join(BASE, "_watch_stop")):
            print("[probe] 检测到 _watch_stop → 退出")
            return
        cur = probe()
        ev = diff(prev, cur)
        if ev:
            if as_json:
                print(json.dumps({"at": time.strftime("%m-%d %H:%M:%S"), "events": ev,
                                  "signals": cur}, ensure_ascii=False))
            else:
                tag = {"new_message": "🔔 有新消息", "session_change": "📋 会话状态变化",
                       "love_bell": "🔕 奇遇铃来了"}[None] if False else ""
                names = {"new_message": "🔔 有新消息", "session_change": "📋 会话状态变化",
                         "love_bell": "🔕 **奇遇铃**"}
                print(f"[{time.strftime('%H:%M:%S')}] " + " | ".join(names.get(e, e) for e in ev))
            prev = cur
            save_state(cur)
        else:
            # 即使无变化也滚一次状态（时间戳刷新），便于判断"监控还活着"
            prev = cur
            save_state(cur)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    if cmd == "once":
        d = probe()
        print(json.dumps(d, ensure_ascii=False, indent=1))
        if d.get("love_m"):
            print(f"  → 奇遇铃最近展示: {time.strftime('%m-%d %H:%M:%S', time.localtime(d['love_m']))}")
        if d.get("msg_max"):
            print(f"  → 最新消息: {time.strftime('%m-%d %H:%M:%S', time.localtime(d['msg_max'] / 1000))}")
    elif cmd == "watch":
        iv = int(sys.argv[2]) if len(sys.argv) > 2 else 30
        watch(iv, as_json="--json" in sys.argv)
    elif cmd == "reset":
        if os.path.exists(STATE):
            os.remove(STATE)
        print("已重置基线")
