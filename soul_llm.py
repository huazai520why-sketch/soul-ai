# -*- coding: utf-8 -*-
"""
Soul 本地模型调用助手（本机 Ollama）

用法：
  python soul_llm.py expert  "Soul 底部聊天 tab 坐标是多少？"
  python soul_llm.py persona "对方说：发张照片看看呗"
  python soul_llm.py persona "对方说：你好呀" --n 3      # 生成 3 条
  echo "对方说：在干嘛" | python soul_llm.py persona -    # 从 stdin 读

远程调用（副机 → 本机）：把本机 Ollama 设为监听 0.0.0.0，然后
  set OLLAMA_HOST=192.168.10.210:11434
  python soul_llm.py expert "问题"
"""
import json, urllib.request, sys, os

HOST = os.environ.get("OLLAMA_HOST", "127.0.0.1:11434")
# 服务端监听地址 0.0.0.0 不能当客户端目标，换成回环
HOST = HOST.replace("0.0.0.0", "127.0.0.1")
if not HOST.startswith("http"):
    HOST = "http://" + HOST
if ":" not in HOST.split("//")[-1]:
    HOST = HOST + ":11434"
API = HOST.rstrip("/") + "/api/generate"

MODELS = {"expert": "soul-expert", "persona": "jianghua"}

# 对外硬口径：这些词绝不出现在聊天消息里
BAN_WORDS = ["代码", "脚本", "程序", "互联网", "程序员"]

last_dropped = []   # 记录被闸掉的行，便于排查


def clean_persona_lines(text, incoming, n):
    """确定性后处理闸：剔掉①复述对方的话 ②含职业违禁词 的行。"""
    global last_dropped
    last_dropped = []
    inc = (incoming or "").strip().rstrip("？?。.!！~～")
    out = []
    for raw in text.splitlines():
        ln = raw.strip(' \t．.。、,，!！?？~～"“”\'`-*#')
        if not ln:
            continue
        if inc and (ln == inc or ln in inc or (len(ln) >= 4 and inc in ln)):
            last_dropped.append(("复述", ln)); continue
        hit = [b for b in BAN_WORDS if b in ln]
        if hit:
            last_dropped.append(("违禁词%s" % hit, ln)); continue
        out.append(ln)
    return out[: (n or 3)]


def ask(kind, prompt, n=None):
    if kind == "persona":
        if n:
            p = ("下面是对方发来的消息（**这是对方说的，不要复述、不要当成你要发的内容**）：\n"
                 "「%s」\n\n你是江华。请直接输出你要回复她的 %d 条消息：每行一条，≤20 字，"
                 "不要编号、不要解释、不要复述她的话、不要编造人设里没有的人和事。"
                 % (prompt, n))
        else:
            p = prompt
        opt = {"temperature": 0.8}
    else:
        p = prompt
        opt = {"temperature": 0.3}
    body = json.dumps({"model": MODELS[kind], "prompt": p, "stream": False,
                       "think": False, "options": opt}).encode()
    req = urllib.request.Request(API, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        raw = json.loads(r.read()).get("response", "").strip()
    if kind == "persona" and n:
        lines = clean_persona_lines(raw, prompt, n)
        if last_dropped:
            sys.stderr.write("[闸] 剔掉 %d 行: %s\n" % (len(last_dropped), last_dropped))
        return "\n".join(lines)
    return raw


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(0)
    kind, text = sys.argv[1], sys.argv[2]
    if text == "-":
        text = sys.stdin.read().strip()
    n = None
    if "--n" in sys.argv:
        n = int(sys.argv[sys.argv.index("--n") + 1])
    if kind not in MODELS:
        print("kind 只能是 expert / persona"); sys.exit(1)
    print(ask(kind, text, n))
