# -*- coding: utf-8 -*-
"""临时工具：同名多会话时，按昵称列出 (uid, sessionId, 末条时间/内容)，并可按 sessionId 打印全文。
用法: python _tmp_sess.py find 昵称
      python _tmp_sess.py dump <sessionId> [limit]
      python _tmp_sess.py mine [分钟]     —— 我最近发的消息（不做过滤，看原始）
"""
import sys, sqlite3, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import soul_im as I

def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    cmd = sys.argv[1] if len(sys.argv) > 1 else "find"
    c = sqlite3.connect(I.IMDB)
    if cmd == "find":
        nick = sys.argv[2]
        nm = I.names()
        rows = c.execute("SELECT toUserId, sessionId, timestamp, lastMsgText "
                         "FROM session ORDER BY timestamp DESC").fetchall()
        n = 0
        for uid, sid, lt, lc in rows:
            nk = nm.get(str(uid), str(uid))
            if nick in nk:
                print("uid=%s sid=%s nick=%s" % (uid, sid, nk))
                print("   末条 %s | %s" % (I._fmt(lt), str(lc)[:80]))
                n += 1
        print("--- 命中 %d 个 ---" % n)
    elif cmd == "dump":
        sid = sys.argv[2]
        limit = int(sys.argv[3]) if len(sys.argv) > 3 else 40
        nm = I.names()
        rows = c.execute("SELECT senderId, text, localTime FROM chatmsg WHERE sessionId=? "
                         "ORDER BY localTime DESC LIMIT ?", (sid, limit)).fetchall()
        for s, t, lt in reversed(rows):
            who = "我" if str(s) == I.ME else nm.get(str(s), str(s))[:8]
            print("[%s] %s: %s" % (I._fmt(lt), who, t))
        print("--- %d 条 ---" % len(rows))
    elif cmd == "mine":
        mins = int(sys.argv[2]) if len(sys.argv) > 2 else 60
        t0 = time.time() * 1000 - mins * 60000
        nm = I.names()
        rows = c.execute("SELECT sessionId, senderId, text, localTime FROM chatmsg "
                         "WHERE localTime>=? ORDER BY localTime", (t0,)).fetchall()
        print("IMDB=%s" % I.IMDB)
        print("近 %d 分钟共 %d 条记录" % (mins, len(rows)))
        for sid, s, t, lt in rows:
            who = "我" if str(s) == I.ME else nm.get(str(s), str(s))[:10]
            print("  [%s] %s | %s | %s" % (I._fmt(lt), "ME" if str(s) == I.ME else "  ", sid, str(t)[:40]))
    c.close()

main()
