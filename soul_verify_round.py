#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""收工落点核查：本轮我发的每条消息，逐条落到"谁的哪个 session"，并报"一人多 session"。

为什么必须有它：
- `soul_reply.py` 的自校验在**同名多人**时直接报 "DB 校验失败"（如「可爱的小猫」库里 3 个重名），
  消息其实发对了，但脚本**自己说不清落在哪** —— 这正是"静默失败"的温床。
- 每轮收工的硬性要求：**同一个人本轮的消息只能落在 1 个 sessionId**。串台=事故。

用法：
    bash soul.sh soul_verify_round.py            # 核查"本轮"（锁启动之后）我发的消息
    bash soul.sh soul_verify_round.py 120        # 核查最近 120 分钟
"""
import sys, os, time, sqlite3
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import soul_im as im
import soul_reply as R

LOCK = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".soul_auto.lock")


def round_start_ms(argv):
    # ⚠️ argv 已经是 sys.argv[1:]，别再取 argv[1]（2026-09-29 实测：越界 → 静默退回"锁文件 mtime"，
    #    结果只核到最近 1 条，报"0 串台"——**假绿灯**，比报错更危险）
    if argv and argv[0].isdigit():
        return time.time() * 1000 - int(argv[0]) * 60_000
    try:
        return os.path.getmtime(LOCK) * 1000
    except OSError:
        return time.time() * 1000 - 60 * 60_000


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    # ⚠️ 默认**不 pull**：2026-09-29 实测 `pull()` 会把 IMDB 换成设备侧副本，
    #    并发/占用时那份副本反而更旧 → 核查结果少一大半（谎报"本轮只发了 1 条"）。
    #    核查是只读取证，用本地已有库即可；要最新就显式加 --pull。
    if "--pull" in argv:
        im.pull()
    t0 = round_start_ms(argv)
    nm = im.names()
    c = sqlite3.connect(im.IMDB)
    rows = c.execute("SELECT sessionId, senderId, text, msgContent, localTime FROM chatmsg "
                     "WHERE localTime>=? ORDER BY localTime", (t0,)).fetchall()
    c.close()
    mine = [(sid, tx, lt) for sid, s, tx, ct, lt in rows
            if str(s) == im.ME and tx and not im._is_sys(tx, ct)]
    if not mine:
        print("本轮（%s 之后）没有我发出的真人消息" % im._fmt(t0))
        return
    per = {}
    for sid, tx, lt in mine:
        per.setdefault(sid, []).append((lt, tx))
    print("本轮我发 %d 条，落在 %d 个 session：" % (len(mine), len(per)))
    bad = 0
    for sid, items in sorted(per.items(), key=lambda kv: -max(t for t, _ in kv[1])):
        who = "?"
        try:
            c = sqlite3.connect(im.IMDB)
            r = c.execute("SELECT toUserId FROM session WHERE sessionId=?", (sid,)).fetchone()
            c.close()
            if r:
                who = nm.get(str(r[0]), str(r[0]))
        except Exception:
            pass
        print("\n  [%s] sid=%s  共 %d 条" % (who, sid, len(items)))
        for lt, tx in items:
            print("      %s  %s" % (im._fmt(lt), tx))
    # 一人多 session = 串台嫌疑
    byname = {}
    for sid in per:
        c = sqlite3.connect(im.IMDB)
        r = c.execute("SELECT toUserId FROM session WHERE sessionId=?", (sid,)).fetchone()
        c.close()
        who = nm.get(str(r[0]), str(r[0])) if r else sid
        byname.setdefault(who, []).append(sid)
    for who, sids in byname.items():
        if len(sids) > 1:
            bad += 1
            print("\n⛔ 串台嫌疑：%s 本轮消息落在 %d 个 session：%s" % (who, len(sids), sids))
    print("\n结果：%s" % ("✅ 每人只落 1 个 session，0 串台" if not bad else "⛔ %d 人疑似串台，需人工核对" % bad))


if __name__ == "__main__":
    main(sys.argv[1:])
