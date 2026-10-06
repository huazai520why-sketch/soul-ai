# -*- coding: utf-8 -*-
"""Soul 自动聊天 · 可视化控制台（零第三方依赖，Python 标准库）

跑法（副机）：
  pythonw E:\\soul\\soul_console.py          # 后台无窗
  python  E:\\soul\\soul_console.py          # 前台调试
浏览器打开：http://<副机IP>:8910/

数据全部只读：daemon/guard 日志、state.json、IM SQLite（immutable 只读打开），
不 pull、不点击、不影响 daemon 运行。
"""
import os, re, sys, json, time, sqlite3, subprocess, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime

VM = os.environ.get("SOUL_VMINDEX", "0")
BASE = r"E:\soul"
OUTD = os.path.join(BASE, "_uimap", "daemon")
DLOG = os.path.join(OUTD, "daemon.%s.log" % VM)
GLOG = os.path.join(OUTD, "guard.%s.log" % VM)
STATE = os.path.join(OUTD, "state.%s.json" % VM)
PORT = int(os.environ.get("SOUL_CONSOLE_PORT", "8910"))
SEND_CAP_H = 40
COOL_SEC = 40 * 60

sys.path.insert(0, BASE)
_IMDB = None
try:
    import soul_im as _im
    _IMDB = _im.IMDB
    _ME = _im.ME
except Exception:
    _im = None
    _ME = "0"

_cache = {"ts": 0, "data": None}
_cache_lock = threading.Lock()


def _tail(path, n=400):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            block = 8192
            data = b""
            while size > 0 and data.count(b"\n") <= n:
                step = min(block, size)
                size -= step
                f.seek(size)
                data = f.read(step) + data
            if size > 0:
                f.seek(size)
                data = f.read()
        return data.decode("utf-8", "replace").splitlines()[-n:]
    except Exception:
        return []


def _names():
    if _im is None:
        return {}
    try:
        return _im.names()
    except Exception:
        return {}


def _pending_rows():
    """只读直查本地 IM 库：未读会话 + 末条消息（不 pull、不碰设备）。"""
    if not _IMDB or not os.path.exists(_IMDB):
        return []
    nm = _names()
    try:
        c = sqlite3.connect("file:%s?mode=ro&immutable=1" % _IMDB.replace("\\", "/"),
                            uri=True, timeout=3)
        rows = c.execute("SELECT sessionId,toUserId,unReadCount,timestamp,lastMsgText FROM session "
                         "WHERE unReadCount>0 ORDER BY timestamp DESC LIMIT 30").fetchall()
        out = []
        for sid, uid, unread, ts, lastmsg in rows:
            txt, mt = "", 0
            try:
                r = c.execute("SELECT senderId,text,msgType FROM chatmsg WHERE sessionId=? "
                              "ORDER BY localTime DESC LIMIT 6", (sid,)).fetchall()
                for sender, text, mtype in r:
                    t = str(text or "").strip()
                    if t and str(sender) != str(_ME):
                        txt, mt = t[:40], int(mtype or 0)
                        break
            except Exception:
                pass
            # chatmsg 取不到时用 session 自带的 lastMsgText 兜底
            if not txt and lastmsg:
                txt = str(lastmsg).strip()[:40]
            out.append({
                "name": nm.get(str(uid), str(uid))[:16],
                "unread": int(unread or 0),
                "ts": int(ts or 0),
                "text": txt,
                "voice": mt == 5,
            })
        c.close()
        return out
    except Exception:
        return []


def _proc_alive(images):
    try:
        r = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True,
                           timeout=8,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
        out = r.stdout.decode("utf-8", "ignore").lower()
        return {img: (img.lower() in out) for img in images}
    except Exception:
        return {img: False for img in images}


def _tcp_ok(host, port, timeout=2):
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _parse_events(lines):
    """解析日志行为结构化事件。"""
    events, sent, fail = [], 0, 0
    last_reply_name = ""
    today = time.strftime("%m-%d")
    for ln in lines:
        m = re.match(r"\[(\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})\]\s?(.*)$", ln)
        if not m:
            continue
        day, hm, body = m.group(1), m.group(2), m.group(3)
        if day != today:
            continue
        ts_str = "%s %s" % (day, hm)
        kind = "info"
        name, text = "", ""
        mm = re.search(r"⚡已发\s*(.+?):\s*(.+)$", body)
        if mm:
            kind, name, text = "sent", mm.group(1).strip(), mm.group(2).strip()
            sent += 1
        elif "发送未成功" in body:
            kind, name = "fail", last_reply_name
            fail += 1
        else:
            mm = re.search(r"「(.+?)」她发来[:：]\s*(.*)$", body)
            if mm:
                kind, name, text = "recv", mm.group(1), mm.group(2)
                last_reply_name = name
            elif "末条是语音" in body:
                kind = "voice"
                mm2 = re.search(r"🎙\s*(\S+?)\s*末条", body)
                if mm2:
                    name = mm2.group(1)
            elif "僵尸待回降级" in body:
                kind = "zombie"
                text = body.split(":", 1)[-1].strip()
            elif "重试冷却中" in body:
                kind = "cool"
                mm2 = re.search(r"不再为它忙\)[:：]?\s*(.+)$", body)
                if mm2:
                    text = mm2.group(1)
            elif "星球匹配" in body or "匹配到" in body:
                kind, text = "match", body.strip()[:60]
            elif "唤醒" in body and ("开始" in body or "唤醒老人" in body):
                kind, text = "wake", body.strip()[:60]
        events.append({"t": ts_str, "kind": kind, "name": name, "text": text[:80]})
    return events[-60:], sent, fail


def collect():
    now = time.time()
    with _cache_lock:
        if _cache["data"] and now - _cache["ts"] < 3:
            return _cache["data"]

    lines = _tail(DLOG, 500)
    events, sent, fail = _parse_events(lines)

    # 心跳：最后一条带时间戳的日志
    hb_age = None
    for ln in reversed(lines):
        m = re.match(r"\[(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})\]", ln)
        if m:
            lt = time.localtime()
            hb = time.mktime((lt.tm_year, int(m.group(1)), int(m.group(2)),
                              int(m.group(3)), int(m.group(4)), int(m.group(5)), 0, 0, -1))
            hb_age = max(0, int(now - hb))
            break

    # state.json
    st = {}
    try:
        st = json.load(open(STATE, encoding="utf-8"))
    except Exception:
        pass
    tries = st.get("tries", {}) or {}
    cooling = []
    for k, v in tries.items():
        try:
            n, ts = int(v[0]), float(v[1])
            if n >= 3 and now - ts < COOL_SEC:
                cooling.append({"name": k.split("|", 1)[0], "n": n,
                                "left": int(COOL_SEC - (now - ts))})
        except Exception:
            continue
    cooling.sort(key=lambda x: -x["n"])

    # 回复耗时（最近一轮 t 字典里的 回复·* 条目）
    reply_t = [int(v) for k, v in (st.get("t") or {}).items() if k.startswith("回复·")]
    avg_reply = int(sum(reply_t) / len(reply_t) / 1000) if reply_t else 0

    procs = _proc_alive(["python.exe", "pythonw.exe"])
    # 模型服务在本机 192.168.10.210:11434（副机远程调用）→ TCP 探活
    ollama_ok = _tcp_ok("192.168.10.210", 11434)
    glines = _tail(GLOG, 20)
    guard_age = None
    for ln in reversed(glines):
        m = re.match(r"\[(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})\]", ln)
        if m:
            lt = time.localtime()
            gt = time.mktime((lt.tm_year, int(m.group(1)), int(m.group(2)),
                              int(m.group(3)), int(m.group(4)), int(m.group(5)), 0, 0, -1))
            guard_age = max(0, int(now - gt))
            break

    pending = _pending_rows()
    total = sent + fail
    data = {
        "now": int(now * 1000),
        "daemon": {
            "hb_age": hb_age,
            "alive": (hb_age is not None and hb_age < 300),
            "py_proc": procs.get("python.exe", False) or procs.get("pythonw.exe", False),
        },
        "guard": {"age": guard_age, "alive": guard_age is not None and guard_age < 180},
        "ollama": ollama_ok,
        "stats": {
            "sent": sent,
            "fail": fail,
            "rate": round(sent * 100.0 / total, 1) if total else 100.0,
            "hour_cnt": int(st.get("send_cnt", 0)) if st.get("send_hour") == time.strftime("%Y%m%d%H") else 0,
            "hour_cap": SEND_CAP_H,
            "fails": int(st.get("fail", 0)),
            "avg_reply": avg_reply,
            "last_match": int(now - st["last_match"]) if st.get("last_match") else None,
            "last_wake": int(now - st["last_wake"]) if st.get("last_wake") else None,
        },
        "pending": pending,
        "cooling": cooling[:12],
        "events": list(reversed(events)),
    }
    with _cache_lock:
        _cache["data"], _cache["ts"] = data, now
    return data


HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Soul 自动聊天控制台</title>
<style>
:root{
  --bg:#0b0f14; --panel:#121922; --panel2:#18212d; --line:#22303f;
  --txt:#dbe6f0; --dim:#7d8fa3; --cyan:#2de2c8; --cyan-d:#134b45;
  --green:#3ddc84; --red:#ff5c6c; --amber:#ffb547; --blue:#5aa9ff; --purple:#b58cff;
}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--txt);
  font-family:"Microsoft YaHei","PingFang SC",system-ui,sans-serif;font-size:14px}
header{display:flex;align-items:center;gap:16px;padding:14px 22px;
  background:linear-gradient(90deg,#0e1620,#0b0f14);border-bottom:1px solid var(--line);
  position:sticky;top:0;z-index:10}
header h1{font-size:18px;font-weight:600;letter-spacing:1px}
header h1 span{color:var(--cyan)}
.dot{width:10px;height:10px;border-radius:50%;display:inline-block;margin-right:7px;
  box-shadow:0 0 8px currentColor}
.dot.on{background:var(--green);color:var(--green);animation:pulse 2s infinite}
.dot.off{background:var(--red);color:var(--red)}
.dot.wait{background:var(--amber);color:var(--amber)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.45}}
.hb{color:var(--dim);font-size:13px}
.spacer{flex:1}
.clock{font-variant-numeric:tabular-nums;color:var(--dim);font-size:13px}
.wrap{padding:18px 22px;display:grid;gap:16px;
  grid-template-columns:1fr 380px;grid-template-areas:"kpi kpi" "feed side";max-width:1500px;margin:0 auto}
.kpis{grid-area:kpi;display:grid;grid-template-columns:repeat(6,1fr);gap:12px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.kpi .label{color:var(--dim);font-size:12px;margin-bottom:7px}
.kpi .val{font-size:26px;font-weight:700;font-variant-numeric:tabular-nums;line-height:1.1}
.kpi .sub{font-size:12px;color:var(--dim);margin-top:5px}
.val.green{color:var(--green)}.val.red{color:var(--red)}.val.amber{color:var(--amber)}
.val.cyan{color:var(--cyan)}.val.blue{color:var(--blue)}
.feed{grid-area:feed;background:var(--panel);border:1px solid var(--line);border-radius:12px;
  overflow:hidden;display:flex;flex-direction:column;min-height:480px}
.side{grid-area:side;display:flex;flex-direction:column;gap:16px}
.ttl{padding:12px 16px;font-weight:600;border-bottom:1px solid var(--line);
  display:flex;align-items:center;gap:8px;background:var(--panel2)}
.ttl .n{margin-left:auto;background:var(--cyan-d);color:var(--cyan);font-size:12px;
  border-radius:10px;padding:2px 9px;font-weight:700}
.evlist{overflow-y:auto;flex:1;max-height:640px}
.ev{display:flex;gap:10px;padding:9px 16px;border-bottom:1px solid rgba(34,48,63,.5);
  align-items:flex-start;font-size:13.5px;line-height:1.45}
.ev:hover{background:rgba(45,226,200,.04)}
.ev .t{color:var(--dim);font-variant-numeric:tabular-nums;white-space:nowrap;font-size:12px;
  padding-top:2px;min-width:78px}
.ev .ic{width:22px;text-align:center;flex:none}
.ev .bd{min-width:0}
.ev .nm{font-weight:600}
.ev .ms{color:var(--dim);word-break:break-all}
.ev.sent .nm{color:var(--green)}
.ev.recv .nm{color:var(--blue)}
.ev.fail{background:rgba(255,92,108,.07)}
.ev.fail .nm,.ev.fail .ms{color:var(--red)}
.ev.voice .nm{color:var(--purple)}
.ev.match .ms{color:var(--cyan)}
.ev.wake .ms{color:var(--purple)}
.ev.cool .ms,.ev.zombie .ms{color:var(--amber)}
.pendlist{max-height:300px;overflow-y:auto}
.pitem{padding:10px 16px;border-bottom:1px solid rgba(34,48,63,.5)}
.pitem:hover{background:rgba(45,226,200,.04)}
.pitem .r1{display:flex;align-items:center;gap:8px}
.badge{background:var(--red);color:#fff;border-radius:9px;font-size:11px;font-weight:700;
  padding:1px 7px;min-width:20px;text-align:center}
.pitem .pnm{font-weight:600;font-size:13.5px}
.pitem .pt{margin-left:auto;color:var(--dim);font-size:11px;font-variant-numeric:tabular-nums}
.pitem .pmsg{color:var(--dim);font-size:12.5px;margin-top:3px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.voicetag{color:var(--purple);font-size:11px;margin-left:5px}
.chip{display:inline-flex;align-items:center;gap:6px;background:var(--panel2);
  border:1px solid var(--line);border-radius:8px;padding:6px 11px;font-size:12.5px;margin:10px 0 0 14px}
.chip .s{font-weight:700}
.chip.ok .s{color:var(--green)}.chip.bad .s{color:var(--red)}
.kv{padding:9px 16px;display:flex;justify-content:space-between;font-size:13px;
  border-bottom:1px solid rgba(34,48,63,.5)}
.kv .k{color:var(--dim)}.kv .v{font-variant-numeric:tabular-nums}
.coolitem{padding:7px 16px;font-size:12.5px;display:flex;gap:8px;border-bottom:1px solid rgba(34,48,63,.5)}
.coolitem .cn{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.coolitem .cl{color:var(--amber);font-variant-numeric:tabular-nums}
.empty{padding:26px 16px;text-align:center;color:var(--dim);font-size:13px}
footer{color:var(--dim);font-size:12px;text-align:center;padding:14px}
.bar{height:6px;border-radius:3px;background:var(--panel2);margin-top:8px;overflow:hidden}
.bar i{display:block;height:100%;background:linear-gradient(90deg,var(--cyan),var(--blue))}
@media(max-width:1000px){
  .wrap{grid-template-columns:1fr;grid-template-areas:"kpi" "side" "feed"}
  .kpis{grid-template-columns:repeat(3,1fr)}
}
</style>
</head>
<body>
<header>
  <h1><span>Soul</span> 自动聊天控制台</h1>
  <div><span id="ddot" class="dot wait"></span><span id="dstate" class="hb">检测中…</span></div>
  <div class="spacer"></div>
  <div class="hb">Ollama <span id="ollama">·</span> ｜ Guard <span id="guard">·</span></div>
  <div class="clock" id="clock"></div>
</header>
<div class="wrap">
  <div class="kpis" id="kpis"></div>

  <div class="feed">
    <div class="ttl">📡 实时动态 <span class="n" id="evn">0</span></div>
    <div class="evlist" id="events"><div class="empty">等待数据…</div></div>
  </div>

  <div class="side">
    <div class="card" style="padding:0;overflow:hidden">
      <div class="ttl">🔴 待回会话 <span class="n" id="pn">0</span></div>
      <div class="pendlist" id="pending"><div class="empty">暂无红点</div></div>
    </div>
    <div class="card" style="padding:0;overflow:hidden">
      <div class="ttl">❄️ 重试冷却 <span class="n" id="cn">0</span></div>
      <div id="cooling" style="max-height:210px;overflow-y:auto"><div class="empty">无冷却</div></div>
    </div>
    <div class="card" style="padding:0;overflow:hidden">
      <div class="ttl">🩺 服务健康</div>
      <div class="kv"><span class="k">本小时配额</span><span class="v" id="quota">–</span></div>
      <div class="bar" style="margin:0 16px 10px"><i id="quotabar" style="width:0"></i></div>
      <div class="kv"><span class="k">连续失败</span><span class="v" id="fails">–</span></div>
      <div class="kv"><span class="k">平均回复耗时</span><span class="v" id="avg">–</span></div>
      <div class="kv"><span class="k">上次匹配</span><span class="v" id="lm">–</span></div>
      <div class="kv"><span class="k">上次唤醒</span><span class="v" id="lw">–</span></div>
    </div>
  </div>
</div>
<footer>数据每 5 秒自动刷新 · 只读自 daemon 日志 / state.json / IM 数据库 · 不影响自动聊天运行</footer>

<script>
const ICONS={sent:"✅",recv:"💬",fail:"⚠️",voice:"🎙",zombie:"🧟",cool:"❄️",match:"🌍",wake:"📣",info:"·"};
const $=id=>document.getElementById(id);
const esc=s=>(s||"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function ago(s){if(s==null)return "–";if(s<60)return s+"秒前";if(s<3600)return Math.floor(s/60)+"分前";
  return Math.floor(s/3600)+"时"+Math.floor(s%3600/60)+"分前";}
function hhmm(ts){const d=new Date(ts);return String(d.getHours()).padStart(2,"0")+":"+
  String(d.getMinutes()).padStart(2,"0");}
function fmtTime(s){const m=Math.floor(s/60),ss=s%60;return m+"分"+ss+"秒";}
let first=true;
async function tick(){
  try{
    const r=await fetch("/api/overview",{cache:"no-store"});
    const d=await r.json();
    // 头部状态
    const dd=$("ddot"),ds=$("dstate");
    if(d.daemon.alive){dd.className="dot on";ds.textContent="运行中 · 心跳 "+ago(d.daemon.hb_age);}
    else{dd.className="dot off";ds.textContent=d.daemon.hb_age==null?"无心跳":("停滞 "+ago(d.daemon.hb_age));}
    $("ollama").innerHTML=d.ollama?'<span style="color:var(--green)">●</span>':'<span style="color:var(--red)">●</span>';
    $("guard").innerHTML=d.guard.alive?'<span style="color:var(--green)">● '+ago(d.guard.age)+'</span>':
      '<span style="color:var(--red)">● 异常</span>';
    // KPI
    const s=d.stats;
    const rateCls=s.rate>=80?"green":s.rate>=50?"amber":"red";
    $("kpis").innerHTML=`
      <div class="card kpi"><div class="label">今日已发</div><div class="val cyan">${s.sent}</div>
        <div class="sub">失败 ${s.fail} 条</div></div>
      <div class="card kpi"><div class="label">发送成功率</div><div class="val ${rateCls}">${s.rate}%</div>
        <div class="sub">${s.sent}/${s.sent+s.fail}</div></div>
      <div class="card kpi"><div class="label">待回红点</div><div class="val red">${d.pending.length}</div>
        <div class="sub">未读会话</div></div>
      <div class="card kpi"><div class="label">冷却中</div><div class="val amber">${d.cooling.length}</div>
        <div class="sub">重试闸保护</div></div>
      <div class="card kpi"><div class="label">本小时已发</div><div class="val blue">${s.hour_cnt}</div>
        <div class="sub">上限 ${s.hour_cap} 条</div></div>
      <div class="card kpi"><div class="label">平均回复</div><div class="val ${s.avg_reply>90?"red":s.avg_reply>60?"amber":"green"}">${s.avg_reply?s.avg_reply+"s":"–"}</div>
        <div class="sub">连续失败 ${s.fails}</div></div>`;
    // 事件流
    $("evn").textContent=d.events.length;
    $("events").innerHTML=d.events.length?d.events.map(e=>`
      <div class="ev ${e.kind}"><span class="t">${e.t.slice(6)}</span><span class="ic">${ICONS[e.kind]||"·"}</span>
      <span class="bd">${e.name?'<span class="nm">'+esc(e.name)+'</span> ':''}
      <span class="ms">${esc(e.text)}</span></span></div>`).join(""):'<div class="empty">暂无事件</div>';
    // 待回
    $("pn").textContent=d.pending.length;
    $("pending").innerHTML=d.pending.length?d.pending.map(p=>`
      <div class="pitem"><div class="r1"><span class="badge">${p.unread}</span>
      <span class="pnm">${esc(p.name)}</span>${p.voice?'<span class="voicetag">🎙语音</span>':''}
      <span class="pt">${p.ts?hhmm(p.ts):""}</span></div>
      <div class="pmsg">${esc(p.text)||"(无文本)"}</div></div>`).join(""):'<div class="empty">暂无红点 🎉</div>';
    // 冷却
    $("cn").textContent=d.cooling.length;
    $("cooling").innerHTML=d.cooling.length?d.cooling.map(c=>`
      <div class="coolitem"><span class="cn">${esc(c.name)}</span>
      <span style="color:var(--dim)">×${c.n}</span><span class="cl">${fmtTime(c.left)}</span></div>`).join(""):
      '<div class="empty">无冷却</div>';
    // 健康
    $("quota").textContent=`${s.hour_cnt} / ${s.hour_cap}`;
    $("quotabar").style.width=Math.min(100,s.hour_cnt/s.hour_cap*100)+"%";
    $("fails").innerHTML=s.fails?'<span style="color:var(--red)">'+s.fails+'</span>':'<span style="color:var(--green)">0</span>';
    $("avg").textContent=s.avg_reply?s.avg_reply+" 秒":"–";
    $("lm").textContent=ago(s.last_match);
    $("lw").textContent=ago(s.last_wake);
  }catch(e){$("dstate").textContent="数据获取失败: "+e;}
}
function clock(){$("clock").textContent=new Date().toLocaleString("zh-CN",{hour12:false});}
tick();setInterval(tick,5000);setInterval(clock,1000);clock();
</script>
</body></html>"""


class H(BaseHTTPRequestHandler):
    def _send(self, body, ctype="application/json; charset=utf-8", code=200):
        b = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(HTML, "text/html; charset=utf-8")
        elif path == "/api/overview":
            try:
                self._send(json.dumps(collect(), ensure_ascii=False))
            except Exception as e:
                self._send(json.dumps({"error": repr(e)}, ensure_ascii=False), code=500)
        else:
            self._send("not found", "text/plain; charset=utf-8", 404)

    def log_message(self, *a):
        pass


def main():
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), H)
    print("Soul console on http://0.0.0.0:%d/" % PORT)
    srv.serve_forever()


if __name__ == "__main__":
    main()
