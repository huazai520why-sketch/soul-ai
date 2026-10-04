#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Soul 全量对话扫描：① 漏聊清单 ② 她发的图片（下载到本地供查看）

用法：
    bash soul.sh soul_scan.py             # 只列清单（不下载）
    bash soul.sh soul_scan.py --dl        # 同时下载她发的真图到 photos_inbox/
"""
import datetime
import hashlib
import json
import os
import sqlite3
import sys
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import soul_im  # noqa: E402

try:  # 2026-09-30 双实例：本库按实例分开
    from soul_instance import state_path as _sp
except Exception:
    def _sp(base, name):
        return os.path.join(base, name)

IMDB = _sp(BASE, "im_data.db")
OUT = os.path.join(BASE, "photos_inbox")
ME = soul_im.ME


def fmt(ts):
    try:
        return datetime.datetime.fromtimestamp(ts / 1000).strftime("%m-%d %H:%M")
    except Exception:
        return "?"


def hours(ts):
    return (datetime.datetime.now().timestamp() * 1000 - ts) / 3600000.0


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    dl = "--dl" in sys.argv
    nm = soul_im.names()
    c = sqlite3.connect(IMDB)
    cur = c.cursor()

    # ---------- ① 漏聊扫描 ----------
    rows = cur.execute("SELECT sessionId, toUserId, unReadCount, timestamp FROM session "
                       "ORDER BY timestamp DESC").fetchall()
    pending, cold, balanced, lopsided = [], [], [], []
    now = datetime.datetime.now().timestamp() * 1000
    base = None
    try:
        base = json.load(open(os.path.join(BASE, ".soul_turn_base.json"), encoding="utf-8"))
        base_ts = (base.get("base_ts") or base.get("ts") or 0) if isinstance(base, dict) else 0
    except Exception:
        base_ts = 0

    for sid, uid, unread, ts in rows:
        name = nm.get(str(uid), str(uid))
        if soul_im._is_official(name):
            continue
        # 全量历史（不只基线）——用户要的是"谁被漏聊"，历史欠账也要算
        msgs = cur.execute(
            "SELECT senderId, localTime, text, msgContent FROM chatmsg "
            "WHERE sessionId=? ORDER BY localTime", (sid,)).fetchall()
        real = [(s, lt) for s, lt, t, mc in msgs if not soul_im._is_sys(t, mc)]
        if not real:
            continue
        mine = sum(1 for s, _ in real if str(s) == ME)
        hers = len(real) - mine
        last_sender, last_ts = real[-1]
        h = hours(last_ts)
        if str(last_sender) != ME:
            # 她最后说的 → 我欠她回复（漏聊）
            txt = cur.execute("SELECT text FROM chatmsg WHERE sessionId=? AND localTime=?",
                              (sid, last_ts)).fetchone()
            pending.append((h, name, hers, mine, fmt(last_ts), (txt[0] or "")[:34], unread))
        elif hers >= 6 and h >= 12:
            # 我最后说的 → 球在她那；她条数多说明她投入过，别让她凉了
            cold.append((h, name, hers, mine, fmt(last_ts)))
        elif hers >= 6:
            balanced.append((h, name, hers, mine, fmt(last_ts)))
        # 她主动度远超我 = 我在冷落她（无论谁最后说）
        if hers - mine >= 5 and hers >= 8:
            lopsided.append((hers - mine, name, hers, mine, fmt(last_ts), h))

    print("=" * 72)
    print("① 漏聊（最后一条真人消息是她发的 = 我欠回复）")
    print("=" * 72)
    for h, name, hers, mine, t, txt, unread in sorted(pending, reverse=True):
        print(f"  {h:5.1f}h前 {t}  {name[:16]:<16} 她{hers}/我{mine}  "
              f"{'★未读' if unread else '    '} 「{txt}」")
    print(f"  --- 共 {len(pending)} 个 ---")

    print()
    print("=" * 72)
    print("② 冷场（我最后说的，她 ≥6 条投入过，但已 ≥12h 没动静 = 该唤醒）")
    print("=" * 72)
    for h, name, hers, mine, t in sorted(cold, reverse=True)[:15]:
        print(f"  {h:5.1f}h前 {t}  {name[:16]:<16} 她{hers}/我{mine}")
    print(f"  --- 共 {len(cold)} 个（12h+）---")
    ok = [x for x in balanced if x[0] < 12]
    print(f"  （另有 {len(ok)} 个她投入过但 <12h，暂不需唤醒）")

    print()
    print("=" * 72)
    print("③ 她在主动、我在冷落（她比我还多 ≥5 条真人消息）")
    print("=" * 72)
    for diff, name, hers, mine, t, h in sorted(lopsided, reverse=True)[:20]:
        print(f"  她{hers}/我{mine}（差{diff}） 最后 {t}（{h:.1f}h前）  {name[:18]}")
    print(f"  --- 共 {len(lopsided)} 个 ---")

    # ---------- ② 图片扫描 ----------
    print()
    print("=" * 72)
    print("③ 她发的图片（msgType=2 真图；msgType=8 表情包已排除）")
    print("=" * 72)
    imgs = cur.execute(
        "SELECT sessionId, senderId, localTime, msgContent FROM chatmsg "
        "WHERE msgContent LIKE '%imageUrl%' AND msgType=2 AND senderId<>? "
        "ORDER BY localTime DESC", (ME,)).fetchall()
    os.makedirs(OUT, exist_ok=True)
    got = []
    for sid, sender, lt, mc in imgs:
        try:
            d = json.loads(mc)
            url = d.get("imageUrl") or ""
        except Exception:
            continue
        if "external_expression" in url:
            continue
        uid = str(sid).replace(ME, "").lstrip("9")
        name = nm.get(str(sender), str(sender))
        got.append((lt, name, url, d.get("imageW"), d.get("imageH")))
    for lt, name, url, w, hh in got:
        print(f"  {fmt(lt)}  {name[:16]:<16} {w}x{hh}")
    print(f"  --- 共 {len(got)} 张 ---")

    if dl:
        print()
        print("下载中...")
        n = 0
        for lt, name, url, w, hh in got:
            safe = "".join(ch for ch in name if ch not in '\\/:*?"<>|')[:12]
            tag = hashlib.md5(url.encode()).hexdigest()[:6]   # 同分钟同尺寸会撞名 → 加 URL 指纹
            fn = os.path.join(OUT, f"{safe}_{datetime.datetime.fromtimestamp(lt/1000).strftime('%m%d_%H%M')}_{tag}.jpg")
            if os.path.exists(fn):
                continue
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = r.read()
                open(fn, "wb").write(data)
                n += 1
                print(f"  ✅ {os.path.basename(fn)}  {len(data)//1024}KB")
            except Exception as e:
                print(f"  ❌ {name} {fmt(lt)}: {e}")
        print(f"  下载 {n} 张 → {OUT}")
    c.close()


if __name__ == "__main__":
    main()
