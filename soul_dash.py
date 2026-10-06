# -*- coding: utf-8 -*-
"""
soul 守护进程可视化监控（独立只读服务，不修改守护进程任何代码）。

读取：
  E:/soul/_uimap/daemon/daemon.0.log   （实时主日志，UTF-8）
  E:/soul/_uimap/daemon/state.0.json   （结构化状态）
  E:/soul/_uimap/daemon/daemon.0.pid   （守护进程 pid）

提供：
  GET /            监控页
  GET /api/snapshot JSON 快照（前端轮询）

用法：python soul_dash.py [port]   （默认 8765）
"""
import os
import re
import sys
import json
import time
import datetime
import subprocess
import http.server
import socketserver

BASE = r"E:/soul"
LOG = os.path.join(BASE, "_uimap", "daemon", "daemon.0.log")
STATE = os.path.join(BASE, "_uimap", "daemon", "state.0.json")
PIDF = os.path.join(BASE, "_uimap", "daemon", "daemon.0.pid")

FORCE_SWITCH_H = 8.0  # 强制切号在线阈值（小时），与 soul_daemon 保持一致

# 阶段识别：从日志尾部（最新→最旧）找第一条命中的，即为“当前流程”
PHASE_RULES = [
    ("切号",   re.compile(r"强制切号|触发切号|已切号|账号轮转|账号跟随|→切号|切号流程")),
    ("奇遇铃", re.compile(r"奇遇铃")),
    ("回复",   re.compile(r"当前阶段|回复·|➡️ 新对话|→新对话|新对话")),
    ("唤醒",   re.compile(r"唤醒老人|唤醒好友|唤醒")),
    ("匹配",   re.compile(r"星球匹配|→匹配|进入匹配|匹配流程|匹配没额度")),
    ("巡检",   re.compile(r"设备就绪|画面健康|空巡检|空间淡检|淡检|空闲等待|静默巡检")),
    ("静默",   re.compile(r"静默|额度用完|quota_out|今日剩余|额度:")),
]
TS_RE = re.compile(r"\[(\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]")


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _parse_ts(line):
    m = TS_RE.search(line)
    if not m:
        return None
    try:
        return datetime.datetime.strptime(
            "%s-%s" % (datetime.datetime.now().year, m.group(1)), "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def _tail_lines(path, max_bytes=90000):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            sz = f.tell()
            f.seek(max(0, sz - max_bytes))
            data = f.read()
        return data.decode("utf-8", "replace").splitlines()
    except Exception:
        return []


def _pid_alive(pid):
    """Windows 上 os.kill(pid,0) 对 pythonw 不可靠，改用 OpenProcess。"""
    if not pid:
        return False
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_INFORMATION = 0x0400
        PROCESS_VM_READ = 0x0010
        h = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        if h:
            kernel32.CloseHandle(h)
            return True
        return False
    except Exception:
        try:
            out = subprocess.run(
                ["tasklist", "/FI", "PID eq %d" % pid],
                capture_output=True, text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)).stdout
            return str(pid) in out
        except Exception:
            return False


def _read_pid():
    try:
        with open(PIDF, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except Exception:
        return None


def _load_state():
    try:
        with open(STATE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _detect_phase(lines):
    """返回 (phase, detail_line, ts_str)。"""
    for line in reversed(lines):
        for name, rx in PHASE_RULES:
            if rx.search(line):
                ts = _parse_ts(line)
                return name, line.strip(), ts.strftime("%m-%d %H:%M:%S") if ts else ""
    return "未知", "", ""


def _detect_account(lines):
    """当前账号：取最新 已切号 行（重置在线计时）或 账号跟随 行。"""
    cur = None
    cur_ts = None
    for line in lines:  # 从头到尾，保留最新
        m = re.search(r"已切号：'([^']*)'→'([^']*)'\((\d+)\)", line)
        if m:
            cur = {"name": m.group(2), "uid": m.group(3)}
            cur_ts = _parse_ts(line)
            continue
        m = re.search(r"当前账号\s*(\d+)", line)
        if m and cur is None:
            cur = {"name": "", "uid": m.group(1)}
            cur_ts = _parse_ts(line)
    if cur is None:
        return {"name": "", "uid": "", "online_sec": None, "to_force_sec": None}
    online_sec = None
    to_force_sec = None
    if cur_ts:
        online_sec = max(0, (datetime.datetime.now() - cur_ts).total_seconds())
        to_force_sec = max(0, FORCE_SWITCH_H * 3600 - online_sec)
    return {"name": cur["name"], "uid": cur["uid"],
            "online_sec": online_sec, "to_force_sec": to_force_sec}


def _detect_pending(lines):
    for line in reversed(lines):
        m = re.search(r"(\d+)\s*个待回", line)
        if m:
            return int(m.group(1))
    return None


def _detect_stage(lines):
    for line in reversed(lines):
        m = re.search(r"当前阶段：([^\n]+)", line)
        if m:
            return m.group(1).strip()
    return ""


def build_snapshot():
    lines = _tail_lines(LOG)
    st = _load_state()
    pid = _read_pid()
    alive = _pid_alive(pid)
    phase, phase_line, phase_ts = _detect_phase(lines)
    acct = _detect_account(lines)
    pending = _detect_pending(lines)
    stage = _detect_stage(lines)

    quota_out_date = st.get("quota_out_date", "")
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    quota_out_today = (quota_out_date == today)

    return {
        "ok": True,
        "now": _now(),
        "daemon": {"pid": pid, "alive": alive},
        "account": acct,
        "quota": {"out_today": quota_out_today, "date": quota_out_date,
                  "quota_out_at": st.get("quota_out_at")},
        "pending": pending,
        "stage": stage,
        "phase": phase,
        "phase_line": phase_line,
        "phase_ts": phase_ts,
        "counters": {
            "send_hour": st.get("send_hour", ""),
            "send_cnt": st.get("send_cnt", 0),
            "fail": st.get("fail", 0),
            "match_empty_streak": st.get("match_empty_streak", 0),
            "match_nav_fail_streak": st.get("match_nav_fail_streak", 0),
            "dot_block_streak": st.get("dot_block_streak", 0),
        },
        "log": lines[-140:],
    }


HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Soul 守护监控</title>
<style>
  :root{
    --bg:#0e1116; --panel:#161b22; --panel2:#1c2330; --bd:#2a3340;
    --fg:#e6edf3; --mut:#8b97a7; --acc:#3fb950; --warn:#d29922; --bad:#f85149;
    --blue:#58a6ff; --pur:#bc8cff; --pink:#ff7b9c;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);
    font-family:-apple-system,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif;}
  .wrap{max-width:1180px;margin:0 auto;padding:14px 16px 40px;}
  header{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:12px;}
  h1{font-size:18px;margin:0;font-weight:700;letter-spacing:.5px}
  .dot{width:10px;height:10px;border-radius:50%;display:inline-block;margin-right:6px}
  .alive{background:var(--acc);box-shadow:0 0 8px var(--acc)}
  .dead{background:var(--bad);box-shadow:0 0 8px var(--bad)}
  .sub{color:var(--mut);font-size:12px}
  .grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:12px}
  @media(max-width:820px){.grid{grid-template-columns:repeat(2,1fr)}}
  .card{background:var(--panel);border:1px solid var(--bd);border-radius:10px;padding:12px 14px}
  .card .k{color:var(--mut);font-size:12px;margin-bottom:6px}
  .card .v{font-size:20px;font-weight:700}
  .card .v.sm{font-size:15px}
  .badge{display:inline-block;padding:2px 10px;border-radius:999px;font-weight:700;font-size:14px}
  .b-匹配{background:rgba(88,166,255,.18);color:var(--blue);border:1px solid var(--blue)}
  .b-唤醒{background:rgba(210,153,34,.18);color:var(--warn);border:1px solid var(--warn)}
  .b-切号{background:rgba(188,140,255,.18);color:var(--pur);border:1px solid var(--pur)}
  .b-奇遇铃{background:rgba(255,123,156,.18);color:var(--pink);border:1px solid var(--pink)}
  .b-回复{background:rgba(63,185,80,.18);color:var(--acc);border:1px solid var(--acc)}
  .b-巡检{background:rgba(139,151,167,.18);color:var(--mut);border:1px solid var(--mut)}
  .b-静默{background:rgba(139,151,167,.18);color:var(--mut);border:1px solid var(--mut)}
  .b-未知{background:rgba(139,151,167,.18);color:var(--mut);border:1px solid var(--mut)}
  .phase-wrap{background:var(--panel);border:1px solid var(--bd);border-radius:10px;
    padding:12px 14px;margin-bottom:12px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
  .phase-wrap .label{color:var(--mut);font-size:13px}
  .phase-line{color:var(--mut);font-size:12px;font-family:ui-monospace,Menlo,Consolas,monospace;
    overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:200px}
  .logpanel{background:#0a0d12;border:1px solid var(--bd);border-radius:10px;overflow:hidden}
  .logpanel .bar{display:flex;justify-content:space-between;align-items:center;
    padding:8px 12px;border-bottom:1px solid var(--bd);color:var(--mut);font-size:12px}
  .log{margin:0;padding:10px 12px;height:54vh;overflow:auto;
    font-family:ui-monospace,Menlo,Consolas,"Courier New",monospace;font-size:12.5px;line-height:1.55}
  .log .ln{white-space:pre-wrap;word-break:break-all;padding:1px 0}
  .c-匹配{color:var(--blue)} .c-唤醒{color:var(--warn)} .c-切号{color:var(--pur)}
  .c-奇遇铃{color:var(--pink)} .c-回复{color:var(--acc)} .c-巡检{color:var(--mut)} .c-静默{color:var(--mut)}
  .err{color:var(--bad)}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1><span id="dot" class="dot dead"></span>Soul 守护监控</h1>
    <span class="sub" id="now"></span>
    <span class="sub" id="pid"></span>
  </header>

  <div class="phase-wrap">
    <span class="label">当前流程</span>
    <span id="phase" class="badge b-未知">未知</span>
    <span class="phase-line" id="phase_line"></span>
    <span class="sub" id="phase_ts"></span>
  </div>

  <div class="grid">
    <div class="card"><div class="k">当前账号</div><div class="v sm" id="acct">—</div></div>
    <div class="card"><div class="k">在线时长</div><div class="v" id="online">—</div></div>
    <div class="card"><div class="k">距强制切号(8h)</div><div class="v" id="toforce">—</div></div>
    <div class="card"><div class="k">待回消息</div><div class="v" id="pending">—</div></div>
    <div class="card"><div class="k">匹配额度</div><div class="v sm" id="quota">—</div></div>
    <div class="card"><div class="k">当前关系阶段</div><div class="v sm" id="stage">—</div></div>
    <div class="card"><div class="k">本小时发送</div><div class="v" id="send">0</div></div>
    <div class="card"><div class="k">失败计数</div><div class="v" id="fail">0</div></div>
  </div>

  <div class="logpanel">
    <div class="bar"><span>实时日志 · daemon.0.log</span><span id="logmeta"></span></div>
    <pre class="log" id="log"></pre>
  </div>
</div>

<script>
const PHASE_CLASS = {"匹配":"c-匹配","唤醒":"c-唤醒","切号":"c-切号","奇遇铃":"c-奇遇铃",
  "回复":"c-回复","巡检":"c-巡检","静默":"c-静默"};
function fmtSec(s){
  if(s===null||s===undefined) return "—";
  s=Math.max(0,Math.round(s));
  const h=Math.floor(s/3600), m=Math.floor((s%3600)/60), x=s%60;
  return (h>0? h+"h ":"")+m+"m"+(h===0?(" "+x+"s"):"");
}
function esc(t){return t.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");}
function phaseOf(line){
  if(/强制切号|触发切号|已切号|账号轮转|账号跟随|切号/.test(line)) return "切号";
  if(/奇遇铃/.test(line)) return "奇遇铃";
  if(/当前阶段|回复·|新对话/.test(line)) return "回复";
  if(/唤醒老人|唤醒好友|唤醒/.test(line)) return "唤醒";
  if(/星球匹配|匹配/.test(line)) return "匹配";
  if(/设备就绪|画面健康|空巡检|淡检|空闲/.test(line)) return "巡检";
  if(/静默|额度|quota_out/.test(line)) return "静默";
  return "";
}
async function tick(){
  try{
    const r=await fetch("/api/snapshot?_="+Date.now());
    const d=await r.json();
    document.getElementById("now").textContent="更新于 "+d.now;
    const alive=d.daemon&&d.daemon.alive;
    document.getElementById("dot").className="dot "+(alive?"alive":"dead");
    document.getElementById("pid").textContent= d.daemon&&d.daemon.pid ? ("pid "+d.daemon.pid+(alive?" · 运行中":" · 已停止")) : "pid ?";
    const ph=d.phase||"未知";
    const pb=document.getElementById("phase");
    pb.textContent=ph; pb.className="badge b-"+ph;
    document.getElementById("phase_line").textContent=d.phase_line||"";
    document.getElementById("phase_ts").textContent=d.phase_ts||"";
    const a=d.account||{};
    document.getElementById("acct").textContent= (a.name||"")+((a.uid)?" ("+a.uid+")":"");
    document.getElementById("online").textContent= a.online_sec!==null&&a.online_sec!==undefined ? fmtSec(a.online_sec) : "—";
    document.getElementById("toforce").textContent= a.to_force_sec!==null&&a.to_force_sec!==undefined ? fmtSec(a.to_force_sec) : "—";
    document.getElementById("pending").textContent= d.pending===null||d.pending===undefined ? "—" : d.pending;
    const q=d.quota||{};
    document.getElementById("quota").textContent= q.out_today ? "今日已用完(静默)" : "有额度";
    document.getElementById("quota").style.color= q.out_today ? "var(--warn)" : "var(--acc)";
    document.getElementById("stage").textContent= d.stage||"—";
    const c=d.counters||{};
    document.getElementById("send").textContent= c.send_cnt||0;
    document.getElementById("fail").textContent= c.fail||0;
    document.getElementById("fail").style.color= (c.fail||0)>0 ? "var(--bad)":"var(--fg)";
    const log=document.getElementById("log");
    const html=d.log.map(l=>{
      const p=phaseOf(l);
      const cls= p?PHASE_CLASS[p]:"";
      return '<div class="ln '+(cls?cls:"")+'">'+esc(l)+'</div>';
    }).join("");
    log.innerHTML=html;
    log.scrollTop=log.scrollHeight;
    document.getElementById("logmeta").textContent= d.log.length+" 行 · 自动刷新";
  }catch(e){
    document.getElementById("now").textContent="拉取失败: "+e.message;
  }
}
tick(); setInterval(tick, 2500);
</script>
</body>
</html>
"""


class H(http.server.BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/snapshot"):
            try:
                self._send(200, json.dumps(build_snapshot(), ensure_ascii=False))
            except Exception as e:
                self._send(500, json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        elif self.path in ("/", "/index.html"):
            self._send(200, HTML, "text/html; charset=utf-8")
        else:
            self._send(404, "not found")

    def log_message(self, *a):
        pass


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("127.0.0.1", port), H) as srv:
        print("soul monitor on http://127.0.0.1:%d" % port)
        srv.serve_forever()


if __name__ == "__main__":
    main()
