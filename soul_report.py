#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""轮次报告总结（自动生成 md + 控制台摘要）

背景：2026-10-01 用户要求「自动化里面加一个轮次报告总结」。
以前报告是我（模型）凭印象手写的 —— 会漏、会记错、还会把脚本自报的"成功"当真。
现在本脚本**只从数据库和锁文件取数**，一条一条列出来，谁发的、发给谁、内容是什么，
全都能回溯，不再靠记忆。

用法（一律走 soul.sh，不弹黑窗）：
    bash soul.sh soul_report.py                 # 本轮（按全局锁 ts 起算）
    SOUL_VMINDEX=1 bash soul.sh soul_report.py  # 实例1（账号2）
    bash soul.sh soul_report.py --minutes 90    # 锁已释放时，回退看最近 90 分钟
    bash soul.sh soul_report.py --nofile        # 只打印摘要，不落 md

产出：
    D:\\AI\\2026-09-25-18-07-26\\Soul轮次报告_<YYYY-MM-DD-HHMM>_vm<i>.md
"""
import io
import os
import sys
import time
import sqlite3

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

OUT_DIR = r"D:\AI\2026-09-25-18-07-26"
DEFAULT_MIN = 90

MT = {1: "文本", 2: "图片", 3: "语音", 4: "视频", 9: "卡片", 27: "系统卡片",
      32: "选择卡", 35: "卡片"}


def _vm():
    try:
        import soul_instance as SI
        return SI.vm_index(), SI
    except Exception:
        return 0, None


def _round_start(fallback_min):
    """本轮起点。优先级：**轮次起点标记** > 全局锁 ts > 按分钟回退。

    ⚠️ 为什么不能用锁的 ts 当唯一依据（2026-10-01 02:20 实测踩到）：
       锁是**公共文件**，别的 Soul 实例跑 `soul_auto.py` 也会往里写它自己的 ts。
       我释放锁半小时后，另一个实例写了 ts → 报告把"本轮起点"取成了 47 秒前
       → 窗口 0 分钟、整份报告空掉，而我这一轮其实发了好几条。
       标记文件 `.soul_round_start[.i]` 只由本实例 start_round 时写、释放锁后不清，
       才是可靠的"本轮从几点开始"。
    """
    try:
        import soul_global_lock as GL
        ts = GL.read_round_start()
        # 标记太旧（>6h）说明是很早以前那一轮留下的，不是"本轮" → 不用它
        if ts and (time.time() - ts) < 6 * 3600:
            return ts, (f"轮次起点标记（本轮 {time.strftime('%H:%M:%S', time.localtime(ts))}）")
        lk = GL.read_lock() or {}
        if lk.get("ts"):
            return float(lk["ts"]), (f"全局锁 ts {time.strftime('%H:%M:%S', time.localtime(lk['ts']))}"
                                     f"（⚠️ 无起点标记，可能不是本轮的锁）")
    except Exception:
        pass
    return time.time() - fallback_min * 60, f"无起点标记，回退最近 {fallback_min} 分钟"


def _nickmap(IM, c):
    """sessionId → 昵称。"""
    nm = IM.names()
    m = {}
    try:
        for sid, uid in c.execute("SELECT sessionId, toUserId FROM session"):
            m[str(sid)] = nm.get(str(uid), str(uid))
    except Exception:
        pass
    return m


def _hm(ms):
    try:
        return time.strftime("%H:%M", time.localtime(ms / 1000))
    except Exception:
        return "?"


def main():
    minutes = DEFAULT_MIN
    if "--minutes" in sys.argv:
        i = sys.argv.index("--minutes")
        if i + 1 < len(sys.argv) and sys.argv[i + 1].isdigit():
            minutes = int(sys.argv[i + 1])
    nofile = "--nofile" in sys.argv

    vm, SI = _vm()
    import soul_im as IM
    IM.pull()

    start, start_why = _round_start(minutes)
    start_ms = int(start * 1000)
    now = time.time()

    c = sqlite3.connect(IM.IMDB)
    nick = _nickmap(IM, c)
    rows = c.execute(
        "SELECT localTime, senderId, sessionId, text, msgType FROM chatmsg "
        "WHERE localTime>=? ORDER BY localTime ASC", (start_ms,)).fetchall()
    c.close()

    mine, hers = [], []
    for lt, sid_, sess, text, mt in rows:
        who = "我" if str(sid_) == str(IM.ME) else "她"
        item = dict(t=lt, name=nick.get(str(sess), str(sess)[-12:]),
                    text=(text or ""), mt=MT.get(mt, f"type{mt}"))
        (mine if who == "我" else hers).append(item)

    # 只保留真人文本（系统卡片单列，不算发言）
    def real(rows_):
        return [r for r in rows_ if r["text"] and r["mt"] not in ("系统卡片", "卡片", "选择卡")]

    mine_real = real(mine)

    # ---- 待回 ----
    try:
        pend = IM.pending() or []
    except Exception as e:
        pend = []
        print(f"  !! pending() 失败: {e!r}")

    # ---- 方向审计 ----
    rev, cold, weak = [], [], []
    try:
        import soul_direction as SD
        for r in SD.scan():
            if r.get("reverse"):
                rev.append(r)
            if not r.get("anchors") and r.get("total", 0) >= 8:
                cold.append(r)
            if r.get("weak"):
                weak.append(r)
    except Exception as e:
        print(f"  !! 方向审计失败: {e!r}")

    # ---- 环境体检（截图尺寸 = OCR 质量的命门）----
    shot = SI.state_path(BASE, "wshot.png") if SI else os.path.join(BASE, "wshot.png")
    shot_size, items_n, ocr_warn = "?", "?", ""
    try:
        from PIL import Image
        with Image.open(shot) as im:
            shot_size = f"{im.size[0]}x{im.size[1]}"
            if im.size[0] < 500:
                ocr_warn = (f"⚠️ 截图宽 {im.size[0]}px < 500 → OCR 会严重退化（认错字→找错人）。"
                            f"把 MuMu 窗口拉回 541x993（截图 533x948）即可。")
    except Exception as e:
        shot_size = f"读不到({e!r})"
    try:
        import soul_read as rd
        items_n = len(rd.items())
    except Exception:
        pass

    # ================= 组装 md =================
    ts = time.strftime("%Y-%m-%d-%H%M")
    L = []
    A = L.append
    A(f"# Soul 轮次报告 · {time.strftime('%Y-%m-%d %H:%M')}（vm{vm}）\n")
    A(f"> 统计窗口：{start_why} → {time.strftime('%H:%M:%S')}"
      f"（约 {int((now - start) / 60)} 分钟）\n")
    dbname = "im_data.db" if vm == 0 else f"im_data.{vm}.db"
    A(f"> 实例 vm{vm}｜我方 uid `{IM.ME}`｜数据来源：Soul 原库 `{dbname}`（不靠记忆、不靠脚本自报）\n")

    A("## 一、本轮我发出去的（逐条核过原文）\n")
    if mine_real:
        A("| 时间 | 对象 | 内容 | 类型 |")
        A("|---|---|---|---|")
        for r in mine_real:
            A(f"| {_hm(r['t'])} | {r['name']} | {r['text'].replace('|', '/')} | {r['mt']} |")
    else:
        A("**本轮一条都没发出去。**")
    n_card = len(mine) - len(mine_real)
    if n_card:
        A(f"\n（另有 {n_card} 条系统卡片/非文本，不计入发言）")

    A("\n## 二、她那边来过的消息\n")
    hers_real = real(hers)
    if hers_real:
        for r in hers_real[-20:]:
            A(f"- {_hm(r['t'])} **{r['name']}**：{r['text'][:60]}")
    else:
        A("无。")

    A("\n## 三、待回（数据库口径：未读 + 已读未回）\n")
    if pend:
        for p in pend[:20]:
            A(f"- {p[1] if len(p) > 1 else p}：{str(p[-1])[:50] if len(p) > 2 else ''}")
    else:
        A("无。")

    A("\n## 四、方向审计\n")
    A(f"- ⛔ 反向违规（说了'我去找你'这类）：**{len(rev)}**")
    for r in rev[:10]:
        A(f"  - {r['name']} → {r['reverse']}")
    A(f"- 🔴 长聊无重庆锚点（真人≥8 条）：**{len(cold)}**")
    for r in cold[:10]:
        A(f"  - {r['name']}（{r['total']} 条）")
    A(f"- ⚠️ 自贬/撤退表达：**{len(weak)}**")
    for r in weak[:10]:
        A(f"  - {r['name']} → {r['weak']}")

    A("\n## 五、环境体检\n")
    A(f"- 截图尺寸：**{shot_size}**（正常应 ≈ 533×948；换算因子 1.688）")
    A(f"- 当前页 OCR 条目数：{items_n}")
    if ocr_warn:
        A(f"- {ocr_warn}")

    md = "\n".join(L) + "\n"

    # ================= 控制台摘要（给自动化直接贴）=================
    print("=" * 62)
    print(f"轮次报告 vm{vm}｜{time.strftime('%H:%M:%S')}｜窗口 {int((now - start) / 60)} 分钟")
    print("=" * 62)
    print(f"我方发出 {len(mine_real)} 条（另有 {n_card} 条系统卡片）：")
    for r in mine_real:
        print(f"   {_hm(r['t'])}  {r['name'][:12]:<12} {r['text'][:40]}")
    print(f"她来的消息 {len(hers_real)} 条｜待回 {len(pend)} 个")
    print(f"方向：反向违规 {len(rev)}｜零锚点 {len(cold)}｜自贬 {len(weak)}")
    print(f"环境：截图 {shot_size}｜OCR 条目 {items_n}")
    if ocr_warn:
        print("⛔ " + ocr_warn)

    if not nofile:
        os.makedirs(OUT_DIR, exist_ok=True)
        path = os.path.join(OUT_DIR, f"Soul轮次报告_{ts}_vm{vm}.md")
        with io.open(path, "w", encoding="utf-8") as f:
            f.write(md)
        print("报告已写入:", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
