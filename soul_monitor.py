# -*- coding: utf-8 -*-
"""Soul 事件监控：奇遇铃 + 待回消息（含**已读未回**）

⭐ 设计目标（2026-09-29）：**零截图、零 adb、零轮询浪费**

监控源（全部直读文件，不碰 UI）：
  1. **奇遇铃** → `/data/data/cn.soulapp.android/shared_prefs/show_love_bell.xml`
     - `show_love_time` 记录「最近弹奇遇铃的日期」
     - 文件 mtime 就是「最后一次弹铃时刻」
     - 实测：00:01 弹铃 ↔ 文件 mtime 00:01 ✓（不用截图/OCR）
  2. **待回消息（含已读未回）** → IM 库 `chatmsg` 表
     - 判据：该会话最后一条消息的 `senderId` **不是我** → 待回
     - **不看 `session.unreadCount`**（有僵尸未读，与 UI 角标对不上，见 SOUL_界面地图.md §9）
     - 「已读未回」和「未读」在这里**是同一件事**：只要最后一句是她说的，就该回

用法：
  python soul_monitor.py once           # 查一次就退出（挂定时任务用）
  python soul_monitor.py watch 60       # 常驻，每 60s 一轮（受 _watch_stop 控制）
  python soul_monitor.py once --json    # 输出 JSON（供上层程序消费）

⚠️ 停止方式：创建 `_watch_stop` 文件即可（watch 模式会体面退出）
"""
import sys, io, os, time, json, subprocess, sqlite3
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

MUMU_CLI = r"D:\MuMuPlayer\nx_main\mumu-cli.exe"
VMINDEX = os.environ.get("SOUL_VMINDEX", "0")
LOVE_BELL = "/data/data/cn.soulapp.android/shared_prefs/show_love_bell.xml"
STOP_FLAG = os.path.join(BASE, "_watch_stop")
STATE_F = os.path.join(BASE, ".soul_monitor_state.json")
LOG_F = os.path.join(BASE, ".soul_monitor.log")

# ⚠️ 不要在模块级包装 sys.stdout —— 被别的脚本 import 时会二次包装，
#    导致原本的 stdout 被关闭（ValueError: I/O operation on closed file）。
#    只在作为主程序运行时包（见文件末尾）。


def sh(cmd, timeout=30):
    try:
        p = subprocess.run([MUMU_CLI, "sh", "-v", VMINDEX, "-c", cmd],
                           capture_output=True, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
        return (p.stdout or b"").decode("utf-8", "ignore").strip()
    except Exception as e:
        return f"ERR {e!r}"


def log(msg):
    line = f"[{datetime.now():%m-%d %H:%M:%S}] {msg}"
    print(line)
    try:
        with open(LOG_F, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ==================== 1. 奇遇铃 ====================
def check_love_bell():
    """返回 dict(active, show_time, mtime, raw) —— 不截图，只读文件"""
    out = sh(f"stat -c '%Y|%y' {LOVE_BELL} 2>/dev/null")
    if not out or out.startswith("ERR") or "|" not in out:
        return {"ok": False, "why": "读不到 show_love_bell.xml", "active": False}
    try:
        mtime = int(out.split("|")[0])
    except (ValueError, IndexError):
        return {"ok": False, "why": f"stat 解析失败: {out[:60]}", "active": False}
    xml = sh(f"cat {LOVE_BELL} 2>/dev/null")
    show_time = ""
    if "show_love_time" in xml:
        seg = xml.split("show_love_time")[1]
        show_time = seg.split(">")[1].split("<")[0].strip() if ">" in seg else ""
    today = datetime.now().strftime("%Y-%m-%d")
    fresh_min = (time.time() - mtime) / 60.0        # 距上次弹铃多少分钟（供调度器判断"刚弹"）
    return {
        "ok": True,
        "active": show_time == today,     # 今天弹过铃
        "show_time": show_time,
        "mtime": mtime,
        "mtime_str": datetime.fromtimestamp(mtime).strftime("%m-%d %H:%M:%S"),
        "fresh_min": round(fresh_min, 1),
        "today": today,
    }


# ==================== 2. 待回消息（未读 + 已读未回）====================
def check_pending():
    """复用 soul_im.pending() —— 口径与人工核对过的版本保持一致。

    ⚠️ 为什么不自查 SQL：soul_im.pending() 里带了**系统号/官方号/已结束对话的过滤**
    （实测自查结果 9 个 vs 它的 6 个，差的就是几个系统号和已婉拒的会话）。
    两套口径并存 = 迟早对不上账，所以这里直接复用。

    判据仍是「最后一条不是我发的」（含已读未回），**不看 unreadCount**。
    """
    try:
        import soul_im as I
    except Exception as e:
        return {"ok": False, "why": f"import soul_im 失败: {e!r}", "items": []}
    pulled = I.pull()
    if not pulled:
        # pull 失败 ≠ 没有待回，必须显式区分（静默失败是头号敌人）
        return {"ok": False, "why": "拉库失败（数据不可信，不可当作无消息）", "items": []}
    try:
        rows = I.pending()
        items = []
        # pending() 返回五元组列表: (ts, name, unread, text, lt)
        for r in rows or []:
            if isinstance(r, (list, tuple)) and len(r) >= 5:
                items.append({
                    "name": str(r[1]),
                    "text": str(r[3])[:40],
                    "unread": r[2],
                    "time": I._fmt(r[4]) if hasattr(I, "_fmt") else str(r[4]),
                    "ts": r[0],
                })
            elif isinstance(r, dict):
                items.append({"name": r.get("name", ""), "text": str(r.get("text", ""))[:40],
                              "unread": r.get("unread"), "time": r.get("time", ""), "ts": r.get("ts", 0)})
        return {"ok": True, "items": items}
    except Exception as e:
        return {"ok": False, "why": f"pending() 异常: {e!r}", "items": []}


# ==================== 主流程 ====================
def load_state():
    try:
        with open(STATE_F, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(s):
    try:
        with open(STATE_F, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False)
    except OSError:
        pass


def scan(quiet=False):
    """扫一次，返回 (bell, pending, changes)"""
    bell = check_love_bell()
    pend = check_pending()
    st = load_state()
    changes = []

    if bell.get("ok"):
        prev = st.get("bell_mtime")
        if prev is not None and bell["mtime"] != prev:
            changes.append(f"🔔 奇遇铃有新动作（{bell['mtime_str']}）")
        st["bell_mtime"] = bell["mtime"]

    if pend.get("ok"):
        cur = {i["name"] for i in pend["items"]}
        prev = set(st.get("pending_names") or [])
        new = cur - prev
        if prev and new:
            changes.append(f"💬 新增待回 {len(new)} 个: {'、'.join(list(new)[:5])}")
        st["pending_names"] = sorted(cur)

    save_state(st)

    if not quiet:
        print("=" * 56)
        if bell.get("ok"):
            flag = "今日已弹" if bell["active"] else "今日未弹"
            print(f"奇遇铃: {flag} | show_love_time={bell['show_time']} | 最后动作 {bell['mtime_str']}")
        else:
            print(f"奇遇铃: ⚠️ {bell.get('why')}")

        if pend.get("ok"):
            print(f"待回消息(含已读未回): {len(pend['items'])} 个")
            for i in pend["items"][:8]:
                print(f"   [{i['time']}] {i['name']}: {i['text']}")
        else:
            print(f"待回消息: ⚠️ {pend.get('why')}")

        if changes:
            print("-" * 56)
            print("变更:")
            for c in changes:
                print("  " + c)
        print("=" * 56)
    return bell, pend, changes


def watch(interval=60):
    log(f"[monitor] 启动，每 {interval}s 一轮（停止：创建 _watch_stop）")
    while True:
        if os.path.exists(STOP_FLAG):
            log("[monitor] 检测到 _watch_stop → 退出")
            return
        try:
            scan(quiet=True)
        except Exception as e:
            log(f"!! 本轮异常（不退出，下轮继续）: {e!r}")
        for _ in range(int(interval)):
            if os.path.exists(STOP_FLAG):
                log("[monitor] 检测到 _watch_stop → 退出")
                return
            time.sleep(1)


if __name__ == "__main__":
    # 只在作为主程序时包装 stdout（避免被 import 时二次包装关掉原 stdout）
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    except Exception:
        pass
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    if cmd == "once":
        as_json = "--json" in sys.argv
        b, p, c = scan(quiet=as_json)          # JSON 模式下不打人类可读输出
        if as_json:
            print(json.dumps({"bell": b, "pending": p, "changes": c}, ensure_ascii=False))
    elif cmd == "watch":
        watch(int(sys.argv[2]) if len(sys.argv) > 2 else 60)
    else:
        print(__doc__)
