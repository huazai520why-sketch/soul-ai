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

def _load_ark_key():
    """密钥读取顺序：环境变量 SOUL_ARK_KEY → 本地 .ark_key 文件（gitignored）→ 空串。
    ⚠️ 绝不把密钥写进源码（2026-10-06 安全整改）；空串时云端调用会 401 → 自动降级本地。"""
    k = os.environ.get("SOUL_ARK_KEY")
    if k and k.strip():
        return k.strip()
    kf = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".ark_key")
    try:
        with io.open(kf, encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""

ARK_KEY = _load_ark_key()
ARK_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
# ⭐ 2026-10-06 用户口径「不用指定模型 用 auto 就够了」。
#   实测：`auto` 在方舟**不是合法模型名**（/api/v3 → InvalidEndpointOrModel.NotFound；
#   /api/plan/v3 → UnsupportedModel，那是 Agent Plan 专线、需专用 key）。
#   方舟里等价「不锁版本」的正解 = `doubao-seed-evolving`（自进化别名，实测 200，
#   服务端实际路由到 `doubao-seed-evolving-latest-version`，永远跟随最新）。
#   想要**跨模型自动择优**则是另一回事：去控制台建「智能模型路由」接入点，
#   把 ARK_MODEL 设成 `ep-xxxxx` 即可（无需改代码）。
ARK_MODEL = os.environ.get("SOUL_ARK_MODEL", "doubao-seed-evolving")
# ⭐ 2026-10-06 用户问「多少时间算智囊团失败」→ 把口径显式化，并做成可调：
#   · 快通道（普通消息）：**单次**调用，超时 = BRAIN_TIMEOUT（默认 40s）
#   · 深通道（触发词）：3 专家**并行**(≤BRAIN_TIMEOUT) + 裁判汇总(≤BRAIN_TIMEOUT)
#                        → 最坏约 2×BRAIN_TIMEOUT（默认 ~80s）
#   · 判定「失败」= 超时 / 网络报错 / 返回了但解析不出可用候选（三者任一）
#   改超时：设环境变量 SOUL_BRAIN_TIMEOUT（秒）。实测正常只要 6~7s（思考已关），40s 是天花板。
BRAIN_TIMEOUT = float(os.environ.get("SOUL_BRAIN_TIMEOUT", "40"))

# ⭐ 2026-10-06 用户定稿：「智囊团输出三句就行了」。
#   快通道（SYSTEM_FAST）、裁判（SUMMARY_SYS）、解析（_parse）三处必须一致，
#   否则解析会多抓/少抓（历史上就是因为两边条数不一致出过问题）。
N_CAND = 3

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

# ⭐ 2026-10-06 用户口径「注入提示词让模型别写 / 单一数据源」：
#   守则（身份 / 战略 / 铁律 / 爽感 / 优先级 / 反面清单 / 红线禁语）原先**散在 6 处硬编码**，
#   改一条要同步 3~6 个地方（改「8~12 轮」时就漏过一次 → 模型不知道第几轮，规则等于没写）。
#   现在全部收敛到 `soul_rules`，本文件四处提示词 + `_build_case` 尾块 + `soul_daemon.gen_reply`
#   只做**组合拼装** —— 改一处，全站生效。
import soul_rules as R

# 完整人设（身份 + 战略 + 铁律 + 节奏 + 爽感 + 反面清单 + 红线禁语）。
# ⚠️ `R.*` 各片段**已**过 `soul_persona.rewrite`（见 soul_rules._rw），双开隔离在这一层做完；
#    下面第 150 行左右的 `_spsn.rewrite` 是**幂等保险**（rewrite 的产物都不再是它的 key，
#    再跑一遍必定原样返回）—— 防的是日后有人往 R 里加了没 `_rw` 的片段。
PERSONA = R.PERSONA

SYSTEM_FAST = (
    ("你是「回复智囊团」主持人，召集 %d 位专家对一条消息做一次简短但激烈的讨论，然后收敛成话术。\n"
     % len(R.EXPERTS))
    + R.EXPERTS_FAST
    + R.STRATEGY + "\n"
    + R.IRON + "\n"
    + "【爽感要求·用户 2026-10-06 定稿】" + R.PUNCH_BRIEF
    + R.ONE_AT_A_TIME + R.SHORT_TO_REPLY + R.NEG_BRIEF + "\n"
    + R.PRIORITY + "\n"
    + "讨论要求：观点可以冲突，主持人必须裁决。\n"
    + "【输出格式】讨论概要不超过 3 行；然后必须输出：\n"
    + R.FMT + R.THREE + "\n" + R.LEN + "\n"
    + R.FORBID
)

SYSTEM_DEEP = (
    "你是「%s」专家，正在对一个社交聊天场景做深度分析。\n"
    + R.STRATEGY + "\n"
    + R.IRON + "\n"
    + "收到案情包后输出：\n"
    "1.【判断】她这句的真实意图 + 她当前投入度（低/中/高）\n"
    "2.【策略】怎么让她多付出一步（回复你、主动找你、甚至提出见你）\n"
    "3.【话术】2 条具体话术（<=12 字，符合" + R.IDENT_SHORT + "人设，能钓出她的主动性）\n"
    + "   ⭐ 每条都要让她读起来**爽**：" + R.PUNCH_BRIEF + "\n"
    + "   " + R.PRIORITY + "\n"
    + "只输出以上三节，不要寒暄。\n"
    + R.FORBID
)

SUMMARY_SYS = (
    ("你是裁判。%d 位专家已分别给出判断和话术。请综合他们的观点，吸收最有价值的判断，"
     "然后输出：\n" % len(R.EXPERTS))
    + R.FMT
    + R.LEN + "\n"
    + R.THREE_BRIEF + "\n"
    + "【爽感要求·用户 2026-10-06 定稿】3 条候选都必须让她读起来**爽**——"
    + R.PUNCH_BRIEF
    + R.ONE_AT_A_TIME + R.SHORT_TO_REPLY + R.NEG + "\n"
    + R.PRIORITY + "\n"
    + R.FORBID
)

# ⭐ 2026-10-04 双开人设隔离：实例 N>0 换成账号2 的独立身份（名字/城市/职业/口吻），
#   实例0 原样（rewrite 对 vm0 是恒等函数）→ 账号1 行为逐字节不变。
#   ⭐ 2026-10-06 起：`soul_rules` 各片段已各自 `_rw`，本次 rewrite 退化为**幂等保险**
#   （rewrite 的四个 key 都不是它自己的产物，再跑一遍必定原样返回），保留以兜住
#   日后往 R 里新增的、忘了 `_rw` 的片段。
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


def _build_case(persona, hist, her_msg, stage_line="", stuck_line=""):
    """把「人设 + 全部对话 + 我最近说过 + 她这一句 + 阶段 + 死磕警报 + 守则」拼成案情包。

    stage_line: `soul_stage.turn_line(sid)` 的阶段一行；空串 = 不注入。
    stuck_line: `soul_rules.stuck_line(我最近几句)` 的死磕警报；空串 = 没死磕。
                2026-10-06 用户批准：原先这个检测在**发送闸**里跑、只 print 给后台看，
                **生成侧完全不知道**（等于白跑）；现在回灌到提示词，升级成准硬约束。
    """
    # ⭐ 2026-10-03 用户要求：全部对话上下文（不只近 10 轮）
    ctx = "".join(("我: " if h.get("role") == "me" else "她: ") + h["text"] + "\n" for h in hist)
    # 超长保护：>6000 字截断保留最近部分（只防 API 超限，正常全量）
    if len(ctx) > 6000:
        ctx = "……（更早的对话已省略）……\n" + ctx[-6000:]
    # ⭐ 2026-10-06 用户当场指出「完全没有按照我的聊天思路在走」→ 根因是：
    #   只给了「人设 + 全部历史 + 她这一句」，模型就只做"给这句话换 5 种说法"，
    #   于是出现「她提修机器→5 条全修机器」「她提冬天→5 条全冬天」的死磕。
    #   补上：① 我最近说过的（明令不许重复其话题）② 聊天思路 ③ 反面清单（采访式/干巴巴）。
    _mine = [str(h.get("text") or "") for h in hist if h.get("role") == "me"][-3:]
    mine_s = "".join("我: " + t + "\n" for t in _mine if t.strip())
    # ⭐ 2026-10-06 用户口径「回复要结合当前时间/环境/天气」——给模型一句「现在」。
    #   拿不到就留空（soul_env 自带缓存+fail-open，不会拖慢/报错）。
    try:
        import soul_env as _env
        _now = _env.now_bg()
    except Exception:
        _now = ""
    # ⭐ 2026-10-06 用户追问「8~12 轮是不是太少了」时发现的结构性缺陷：
    #   原来提示词里只有 PERSONA 那句「每 8~12 轮升温一档」，但**没告诉模型现在是第几轮**
    #   → 模型既不知当前进度、也不知此人总共聊了多少轮 ⇒ 该规则无法执行，等于没写；
    #   而阶段判断只活在报告脚本里，生成链路完全不知道阶段。
    #   现在把「当前阶段 + 该阶段目标」显式注入（来源 soul_stage，与两份报告同口径）。
    _stage_blk = ("【当前关系阶段 + 亲密度档位·用户 2026-09-29 / 09-26 定稿，据此判断该不该升温】\n"
                  "%s\n"
                  "（⚠️ 到哪个阶段就做哪个阶段的事：没到不要硬拉，到了就自然往目标走；"
                  "别跳步、别原地踏步。）\n\n" % stage_line) if stage_line else ""
    # ⭐ 2026-10-06 死磕警报（脚本**实测**，不是猜）：紧跟在「我最近说过的几句」后面，
    #   因为它就是指那一节说的；`stuck_line` 自带结尾换行、没死磕时是 ''。
    _stuck_blk = stuck_line if stuck_line else ""
    return (("【人设】%s\n%s\n%s"
             "【你俩的全部对话（按时间顺序）】\n%s\n"
             "【我最近说过的几句——⚠️ 这一轮**不许**再顺着它们的话题说】\n%s\n"
             "%s"
             "【她刚发来的这一句】\n%s\n\n"
             % (persona, _now or "(时间未知)", _stage_blk,
                ctx or "(这是你俩首次对话)\n",
                mine_s or "(还没说过什么)\n", _stuck_blk, her_msg))
            + R.PRIORITY + "\n"
            + R.THINK + "\n"
            + R.NEG)


def _strip_label(t):
    """去掉候选行前面的「话术N：」「1.」「-」等标号；**纯标号占位行整行丢弃**。

    🔴 2026-10-06 根因修复（用户当场指出「完全没有按照我的聊天思路在走」）：
      智囊团明明按新格式输出了 5 条**不同动作**的好候选，例：
        话术1：下班整碗小面去 / 话术2：摆龙门阵还急呀 / 话术3：刚啃了半块凉糕
        话术4：你慌到想我了迈 / 话术5：等哈给你看个好东西
      但解析出来是 **0 条** —— 因为旧 `_parse` 把「以『话术』开头」当黑名单直接丢掉，
      于是一整轮好候选被弃，静默降级到弱提示词的本地模型（又回去死磕「修机器」）。
      现在：先剥掉「话术N：」标号，再判长度，并把「话术/第/条」从黑名单移除（标号已剥）。

    🔴 2026-10-06 二次修复（实盘抓到）：模型有时只输出**光秃秃的「话术1」「话术2」占位行**
      （没冒号、没内容），旧规则要求标号后必须跟标点才剥 → 剥不掉 → 这些占位行被当候选捞进去
      （实盘日志：`3 条=['话术1', '凌晨风凉，吹得脑壳醒', '话术2']`）。
      现在：整行只有标号/序号/符号的，一律返回 "" → 由 `_parse` 丢弃。
    """
    t = str(t or "").strip()
    # 整行就是标号/序号/纯符号（无实质内容）→ 丢弃
    if re.match(r"^(?:话术|候选|第|条)\s*\d*\s*(?:条)?\s*[:：、.．)）]?\s*$", t):
        return ""
    if re.match(r"^\d+\s*[:：、.．)）]?\s*$", t):
        return ""
    if re.match(r"^[-—–*#·•\s]+$", t):
        return ""
    # 「话术1：」「话术 1、」「第1条：」等前缀
    t = re.sub(r"^(?:话术|候选|第)\s*\d*\s*(?:条)?\s*[:：、.．)）]\s*", "", t)
    t = t.lstrip("0123456789.、)）·-—*# ")
    return t.strip("“”\"'「」『』 \t")


def _parse(text):
    """抓 ### 回复候选 后的 N_CAND 行话术；无标记时回退取最后 N_CAND 行短句"""
    if not text:
        return None
    m = re.search(r"###\s*回复候选\s*\n(.*)", text, re.S)
    body = m.group(1) if m else text
    lines = []
    for ln in body.splitlines():
        t = _strip_label(ln)
        if not t or t.startswith("#"):
            continue
        if 0 < len(t) <= 16:
            lines.append(t)
        if len(lines) >= N_CAND:
            break
    if lines:
        return lines[:N_CAND]
    # 回退：无 ### 标记时，从全文取最后 N_CAND 个 <=16 字的非空短句（跳过讨论性长行）
    #   ⚠️ 必须按**原始行**判断标题/小节（`_strip_label` 会把开头的 # 剥掉，
    #      导致 `t.startswith("#")` 失效 → 标题「### 回复候选」会被当成候选捞出来）。
    _HEAD = ("回复候选", "候选", "话术", "讨论概要")
    cand = []
    for ln in reversed(text.splitlines()):
        if ln.strip().startswith(("#", "【")):
            continue
        t = _strip_label(ln)
        if not t or t in _HEAD or t.startswith("【"):
            continue
        if 0 < len(t) <= 16:
            cand.append(t)
        if len(cand) >= N_CAND:
            break
    return cand[:N_CAND] if cand else None


def is_deep(her_msg):
    return any(k in her_msg for k in DEEP_TRIGGER)


def brain_reply(hist, her_msg, persona=PERSONA, deep=None, stage_line="", stuck_line=""):
    """返回 3 条候选话术；失败/无结果返回 None（调用方降级本地模型）

    stage_line: 由 `soul_stage.turn_line(sid)` 生成的「当前阶段」一行摘要；空串 = 不注入。
    stuck_line: 由 `soul_rules.stuck_line(我最近几条)` 生成的「死磕警报」；空串 = 没死磕。
    """
    case = _build_case(persona, hist, her_msg, stage_line, stuck_line)
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

    # ⭐ 2026-10-06 单一数据源：专家名单改引用 `soul_rules.EXPERT_NAMES`
    #   （原先在 SYSTEM_FAST / 这里 / 汇总 zip 三处各抄一份，共 5 份）。
    _names = R.EXPERT_NAMES
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
    joined = "\n\n".join("【%s】\n%s" % (n, t) for n, t in zip(_names, expert_txts))
    txt = _call([
        {"role": "system", "content": SUMMARY_SYS},
        {"role": "user", "content": case + "\n\n【三位专家的分析】\n" + joined},
    ])
    cand = _parse(txt)
    _dbg("[智囊团-深] 3专家+汇总 -> %s条=%s" % (len(cand) if cand else 0, cand))
    return cand
