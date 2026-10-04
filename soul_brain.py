# -*- coding: utf-8 -*-
"""soul_brain.py — 在线智囊团：三系辩论 → 3 条候选话术（火山方舟）
2026-10-03 用户方案：
  本地模型(人设+上下文) -> 在线模型三系激烈讨论 -> 3 条话术 -> 本地挑一条发送
  系1：GitHub 头部 AI Agent Skill 方法论（结构化决策：意图->策略->风险->验证）
  系2：狗头军师（先接情绪->关系分析->可执行策略->分支预判）
  系3：恋爱大师（情绪价值->推拉节奏->框架感->具体话术）
混合模式：普通=单请求三系齐发；重要（触发词）=三专家独立深辩+汇总
"""
import io
import json
import os
import re
import time
import urllib.request

ARK_KEY = "REDACTED_ARK_KEY"
ARK_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
ARK_MODEL = "doubao-seed-2-1-pro-260628"   # 火山方舟模型（2026-10-03 官方文档示例；用户 Agent Plan Small 套餐）
BRAIN_TIMEOUT = 40

# ⭐ 2026-10-04：守护用 pythonw 启动，stdout 不存在 → print 全部丢失，
#   导致「在线模型超时静默降级」排查时毫无线索。改写入文件，便于事后核对。
_DBG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "_uimap", "daemon", "brain.log")


def _dbg(msg):
    try:
        d = os.path.dirname(_DBG)
        if not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        with io.open(_DBG, "a", encoding="utf-8", errors="replace") as f:
            f.write("[%s] %s\n" % (time.strftime("%m-%d %H:%M:%S"), msg))
    except Exception:
        pass

DEEP_TRIGGER = ("？", "?", "难过", "生气", "烦", "开心", "想", "喜欢", "爱", "分手",
                "见面", "约", "钱", "工资", "暧昧", "累", "哭", "怎么", "为什么",
                "别", "删", "拉黑", "不聊", "说清楚", "解释", "忙", "睡", "加班")

PERSONA = ("你是江华：男，在厂里上班，穷、不装富、不吹牛，重庆人，说话带点重庆味、"
           "接地气、幽默但不过分，接得住情绪，不查户口，不舔狗，有自己框架。"
           "【关系战略·用户 2026-10-03 定稿】目标四阶段推进：认识更多异性朋友 → 暧昧对象（最低要求）"
           "→ 她主动来线下见面 → 正式在一起。"
           "铁律「她付出、我享受」：追我的是她；暧昧是她来暧昧我，见面是她主导来见我，绝不是我去倒贴。"
           "所有话术要么给情绪价值、要么留钩子，钓她主动找你；"
           "每 8~12 轮升温一档（陌生→熟→暧昧），她热情就顺势推进，她冷淡就撤、晾她，绝不低头讨好。")

SYSTEM_FAST = (
    "你是「回复智囊团」主持人，召集三位专家对一条消息做一次简短但激烈的讨论，然后收敛成话术。\n"
    "【专家1·Agent Skill 方法论】结构化决策：先拆她这句的真实意图（敷衍/试探/晾人/推进/情绪宣泄）-> 判断当前关系阶段 -> 给策略 -> 列出风险（哪句不能说）。\n"
    "【专家2·狗头军师】先接住情绪 -> 分析她的行为模式（欲擒故纵/装矜持/钓系/回避）-> 给可执行动作 -> 预判她可能的两种反应分支。\n"
    "【专家3·恋爱大师】给情绪价值 -> 推拉节奏（有推有拉）-> 保持框架（不舔狗不查户口）-> 出具体话术。\n"
    "【全局战略·用户 2026-10-03 定稿】关系四阶段：认识更多异性 → 暧昧对象（最低要求）→ 她主动来见面 → 在一起。"
    "铁律「她付出我享受」：让她投资这段关系、她主动暧昧、她主导提出见面；"
    "我们只提供情绪价值和钩子，绝不倒贴、不表白、不跪舔。所有候选都要围绕「钓出她的主动性」。\n"
    "讨论要求：观点可以冲突，主持人必须裁决。\n"
    "【输出格式】讨论概要不超过 3 行；然后必须输出：\n"
    "### 回复候选\n"
    "话术1\n"
    "话术2\n"
    "话术3\n"
    "话术4\n"
    "话术5\n"
    "共 5 条，**每条尽量少字（<=12 字最佳，不超过 16 字）**、口语、带重庆味、"
    "符合江华人设（穷不装富、接地气、有框架）、总字数控制在 60 字以内、不要任何解释。"
)

SYSTEM_DEEP = (
    "你是「%s」专家，正在对一个社交聊天场景做深度分析。\n"
    "【关系战略·用户 2026-10-03 定稿，所有判断必须围绕它】\n"
    "四阶段：①认识更多异性 ②成为暧昧对象（最低要求）③她主动来线下见面 ④在一起。\n"
    "原则：她付出我享受——让她投资这段关系；她主动暧昧、她主导来见我。"
    "我们只提供情绪价值和钩子，绝不倒贴、不表白、不查户口、不跪舔。\n"
    "收到案情包后输出：\n"
    "1.【判断】她这句的真实意图 + 她当前投入度（低/中/高）\n"
    "2.【策略】怎么让她多付出一步（回复你、主动找你、甚至提出见你）\n"
    "3.【话术】2 条具体话术（<=12 字，符合江华人设，能钓出她的主动性）\n"
    "只输出以上三节，不要寒暄。"
)

SUMMARY_SYS = (
    "你是裁判。三位专家已分别给出判断和话术。请综合三位的观点，吸收最有价值的判断，"
    "然后输出：\n### 回复候选\n话术1\n话术2\n话术3\n话术4\n话术5\n"
    "共 5 条，**每条尽量少字（<=12 字最佳，不超过 16 字）**、口语、带重庆味、"
    "符合江华人设（穷不装富、接地气、有框架）、总字数控制在 60 字以内、不要任何解释。"
)

# ⭐ 2026-10-04 双开人设隔离：实例 N>0 换成账号2 的独立身份（名字/城市/职业/口吻），
#   实例0 原样（rewrite 对 vm0 是恒等函数）→ 账号1 行为逐字节不变。
try:
    import soul_persona as _spsn
    PERSONA = _spsn.rewrite(PERSONA)
    SYSTEM_FAST = _spsn.rewrite(SYSTEM_FAST)
    SYSTEM_DEEP = _spsn.rewrite(SYSTEM_DEEP)
    SUMMARY_SYS = _spsn.rewrite(SUMMARY_SYS)
except Exception:
    pass


def _call(messages, timeout=BRAIN_TIMEOUT):
    body = json.dumps({
        "model": ARK_MODEL,
        "messages": messages,
        "temperature": 0.9,
        "max_tokens": 2048,
        # ⭐ 2026-10-04 关键：本模型是推理型，默认思考 70+s > BRAIN_TIMEOUT(40)
        #   → 每次真实调用超时 → 静默降级本地。关闭思考后实测 6~7s，候选正常。
        "thinking": {"type": "disabled"},
    }).encode("utf-8")
    req = urllib.request.Request(ARK_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + ARK_KEY)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        _dbg("!! 在线模型调用失败: %r" % (e,))
        return None


def _build_case(persona, hist, her_msg):
    # ⭐ 2026-10-03 用户要求：全部对话上下文（不只近 10 轮）
    ctx = "".join(("我: " if h.get("role") == "me" else "她: ") + h["text"] + "\n" for h in hist)
    # 超长保护：>6000 字截断保留最近部分（只防 API 超限，正常全量）
    if len(ctx) > 6000:
        ctx = "……（更早的对话已省略）……\n" + ctx[-6000:]
    return ("【人设】%s\n\n【你俩的全部对话（按时间顺序）】\n%s\n"
            "【她刚发来的这一句】\n%s\n\n" % (persona, ctx or "(这是你俩首次对话)\n", her_msg))


def _parse(text):
    """抓 ### 回复候选 后的 3 行话术；无标记时回退取最后 3 行短句"""
    if not text:
        return None
    m = re.search(r"###\s*回复候选\s*\n(.*)", text, re.S)
    body = m.group(1) if m else text
    lines = []
    for ln in body.splitlines():
        t = ln.strip().lstrip("0123456789.、)）·-— ").strip("“”\"'「」『』")
        if not t or t.startswith("#"):
            continue
        if 0 < len(t) <= 16 and not t.startswith(("第", "话术", "条")):
            lines.append(t)
        if len(lines) >= 5:
            break
    if lines:
        return lines[:5]
    # 回退：无 ### 标记时，从全文取最后 5 个 <=16 字的非空短句（跳过讨论性长行）
    cand = []
    for ln in reversed(text.splitlines()):
        t = ln.strip().lstrip("0123456789.、)）·-— ").strip("“”\"'「」『』")
        if t and 0 < len(t) <= 16 and not t.startswith(("第", "话术", "条", "#", "【")):
            cand.append(t)
        if len(cand) >= 5:
            break
    return cand[:5] if cand else None


def is_deep(her_msg):
    return any(k in her_msg for k in DEEP_TRIGGER)


def brain_reply(hist, her_msg, persona=PERSONA, deep=None):
    """返回 3 条候选话术；失败/无结果返回 None（调用方降级本地模型）"""
    case = _build_case(persona, hist, her_msg)
    if deep is None:
        deep = is_deep(her_msg)
    if not deep:
        txt = _call([
            {"role": "system", "content": SYSTEM_FAST},
            {"role": "user", "content": case},
        ])
        cand = _parse(txt)
        _dbg("[智囊团-快] %s条=%s" % (len(cand) if cand else 0, cand or str(txt)[:160]))
        return cand
    # ⭐ 2026-10-03 三位专家改**并行**（原串行最坏 4×40s=160s，远超超时；
    #   实测日志两次 TimeoutError 导致降级本地模型、话术质量下降）
    from concurrent.futures import ThreadPoolExecutor

    def _ask(nm):
        return _call([
            {"role": "system", "content": SYSTEM_DEEP % nm},
            {"role": "user", "content": case},
        ])

    _names = ("Agent Skill 方法论", "狗头军师", "恋爱大师")
    try:
        with ThreadPoolExecutor(max_workers=3) as _ex:
            _res = list(_ex.map(_ask, _names))
    except Exception as _e:
        _dbg("!! 专家并行调用异常: %r → 退化为串行" % (_e,))
        _res = []
        for nm in _names:
            try:
                _res.append(_ask(nm))
            except Exception:
                _res.append(None)
    expert_txts = [t or "（该专家未输出）" for t in _res]
    joined = "\n\n".join("【%s】\n%s" % (n, t) for n, t in zip(
        ("Agent Skill 方法论", "狗头军师", "恋爱大师"), expert_txts))
    txt = _call([
        {"role": "system", "content": SUMMARY_SYS},
        {"role": "user", "content": case + "\n\n【三位专家的分析】\n" + joined},
    ])
    cand = _parse(txt)
    _dbg("[智囊团-深] 3专家+汇总 -> %s条=%s" % (len(cand) if cand else 0, cand))
    return cand
