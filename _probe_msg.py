# -*- coding: utf-8 -*-
"""取证 v3：133677664 在不在累积库 + im_user_bean 里的名字"""
import sqlite3, os, time, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

con = sqlite3.connect(r"E:\soul\chat_im.db")
print("== chat_im.db 用户表找 133677664 ==")
for t in ("im_user_bean", "im_anonymity_user_bean"):
    try:
        cols = [c[1] for c in con.execute("pragma table_info('%s')" % t)]
        print("  %s cols: %s" % (t, cols))
        for r in con.execute("select * from %s where uid like '%%133677664%%' limit 2" % t):
            print("   ", str(r)[:280])
    except Exception as e:
        print("  %s ERR %r" % (t, e))
con.close()
con = sqlite3.connect(r"E:\soul\im_data.db")
try:
    print("== chatmsg 最新 6 条 ==")
    cols = [c[1] for c in con.execute("pragma table_info('chatmsg')")]
    print("  cols:", cols)
    tcol = [c for c in cols if "time" in c.lower() or c == "ts"]
    oc = tcol[0] if tcol else cols[0]
    for r in con.execute("select * from chatmsg order by %s desc limit 6" % oc):
        d = dict(zip(cols, r))
        ts = d.get(oc) or 0
        tstr = time.strftime("%m-%d %H:%M", time.localtime(ts / 1000 if ts > 1e11 else ts)) if ts else "?"
        print("  [%s] from=%s to=%s | %s" % (tstr, d.get("fromUserId") or d.get("sender") or "?", d.get("toUserId"), str(d.get("msg") or d.get("content") or "")[:40]))
except Exception as e:
    print("  ERR", repr(e))
con.close()

p = r"E:\soul\soul_memory.db"
print("== soul_memory.db ==", "存在" if os.path.exists(p) else "不存在")
if os.path.exists(p):
    con = sqlite3.connect(p)
    tabs = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
    print("  tables:", tabs)
    for t in tabs:
        cols = [c[1] for c in con.execute("pragma table_info('%s')" % t)]
        if not any("id" in c.lower() or "name" in c.lower() for c in cols):
            continue
        hit = None
        for c in cols:
            if "id" in c.lower():
                try:
                    hit = list(con.execute("select * from '%s' where %s like '%%133677664%%' limit 2" % (t, c)))
                    if hit:
                        break
                except Exception:
                    pass
        if hit:
            print("  [%s] 命中 133677664:" % t)
            for r in hit:
                print("   ", str(r)[:300])
    con.close()
