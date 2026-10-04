# -*- coding: utf-8 -*-
import sqlite3, datetime
ME="96691646"
c=sqlite3.connect('im_data.db'); c.row_factory=sqlite3.Row
d0=datetime.datetime.strptime("2026-09-29","%Y-%m-%d"); d1=d0+datetime.timedelta(days=1)
a=int(d0.timestamp()*1000); b=int(d1.timestamp()*1000)
BAD=["我去找你","我来找你","我过去找你","我去你那","我过来找你","我去看你","我飞过去","我买票去","我请假去","我过去看你","我去重庆外","我来见你","我得赶在","机票","我订票","订机票","我买票","我带你","我带你去","我来玩","我去玩","我过来玩","我飞你那","我飞过去找"]
print("=== 新词表复扫今天我发的消息 ===")
n=0
for r in c.execute("select text,localTime from chatmsg where senderId=? and localTime>=? and localTime<? order by localTime",(ME,a,b)):
    t=(r['text'] or '')
    for k in BAD:
        if k in t:
            n+=1; print(" [命中:%s] %s %s"%(k,datetime.datetime.fromtimestamp(r['localTime']/1000).strftime('%H:%M'),t[:50])); break
print("命中:",n)
