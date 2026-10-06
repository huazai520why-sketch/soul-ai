# -*- coding: utf-8 -*-
"""_rules_test.py — 验证「守则单一数据源」真的打通（用户 2026-10-06 口径）。

覆盖：
  A. 四处提示词 + 案情包 都能 import、都能组合出内容
  B. 关键守则（战略/铁律/爽感/优先级/反面清单/**红线禁语 FORBID**）都到位
  C. **单一数据源**：改 soul_rules 一处 → 6 个站点同时变（这是本任务的验收点）
  D. SYSTEM_DEEP 仍恰好一个 %s，能 % 格式化
  E. soul_direction 引用的 REVERSE/WEAK/ANCHORS 与 soul_rules 同一对象
"""
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAIL = []
N = 0


def ck(name, cond, extra=""):
    global N
    N += 1
    print("%s %s%s" % ("✅" if cond else "❌", name, ("  | " + extra) if extra else ""))
    if not cond:
        FAIL.append(name)


import soul_rules as R
import soul_brain as SB

# ── A. 组装可用 ────────────────────────────────────────────────
ck("A1 PERSONA 非空", bool(R.PERSONA) and len(R.PERSONA) > 400, "%d 字" % len(R.PERSONA))
ck("A2 SYSTEM_FAST 非空", bool(SB.SYSTEM_FAST), "%d 字" % len(SB.SYSTEM_FAST))
ck("A3 SYSTEM_DEEP 非空", bool(SB.SYSTEM_DEEP), "%d 字" % len(SB.SYSTEM_DEEP))
ck("A4 SUMMARY_SYS 非空", bool(SB.SUMMARY_SYS), "%d 字" % len(SB.SUMMARY_SYS))

# ── B. 关键守则到位 ────────────────────────────────────────────
STATIONS = {
    "PERSONA": R.PERSONA,
    "SYSTEM_FAST": SB.SYSTEM_FAST,
    "SYSTEM_DEEP": SB.SYSTEM_DEEP,
    "SUMMARY_SYS": SB.SUMMARY_SYS,
}
# 战略只在 2 处（PERSONA / FAST / DEEP）—— SUMMARY 是裁判，不需要
for k, v in STATIONS.items():
    ck("B-爽感[%s]" % k, "爽" in v and "至少命中 2 个" in v)
    ck("B-红线禁语[%s]" % k, "绝不许说" in v and "我去找你" in v)

# ⚠️ 设计：PRIORITY 是「用点注入」—— 不进 PERSONA，而是各用点按需拼：
#   案情包（_build_case 尾）+ 本地 xiang + FAST/DEEP/SUMMARY，共 5 个用点都各自带上。
ck("B-设计 PERSONA 不含优先级", "回复优先级" not in R.PERSONA,
   "PERSONA 只装身份/战略/铁律/爽感/反面/红线；优先级按用点拼")
for k in ("SYSTEM_FAST", "SYSTEM_DEEP", "SUMMARY_SYS"):
    ck("B-优先级[%s]" % k, "回复优先级" in STATIONS[k])

# 铁律：PERSONA / FAST / DEEP 有；SUMMARY 是裁判，只认红线（FORBID），不需要铁律全文
for k in ("PERSONA", "SYSTEM_FAST", "SYSTEM_DEEP"):
    ck("B-铁律[%s]" % k, "她付出" in STATIONS[k])

ck("B-战略[PERSONA]", "关系战略" in R.PERSONA)
ck("B-战略[FAST]", "关系战略" in SB.SYSTEM_FAST)
ck("B-战略[DEEP]", "关系战略" in SB.SYSTEM_DEEP)
ck("B-反面清单[PERSONA]", "反面清单" in R.PERSONA)
ck("B-三条三动作[FAST]", "三个不同的动作" in SB.SYSTEM_FAST)
ck("B-三条三动作[SUMMARY]", "三个不同的动作" in SB.SUMMARY_SYS)
ck("B-案情包有优先级/思路/反面",
   all(w in SB._build_case(R.PERSONA, [], "x") for w in ("回复优先级", "聊天思路", "反面清单")))

# ── C. 案情包也能拿到（真调 _build_case）────────────────────────
case = SB._build_case(R.PERSONA, [{"role": "her", "text": "在干嘛"}], "在干嘛",
                      stage_line="初识（2 轮 · 我 1 / 她 1）· 本阶段目标：破冰")
for kw in ("【人设】", "【回复优先级", "【聊天思路", "反面清单", "绝不许说", "我去找你",
           "【当前关系阶段", "初识（2 轮"):
    ck("C-案情包含[%s]" % kw, kw in case)

# ── D. SYSTEM_DEEP 恰好一个 %s ─────────────────────────────────
ck("D1 SYSTEM_DEEP 只有一个 %s", SB.SYSTEM_DEEP.count("%s") == 1,
   "count=%d" % SB.SYSTEM_DEEP.count("%s"))
try:
    _d = SB.SYSTEM_DEEP % "狗头军师"
    ck("D2 SYSTEM_DEEP 可格式化", "「狗头军师」专家" in _d)
except Exception as e:
    ck("D2 SYSTEM_DEEP 可格式化", False, repr(e))

# ── E. 单一数据源：改一处 → 6 站点同步 ─────────────────────────
MARK = "ZZ_SINGLE_SOURCE_MARK_ZZ"
_old_strategy = R.STRATEGY
# 直接改模块属性，再重组（模拟「改 soul_rules 一处」）
R.STRATEGY = _old_strategy + MARK
try:
    R.PERSONA = (R.IDENT + R.STRATEGY + R.IRON + R.RHYTHM + R.PUNCH + R.ONE_AT_A_TIME
                 + R.SHORT_TO_REPLY + R.NEG + "\n" + R.FORBID)
    import importlib
    importlib.reload(SB)          # 站点重新拼装（= 重启后自然发生的事）
    ck("E1 PERSONA 同步", MARK in SB.PERSONA)
    ck("E2 SYSTEM_FAST 同步", MARK in SB.SYSTEM_FAST)
    ck("E3 SYSTEM_DEEP 同步", MARK in SB.SYSTEM_DEEP)
    ck("E4 案情包同步",
       MARK in SB._build_case(SB.PERSONA, [{"role": "her", "text": "x"}], "x"))
finally:
    R.STRATEGY = _old_strategy
    R.PERSONA = (R.IDENT + R.STRATEGY + R.IRON + R.RHYTHM + R.PUNCH + R.ONE_AT_A_TIME
                 + R.SHORT_TO_REPLY + R.NEG + "\n" + R.FORBID)
    import importlib
    importlib.reload(SB)
ck("E5 回滚干净", MARK not in SB.PERSONA and MARK not in SB.SYSTEM_FAST)

# ── F. soul_direction 与 soul_rules 同一份名单 ─────────────────
import soul_direction as SD
ck("F1 REVERSE 同一对象", SD.REVERSE == R.REVERSE, "%d 条" % len(SD.REVERSE))
ck("F2 WEAK 同一对象", SD.WEAK == R.WEAK, "%d 条" % len(SD.WEAK))
ck("F3 ANCHORS 与改造前一致", SD.ANCHORS == R.ANCHORS and len(R.ANCHORS) == 15,
   "%d 条" % len(R.ANCHORS))
ck("F4 FORBID 含全部 REVERSE", all(w in R.FORBID for w in R.REVERSE))
ck("F5 FORBID 含全部 WEAK", all(w in R.FORBID for w in R.WEAK))

# ── G. 双开隔离：vm0 与 vm1 都正确 ─────────────────────────────
os.environ["SOUL_VMINDEX"] = "1"
import importlib
importlib.reload(R)
ck("G1 vm1 名字", "阿凯" in R.IDENT and "江华" not in R.IDENT)
ck("G2 vm1 城市", "沈阳" in R.IDENT and "重庆" not in R.IDENT)
ck("G3 vm1 口吻", "沈阳式" in R.PUNCH and "重庆" not in R.PUNCH)
ck("G4 vm1 LEN 口吻", "东北味" in R.LEN and "重庆" not in R.LEN)
ck("G5 vm1 LEN 人设", "阿凯人设" in R.LEN)
os.environ.pop("SOUL_VMINDEX", None)
importlib.reload(R)
ck("G6 vm0 复原", "江华" in R.IDENT and "重庆式" in R.PUNCH and "重庆味" in R.LEN)

# ── H. soul_daemon 的 _who / gen_reply / gen_wake / gen_pick 也走单一数据源 ──
_save = sys.stdout
import soul_daemon as D          # 该模块导入时会接管 stdout，先存后还
sys.stdout = _save
CAP = {}


def _cap_llm(p, temp=0.8):
    CAP["p"] = p
    return ["假稿"]


D._llm = _cap_llm
D._env_now = lambda: ""          # 免网络

ck("H1 _who(reply) 与 R 同源", D._who("reply") == R.IDENT_REPLY, D._who("reply"))
ck("H2 _who(wake) 与 R 同源", D._who("wake") == R.IDENT_WAKE)
ck("H3 _who(pick) 与 R 同源", D._who("pick") == R.IDENT_PICK)

CAP.clear()
D.gen_reply("在干嘛", [{"role": "her", "text": "在干嘛"}], 0, None, "初识（2 轮）")
pr = CAP.get("p", "")
ck("H4 gen_reply 含红线禁语", "绝不许说" in pr and "我去找你" in pr)
ck("H5 gen_reply 含优先级", "回复优先级" in pr)
ck("H6 gen_reply 含阶段", "初识（2 轮）" in pr)

CAP.clear()
D.gen_wake("小丸子", [{"role": "me", "text": "睡没"}], 30)
pw = CAP.get("p", "")
ck("H7 gen_wake 含爽感四要素", "至少命中 2 个" in pw and "有画面" in pw)
ck("H8 gen_wake 含优先级", "回复优先级" in pw)

CAP.clear()
D.gen_pick(["候选甲", "候选乙"], "在干嘛", [{"role": "her", "text": "在干嘛"}])
pp = CAP.get("p", "")
ck("H9 gen_pick 含优先级", "回复优先级" in pp)
ck("H10 gen_pick 含反面清单", "查户口" in pp)

# vm1：连 gen_wake 这种非「reply」站点也不能串味（此前手抄「重庆式」没过 rewrite）
os.environ["SOUL_VMINDEX"] = "1"
importlib.reload(R)
ck("H11 vm1 _who(reply) 换身份", "阿凯" in D._who("reply") and "江华" not in D._who("reply"),
   D._who("reply"))
CAP.clear()
D.gen_wake("小丸子", [{"role": "me", "text": "睡没"}], 30)
pw = CAP.get("p", "")
ck("H12 vm1 gen_wake 不串味",
   "重庆" not in pw and ("沈阳" in pw or "东北味" in pw),
   "「重庆式」应被 rewrite 成「沈阳式」")
os.environ.pop("SOUL_VMINDEX", None)
importlib.reload(R)
importlib.reload(SB)

# ── I. 专家名单单一来源（审计第 1 条）──────────────────────────────
ck("I1 EXPERTS 3 位", len(R.EXPERTS) == 3, "%s" % (R.EXPERT_NAMES,))
ck("I2 EXPERT_NAMES 派生正确",
   R.EXPERT_NAMES == ("Agent Skill 方法论", "狗头军师", "恋爱大师"))
ck("I3 EXPERTS_FAST 三段齐全",
   all(("【专家%d·%s】" % (i + 1, n)) in R.EXPERTS_FAST
       for i, (n, _d) in enumerate(R.EXPERTS)))
ck("I4 SYSTEM_FAST 用了派生名单", all(n in SB.SYSTEM_FAST for n in R.EXPERT_NAMES))
ck("I5 SYSTEM_FAST 人数派生", "召集 3 位专家" in SB.SYSTEM_FAST)
ck("I6 SUMMARY_SYS 人数派生", "3 位专家已分别给出判断" in SB.SYSTEM_FAST if False
   else "3 位专家已分别给出判断" in SB.SUMMARY_SYS)
_old_exp = R.EXPERTS
R.EXPERTS = R.EXPERTS + ((" test ", "占位"),)
R.EXPERTS_FAST = "".join("【专家%d·%s】%s\n" % (i + 1, n, d)
                         for i, (n, d) in enumerate(R.EXPERTS))
importlib.reload(SB)
ck("I7 加第 4 位专家 → SYSTEM_FAST 同步", "召集 4 位专家" in SB.SYSTEM_FAST)
ck("I8 SB 拿的是同一份 EXPERTS", len(SB.R.EXPERTS) == 4)
# EXPERT_NAMES 是**模块加载时**从 EXPERTS 派生的常量：同一进程内改 R.EXPERTS 它不会跟着变
# （正常用法是改文件后重新 import），所以这里只断言「加载态下二者一致」。
R.EXPERTS = _old_exp
R.EXPERTS_FAST = "".join("【专家%d·%s】%s\n" % (i + 1, n, d)
                         for i, (n, d) in enumerate(R.EXPERTS))
importlib.reload(SB)
ck("I9 回滚到 3 位", "召集 3 位专家" in SB.SYSTEM_FAST)
ck("I9b EXPERTS↔EXPERT_NAMES 加载态一致",
   tuple(n for n, _d in R.EXPERTS) == R.EXPERT_NAMES)

# ── J. 死磕检测（审计第 4 条：把白跑的检测接进生成侧）──────────────
_EGGS = ["今天鸡蛋多少钱来着", "鸡蛋我还顺手多带了五个", "明天再给你捎几个鸡蛋吧"]
ck("J1 stuck_hot 抓到鸡蛋", any(w == "鸡蛋" for w, _n in R.stuck_hot(_EGGS)),
   "%s" % R.stuck_hot(_EGGS, min_hits=3)[:2])
_ln = R.stuck_line(_EGGS)
ck("J2 stuck_line 非空且含禁词", bool(_ln) and "鸡蛋" in _ln)
ck("J3 stuck_line 含「必须换话题」", "必须换话题" in _ln)
_lines = [x for x in _ln.splitlines() if x.strip()]
ck("J3b 禁词不重叠（只列「鸡蛋」、不列冗余单字）",
   "「鸡蛋」" in _ln and "「鸡」" not in _ln and "「蛋」" not in _ln,
   _lines[1] if len(_lines) > 1 else "")
ck("J4 不成话题 → 空", R.stuck_line(["在干嘛", "嗯嗯", "哦"]) == "")
ck("J5 不足 min_hits 条 → 空", R.stuck_line(["鸡蛋多少钱"]) == "")
# ⚠️ 锚点词必须排除：转向时本来就要反复给重庆细节，当死磕会误伤补锚点策略
ck("J6 锚点词不误报（重庆/小面）",
   R.stuck_line(["重庆这边有点热", "重庆今天下雨了", "重庆，去吃小面没"]) == "",
   "零锚点 30 人是长期欠账")
ck("J7 虚词不误报（的/了/明天）",
   R.stuck_line(["我明天就要去的了", "我昨天也是的了", "对了后天还是的了"]) == "")

# 案情包 / 本地链路都要真的注入
ck("J8 案情包注入死磕警报",
   "死磕警报" in SB._build_case(SB.PERSONA, [{"role": "her", "text": "x"}], "x",
                                "", _ln))
CAP.clear()
D.gen_reply("嗯", [{"role": "me", "text": t} for t in _EGGS], 0, None, "", _ln)
pj = CAP.get("p", "")
ck("J9 本地链路注入死磕警报", "死磕警报" in pj and "鸡蛋" in pj)
ck("J10 _stuck_line_of 可用", "鸡蛋" in D._stuck_line_of(
    [{"role": "me", "text": t} for t in _EGGS]))
ck("J11 没死磕时注入为空", D._stuck_line_of([{"role": "me", "text": "在吗"}]) == "")

# ── K. 本地链路去重（审计第 5 条）────────────────────────────────
CAP.clear()
D.gen_reply("在干嘛", [{"role": "her", "text": "在干嘛"}], 0, None, "", "")
pk = CAP.get("p", "")
ck("K1 爽感只讲一遍", pk.count("爽感铁律") == 1 and pk.count("四要素") == 0,
   "去重前为 爽感铁律×1 + 四要素×1")
ck("K2 优先级仍在一遍", pk.count("回复优先级") == 1)
ck("K3 红线仍在", "绝不许说" in pk and "我去找你" in pk)
ck("K4 prompt 变短了", len(pk) < 1500, "%d 字（去重前 1543）" % len(pk))
ck("K5 标题已清实现细节", "来自与智囊团同一份文本" not in pk)

print("\n" + "=" * 56)
print("共 %d 项，失败 %d 项" % (N, len(FAIL)))
if FAIL:
    print("失败：\n  - " + "\n  - ".join(FAIL))
    sys.exit(1)
print("全部通过 ✅")
