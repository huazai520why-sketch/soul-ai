# -*- coding: utf-8 -*-
import sqlite3, datetime, json, sys
ME="96691646"
c=sqlite3.connect('im_data.db'); c.row_factory=sqlite3.Row
targets=sys.argv[1:]
d="2026-09-29"
d0=datetime.datetime.strptime(d,"%Y-%m-%d"); d1=d0+datetime.timedelta(days=1)
a=int(d0.timestamp()*1000); b=int(d1.timestamp()*1000)
# build sid->nick from session extInfo
sids={}
for r in c.execute("select sessionId,extInfo,lastMsgText from session"):
    sids[r['sessionId']]=(r['extInfo'] or '')+'|'+(r['lastMsgText'] or '')
for tg in targets:
    print("\n############", tg, "############")
    found=[s for s,v in sids.items() if tg in v]
    if not found:
        print("  (session 表里没匹配到昵称，改用消息全表反查)")
        found=[r[0] for r in c.execute("select distinct sessionId from chatmsg where text like ?",('%'+tg+'%',))]
    for sid in found[:3]:
        rows=list(c.execute("select senderId,text,localTime from chatmsg where sessionId=? and localTime>=? and localTime<? order by localTime",(sid,a,b)))
        if not rows: continue
        print("  --- sid=%s 今日 %d 条 ---"%(sid,len(rows)))
        for r in rows:
            who="我" if str(r['senderId'])==ME else "她"
            ts=datetime.datetime.fromtimestamp(r['localTime']/1000).strftime('%H:%M')
            print("  %s %s: %s"%(ts,who,(r['text'] or '')[:80]))
