# -*- coding: utf-8 -*-
import sqlite3, sys, datetime
ME="96691646"
c=sqlite3.connect('im_data.db')
c.row_factory=sqlite3.Row
d="2026-09-29"
d0=datetime.datetime.strptime(d,"%Y-%m-%d"); d1=d0+datetime.timedelta(days=1)
a=int(d0.timestamp()*1000); b=int(d1.timestamp()*1000)
# nickname map
nick={}
try:
    for r in c.execute("select sessionId,extInfo from session"):
        nick[r['sessionId']]=r['extInfo']
except Exception as e: pass
DIRECTION_BAD=["我去找你","我来找你","我过去找你","我去你那","我过来找你","我去看你","我飞过去","我买票去","我请假去","我来见你","我过去","我过去看你"]
I_INVITE=["见个面","见一面","什么时候见","约一下","出来吃饭","一起吃个饭","来找我吧","你过来","来重庆玩"]
print("=== 我发的方向违规 / 主动邀约（今天）===")
rows=list(c.execute("select sessionId,text,localTime from chatmsg where senderId=? and localTime>=? and localTime<? order by localTime",(ME,a,b)))
hit=0
for r in rows:
    t=(r['text'] or '')
    for k in DIRECTION_BAD:
        if k in t:
            hit+=1
            print("[BAD]",datetime.datetime.fromtimestamp(r['localTime']/1000).strftime('%H:%M'),t[:60])
            break
    else:
        for k in I_INVITE:
            if k in t:
                hit+=1
                print("[INV]",datetime.datetime.fromtimestamp(r['localTime']/1000).strftime('%H:%M'),t[:60])
                break
print("命中数:",hit)
