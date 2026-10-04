# -*- coding: utf-8 -*-
import sqlite3, datetime
ME="96691646"
c=sqlite3.connect('im_data.db'); c.row_factory=sqlite3.Row
d0=datetime.datetime.strptime("2026-09-29","%Y-%m-%d"); d1=d0+datetime.timedelta(days=1)
a=int(d0.timestamp()*1000); b=int(d1.timestamp()*1000)
print("=== 含'约见面'的她方原话上下文 ===")
for r in c.execute("select sessionId,text,localTime from chatmsg where text like '%约见面%' and localTime>=? and localTime<? ",(a,b)):
    print(r['sessionId'], datetime.datetime.fromtimestamp(r['localTime']/1000).strftime('%H:%M'), (r['text'] or '')[:100])
print()
print("=== 今天我发出消息的字数分布 ===")
import collections
cc=collections.Counter(); longs=[]
for r in c.execute("select text from chatmsg where senderId=? and localTime>=? and localTime<? ",(ME,a,b)):
    t=(r['text'] or '')
    if not t.strip() or t=='None': continue
    n=len(t)
    cc['>40' if n>40 else ('26-40' if n>25 else ('21-25' if n>20 else '<=20'))]+=1
    if n>40: longs.append(n)
print(dict(cc), " 最长:", max(longs) if longs else 0)
