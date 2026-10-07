# -*- coding: utf-8 -*-
"""soul_monitor.py —— agentM 监控告警官（**纯代码，不调模型**）

【它在闭环里的位置】← 这就是 2026-10-07 接通的那根线
    ⑤ 记忆(agentK) ──写规则──▶ pipeline/checks.json ──读规则──▶ ① 感知(agentM) ──开工单──▶ 工单池
    没有这根线，agentK 的回流只是"写进笔记本"，agentM 永远不会去看 → 两条平行线，不成闭环。

【职责】常驻/单轮巡检本项目健康度，发现问题 → 开工单。
【边界】只读代码与日志、只写自己的 pipeline/ 目录；**不改码、不碰守护、不碰 git**。

【两类检查】
    A. 内置基础检查（BASE_CHECKS，写死在下面的 registry）
    B. **回流检查**（读 pipeline/checks.json）—— agentK 每次回流往里追加

【用法】
    python soul_monitor.py            # 单轮巡检（打印摘要 + 开工单）
    python soul_monitor.py --dry      # 只打印，不写工单
    python soul_monitor.py --list     # 列出全部检查项（含回流来的）
    python soul_monitor.py --loop     # 常驻（默认 300s 一轮，可用 --interval 改）
"""
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
PIPE = os.path.join(BASE, "pipeline")
CHECKS_F = os.path.join(PIPE, "checks.json")
ORDERS_D = os.path.join(PIPE, "orders")
STATE_F = os.path.join(PIPE, "state.json")
DAEMON_LOG = os.path.join(BASE, "_uimap", "daemon", "daemon.0.log")
CODE_WATCH_LOG = os.path.join(BASE, "logs", "code_watch.log")
LOCK_F = os.path.join(BASE, ".soul_auto.lock")

# 同一个检查重复报警的冷却（秒）—— 防止一个问题刷出一堆工单
WO_COOLDOWN = 6 * 3600


def _sh(cmd):
    """跑一条命令，返回 stdout（失败返回空串）。"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           errors="replace", timeout=60)
        return (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return "!! %r" % (e,)


def _ps(script):
    return _sh(["powershell", "-NoProfile", "-NonInteractive", "-Command", script])


def _tail(path, n=400):
    try:
        with io.open(path, encoding="utf-8", errors="replace") as f:
            return f.readlines()[-n:]
    except Exception:
        return []


# ══════════════════ A. 内置基础检查 ══════════════════

def chk_daemon_alive():
    """守护链存活：guard / daemon 各至少一个进程。"""
    out = _ps("Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
              "Where-Object { $_.CommandLine -match 'soul_daemon|_daemon_guard' } | "
              "ForEach-Object { if ($_.CommandLine -match 'soul_daemon') {'daemon'} else {'guard'} }")
    n_daemon = out.count("daemon")
    n_guard = out.count("guard")
    if n_daemon < 1 or n_guard < 1:
        return False, "守护链不完整：guard=%d daemon=%d（进程名匹配）" % (n_guard, n_daemon)
    return True, "guard=%d daemon=%d" % (n_guard, n_daemon)


def chk_lock_stale():
    """整轮锁残留：心跳超过 15 分钟没更新 = 死锁残留。"""
    try:
        d = json.loads(io.open(LOCK_F, encoding="utf-8").read())
    except Exception:
        return True, "无锁文件（正常）"
    hb = float(d.get("heartbeat") or d.get("ts") or 0)
    age = time.time() - hb
    if age > 900:
        return False, ("锁残留 %.0f 分钟：pid=%s step=%s "
                       "（处置：python soul_global_lock.py release）"
                       % (age / 60, d.get("pid"), d.get("step")))
    return True, "锁心跳 %.0f 秒前（正常）" % age


def chk_daemon_errors():
    """守护日志近 30 分钟是否有『!!』异常行。"""
    lines = _tail(DAEMON_LOG, 600)
    now = time.time()
    hits = []
    for ln in lines:
        m = re.match(r"\[(\d\d)-(\d\d) (\d\d):(\d\d):(\d\d)\]", ln)
        if not m:
            continue
        try:
            ts = time.mktime((2026, int(m.group(1)), int(m.group(2)),
                              int(m.group(3)), int(m.group(4)), int(m.group(5)),
                              0, 0, -1))
        except Exception:
            continue
        if now - ts <= 1800 and "!!" in ln:
            hits.append(ln.strip()[:120])
    if hits:
        return False, "近30分钟 %d 条异常，例：%s" % (len(hits), hits[-1])
    return True, "近30分钟无 !! 异常"


def chk_restart_pending():
    """⭐ 今晚的坑：代码改了但守护没重启 → 改动不生效。
    判据：最后一次守护启动时间 < 最后一次 git 提交时间。"""
    lines = _tail(DAEMON_LOG, 800)
    last_boot = None
    for ln in reversed(lines):
        if "预热 OCR 完成" in ln or "[锁] 启动时清理残留锁文件" in ln:
            m = re.match(r"\[(\d\d)-(\d\d) (\d\d):(\d\d):(\d\d)\]", ln)
            if m:
                last_boot = time.mktime((2026, int(m.group(1)), int(m.group(2)),
                                         int(m.group(3)), int(m.group(4)), int(m.group(5)),
                                         0, 0, -1))
                break
    if not last_boot:
        return True, "读不到守护启动时间（跳过）"
    out = _sh(["git", "-C", BASE, "log", "-1", "--format=%ct"]).strip()
    try:
        last_commit = float(re.search(r"\d{9,}", out).group(0))
    except Exception:
        return True, "读不到最后提交时间（跳过）"
    if last_commit > last_boot + 60:
        dt = (last_commit - last_boot) / 60
        return False, ("**改了没重启**：最后提交比守护启动晚 %.0f 分钟 → "
                       "新代码未加载（处置：release 锁 → 重启守护）" % dt)
    return True, "守护启动晚于最后提交（新代码已加载）"


def chk_nick_empty_name():
    """⭐ 今晚真根因：nick 表存在归一化后为空的名字 → 会污染子串匹配。"""
    db = os.path.join(BASE, "soul_memory.db")
    if not os.path.isfile(db):
        return True, "soul_memory.db 不存在（跳过）"
    def norm(n):
        t = str(n or "").replace("~", "").replace("～", "").replace(" ", "").strip()
        t = t.rstrip("。.．!！?？·、,，…～~'\"“”‘’_-")
        return t if len(t) >= 2 else str(n or "").strip().rstrip("。.．!！?？")
    try:
        c = sqlite3.connect(db)
        rows = c.execute("SELECT uid, name FROM nick").fetchall()
        c.close()
    except Exception as e:
        return True, "nick 表读失败（跳过）: %r" % (e,)
    bad = [u for u, n in rows if n and not norm(n)]
    if bad:
        return False, ("nick 表有 %d 条**归一化后为空**的名字（uid=%s）→ "
                       "会污染 _row_sid 子串匹配，导致红点行身份解析全线失败"
                       % (len(bad), ",".join(map(str, bad[:5]))))
    return True, "nick 表 %d 条，无空名污染" % len(rows)


def chk_secret_tracked():
    """敏感文件是否被 git 跟踪（今晚凭据泄露的同类风险）。"""
    risky = [".ark_key", "soul_accounts.json", "soul_vm.json", "agent_models.json",
             "soul_notes.json", ".env"]
    out = _sh(["git", "-C", BASE, "ls-files"] + risky)
    tracked = [l.strip() for l in out.splitlines() if l.strip() in risky]
    if tracked:
        return False, "**敏感文件已入库**：%s（凭据泄露风险，处置：git rm --cached + 加 .gitignore）" % ",".join(tracked)
    return True, "敏感文件均未被 git 跟踪"


def chk_high_risk_change():
    """⭐ 真正要抓的不是「有多少高危变更」，而是「**改了却一直没提交**」
    —— 那才说明有人绕过了流水线在直改生产文件。
    判据：高危文件的工作区脏改动**持续超过 30 分钟**（用 state 记首次发现时间）。
    为什么加时间窗：流水线正常跑时 agent2 本来就会有临时的未提交改动，
    不设窗口会满屏噪音（实测首版就是这个毛病：24h 报 53 条）。"""
    lines = _tail(CODE_WATCH_LOG, 400)
    now = time.time()
    seen = []
    for ln in lines:
        if "高危" not in ln:
            continue
        m = re.match(r"\[(\d{4})-(\d\d)-(\d\d) (\d\d):(\d\d):(\d\d)\]", ln)
        if not m:
            continue
        try:
            ts = time.mktime(tuple(int(x) for x in m.groups()) + (0, 0, -1))
        except Exception:
            continue
        if now - ts > 86400:
            continue
        fm = re.search(r"变更 (\S+\.py)", ln)
        if fm:
            seen.append(fm.group(1))
    if not seen:
        return True, "24h 内无高危变更"
    out = _sh(["git", "-C", BASE, "status", "--porcelain", "--"] + sorted(set(seen)))
    dirty = []
    for l in out.splitlines():
        if l.strip():
            dirty.append(l.split()[-1])
    if not dirty:
        return True, "24h 内 %d 个高危文件变更，均已提交" % len(set(seen))
    # 脏改动的持续时长（首次发现时间记在 state.first_dirty）
    st = _load_state()
    fd = st.setdefault("first_dirty", {})
    stale = []
    for f in dirty:
        t0 = float(fd.get(f, 0)) or now
        if f not in fd:
            fd[f] = now
        if now - t0 > 1800:
            stale.append("%s(已 %.0f 分钟)" % (f, (now - t0) / 60))
    # 清掉已不脏的
    for f in list(fd.keys()):
        if f not in dirty:
            fd.pop(f, None)
    _save_state(st)
    if stale:
        return False, ("高危文件**脏改动超 30 分钟未提交**（疑似绕过流水线直改）：%s"
                       % "; ".join(stale))
    return True, "高危文件有未提交改动 %d 个，但都在 30 分钟内（流水线进行中，正常）" % len(dirty)


# ══════════════════ B. 回流检查（读 checks.json）══════════════════

def run_reflow_checks():
    """执行 checks.json 里的规则。type=grep 时：在 files 里找/找不到 pattern。"""
    out = []
    try:
        cfg = json.loads(io.open(CHECKS_F, encoding="utf-8").read())
    except Exception as e:
        return out, "checks.json 读失败（跳过回流检查）: %r" % (e,)
    import glob as _glob
    for c in (cfg.get("checks") or []):
        if not c.get("enabled", True):
            continue
        cid = c.get("id") or "?"
        ctype = (c.get("type") or "grep").lower()
        try:
            if ctype == "grep":
                pat = re.compile(c.get("pattern") or "")
                files = []
                for g in (c.get("files") or ["*.py"]):
                    files += _glob.glob(os.path.join(BASE, g))
                hit_file = None
                for fp in files:
                    txt = io.open(fp, encoding="utf-8", errors="replace").read()
                    if pat.search(txt):
                        hit_file = os.path.basename(fp)
                        break
                expect = (c.get("expect") or "present").lower()
                if expect == "absent" and hit_file:
                    out.append((cid, False, "%s（回流规则）命中 %s —— 期望不应出现" % (c.get("desc", ""), hit_file)))
                elif expect == "present" and not hit_file:
                    out.append((cid, False, "%s（回流规则）未命中任何文件 —— 期望应存在" % (c.get("desc", ""),)))
                else:
                    out.append((cid, True, c.get("desc", "")))
            else:
                out.append((cid, True, "未知检查类型 %r（跳过）" % ctype))
        except Exception as e:
            out.append((cid, True, "执行异常，跳过: %r" % (e,)))
    return out, None


# ══════════════════ 调度 ══════════════════

BASE_CHECKS = [
    ("daemon_alive", "守护链存活", chk_daemon_alive),
    ("lock_stale", "整轮锁残留", chk_lock_stale),
    ("daemon_errors", "守护日志异常", chk_daemon_errors),
    ("restart_pending", "改了没重启（今晚的坑）", chk_restart_pending),
    ("nick_empty_name", "nick 表空名污染（今晚的根因）", chk_nick_empty_name),
    ("secret_tracked", "敏感文件是否入库", chk_secret_tracked),
    ("high_risk_change", "高危变更未处理", chk_high_risk_change),
]


def _load_state():
    try:
        return json.loads(io.open(STATE_F, encoding="utf-8").read())
    except Exception:
        return {}


def _save_state(s):
    try:
        os.makedirs(PIPE, exist_ok=True)
        io.open(STATE_F, "w", encoding="utf-8").write(json.dumps(s, ensure_ascii=False, indent=1))
    except Exception:
        pass


def open_order(cid, title, detail, dry=False):
    """开工单（去重：同一检查 WO_COOLDOWN 内不重复开）。
    ⚠️ dry=True 时**不写 state**，否则一条 dry 试跑会把冷却时间点写进去，
       真跑时反被判成"冷却中，不重复开"（实测踩过）。"""
    st = _load_state()
    last = float((st.get("reported") or {}).get(cid, 0))
    if time.time() - last < WO_COOLDOWN:
        return None
    if dry:
        return "(dry-run)"
    st.setdefault("reported", {})[cid] = time.time()
    _save_state(st)
    os.makedirs(ORDERS_D, exist_ok=True)
    fn = os.path.join(ORDERS_D, "WO-%s-%s.md" % (time.strftime("%Y%m%d-%H%M%S"), cid))
    body = (
        "# 工单 · %s\n\n"
        "- **检查项**：`%s`\n"
        "- **开出时间**：%s\n"
        "- **来源**：agentM 监控告警（纯代码巡检）\n"
        "- **状态**：NEW\n\n"
        "## 现象\n\n%s\n\n"
        "## 下一步（按 SOUL_Agent流水线.md 七环推进）\n\n"
        "1. ① agent0 定界 —— 先确认这个问题真实存在、划出边界\n"
        "2. ② agent1 方案 → ③ agentG 影响 → ④ agent2 实施 → ⑤ agent3 测试 → ⑥ agent4 终审 → ⑦ agentK 回流\n\n"
        "> 入口：`按 E:\\soul\\SOUL_Agent流水线.md 开新工单。问题：<本工单标题>`\n"
        % (title, cid, time.strftime("%Y-%m-%d %H:%M:%S"), detail)
    )
    io.open(fn, "w", encoding="utf-8").write(body)
    return fn


def one_round(dry=False, verbose=True):
    problems = []
    if verbose:
        print("=" * 64)
        print("agentM 巡检 · %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
        print("=" * 64)
    for cid, desc, fn in BASE_CHECKS:
        try:
            ok, detail = fn()
        except Exception as e:
            ok, detail = True, "执行异常，跳过: %r" % (e,)
        mark = "OK  " if ok else "FAIL"
        if verbose:
            print("  %s %-22s %s" % (mark, desc, detail))
        if not ok:
            problems.append((cid, desc, detail))
    rflow, err = run_reflow_checks()
    if rflow:
        if verbose:
            print("  --- 回流检查（来自 agentK → checks.json）---")
        for cid, ok, detail in rflow:
            if verbose:
                print("  %s %-22s %s" % ("OK  " if ok else "FAIL", cid, detail))
            if not ok:
                problems.append(("reflow:" + cid, "回流检查 %s" % cid, detail))
    if err and verbose:
        print("  ! %s" % err)

    opened = []
    for cid, desc, detail in problems:
        fn = open_order(cid, desc, detail, dry=dry)
        if fn:
            opened.append((desc, fn))
    if verbose:
        print("-" * 64)
        if problems:
            print("发现 %d 个问题，开单 %d 张%s"
                  % (len(problems), len(opened), "（其余冷却中，未重复开）" if len(opened) < len(problems) else ""))
            for desc, fn in opened:
                print("  📋 %s → %s" % (desc, fn))
        else:
            print("全部正常，无工单")
    return problems


def main():
    args = sys.argv[1:]
    if "--list" in args:
        print("内置检查：")
        for cid, desc, _ in BASE_CHECKS:
            print("  %-22s %s" % (cid, desc))
        rflow, err = run_reflow_checks()
        print("回流检查（checks.json）：")
        for cid, _ok, _d in rflow:
            print("  %-22s %s" % (cid, ""))
        if err:
            print("  ! %s" % err)
        return 0
    dry = "--dry" in args
    if "--loop" in args:
        iv = 300
        if "--interval" in args:
            iv = max(60, int(args[args.index("--interval") + 1]))
        print("agentM 常驻启动 · 间隔 %ds · 工单目录 %s" % (iv, ORDERS_D))
        while True:
            try:
                one_round(dry=dry)
            except Exception as e:
                print("!! 巡检异常: %r" % (e,))
            time.sleep(iv)
    one_round(dry=dry)
    return 0


if __name__ == "__main__":
    sys.exit(main())
