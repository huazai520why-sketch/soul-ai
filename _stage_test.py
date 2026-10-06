# -*- coding: utf-8 -*-
"""阶段注入验证：
A. 口径唯一性：soul_stage / soul_progress / soul_review 三处逐轮数完全一致
B. 注入生效：智囊团 _build_case 与本地 gen_reply 的提示词都含【当前关系阶段】+ 阶段行
C. fail-open：拿不到阶段时不注入、不报错
"""
import io, sys
_OUT = io.StringIO(); sys.stdout = _OUT
sys.path.insert(0, "E:/soul")
import soul_stage as SG
import soul_progress as SP
import soul_review as SR
import soul_brain as SB
import soul_daemon as D
sys.stdout = _OUT

FAIL = []
def check(tag, cond, extra=""):
    _OUT.write(("  ✅ " if cond else "  ❌ ") + tag + ("  " + extra if extra else "") + "\n")
    if not cond: FAIL.append(tag)

# ── A. 三处口径逐轮数一致（0~160，含所有边界）──
_OUT.write("A 口径唯一性（0~160 轮逐点比对 soul_stage / soul_progress / soul_review）\n")
bad = []
for t in range(0, 161):
    a = SG.stage_name(t)
    b = SP.stage_of(t)[0]
    c = SR.stage_of(t)
    if not (a == b == c) or a == "?":
        bad.append((t, a, b, c))
check("A1: 三处逐轮数一致", not bad, ("不一致点=%r" % bad[:5]) if bad else "")
check("A2: STAGES 是同一对象", SP.STAGES is SG.STAGES)
check("A3: soul_review 表由 soul_stage 派生",
      [tuple(x) for x in SR.TURNS_STAGES] == [(lo, hi, nm) for lo, hi, nm, _ in SG.STAGES])
# 关键边界
for t, want in ((0, "初识"), (29, "初识"), (30, "熟悉"), (49, "熟悉"),
                (50, "推进"), (99, "推进"), (100, "暧昧"), (9999, "暧昧")):
    check("A4: %d 轮 → %s" % (t, want), SG.stage_name(t) == want)

# ── B. 注入生效 ──
_OUT.write("\nB 提示词注入\n")
SID = "96691646314323844"
line = D._stage_line_of(SID)
_OUT.write("   真实 sid 阶段行: %r\n" % line)
check("B1: 真实 sid 能取到阶段行", bool(line) and "本阶段目标" in line)
check("B2: 与 soul_stage 同源", line == SG.turn_line(SID))

FAKE_HIST = [{"role": "her", "text": "在干嘛"}]
case = SB._build_case("人设XYZ", FAKE_HIST, "在干嘛", "熟悉（41 轮）")
check("B3: 智囊团 case 含【当前关系阶段】", "【当前关系阶段" in case)
check("B4: 智囊团 case 含阶段行原文", "熟悉（41 轮）" in case)
case2 = SB._build_case("人设XYZ", FAKE_HIST, "在干嘛", "")
check("B5: 不传阶段行时不出现该块", "【当前关系阶段" not in case2)

# 本地 gen_reply：拦下提示词
CAPT = {}
D._llm = lambda p, temp=0.85, timeout=180: (CAPT.setdefault("p", p), ("装A\n装B", 10))[1]
D._env_now = lambda: "【现在】测试时刻"
D.gen_reply("在干嘛", FAKE_HIST, attempt=0, stage_line="推进（55 轮 · 我 20 / 她 35）· 目标：情绪投资")
p = CAPT.get("p", "")
check("B6: 本地 gen_reply 含【当前关系阶段】", "【当前关系阶段" in p)
check("B7: 本地 gen_reply 含阶段行原文", "推进（55 轮" in p)
check("B8: 本地 gen_reply 仍含【战略铁律】", "【我的战略铁律" in p)
check("B9: 本地 gen_reply 仍含爽感+优先级", ("爽" in p) and ("优先级" in p))

# ── C. fail-open ──
_OUT.write("\nC fail-open\n")
check("C1: _stage_line_of(None) == ''", D._stage_line_of(None) == "")
check("C2: _stage_line_of('乱码') == ''", D._stage_line_of("乱码sid不存在") == "")
check("C3: SG.turn_line('乱码') == ''", SG.turn_line("乱码sid不存在") == "")
check("C4: SG.stage_of('abc') == ('?','')", SG.stage_of("abc") == ("?", ""))
check("C5: SG.turns_of(None,None) 不抛异常", isinstance(SG.turns_of(None, None), dict))

# ── D. 亲密度阶梯 L0~L4 接入 ──
_OUT.write("\nD 亲密度阶梯 L0~L4\n")
import soul_db as SDB
check("D1: levels() 与 soul_db.STAGES 同源", SG.levels() == dict(SDB.STAGES))
check("D2: 5 档齐全", sorted(SG.levels()) == [0, 1, 2, 3, 4])
check("D3: 轮数→L 边界",
      [SG.level_from_turns(t) for t in (0, 14, 15, 29, 30, 49, 50, 99, 100, 500)]
      == [0, 0, 1, 1, 2, 2, 3, 3, 4, 4])
check("D4: 非法轮数 → None", SG.level_from_turns("abc") is None)

# DB 优先：'小丸子' 记录 L0，但给一个派生为 L4 的轮数 → 必须用 DB 的 L0
sm = SDB.stage_map()
if "小丸子" in sm:
    lv, src = SG.level_of("小丸子", 500)
    check("D5: DB 记录优先于轮数派生", lv == int(sm["小丸子"]) and src == "db",
          "db=%s 派生=%s → 取 %s(%s)" % (sm["小丸子"], SG.level_from_turns(500), lv, src))
else:
    check("D5: DB 记录优先于轮数派生（跳过：无样本）", True)
# 无记录 → 派生
lv2, src2 = SG.level_of("这个昵称肯定不在库里_xyz", 60)
check("D6: 无记录 → 按轮数派生", lv2 == 3 and src2 == "auto", "L%s/%s" % (lv2, src2))

ll = SG.level_line("小丸子", 500)
check("D7: level_line 含档位+行动清单", ("L%d" % int(sm.get("小丸子", 0))) in ll and "行动清单" in ll)
check("D8: level_line 含铁律约束", "见面必须她主导" in ll and "绝不自己约" in ll)
check("D9: level_line 含每轮一级规则", "每轮最多推进一级" in ll)

tl = SG.turn_line(SID, "小丸子")
check("D10: turn_line 三行（阶段+L+约束）", len([x for x in tl.splitlines() if x.strip()]) == 3,
      "行数=%d" % len([x for x in tl.splitlines() if x.strip()]))
check("D11: _stage_line_of 与 turn_line 同源", D._stage_line_of(SID, "小丸子") == tl)

OUT = _OUT.getvalue()
OUT += "\n" + ("全部通过 ✅" if not FAIL else "失败项: %s ❌" % FAIL) + "\n"
open("_stage_test_out.txt", "w", encoding="utf-8").write(OUT)
sys.exit(1 if FAIL else 0)
