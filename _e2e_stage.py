# -*- coding: utf-8 -*-
"""真调验证：带【当前关系阶段】后，智囊团仍出 3 条、本地仍出 2 条。"""
import io, sys, time
_OUT = io.StringIO(); sys.stdout = _OUT
sys.path.insert(0, "E:/soul")
import soul_brain as SB
import soul_daemon as D
sys.stdout = _OUT

HIST = [
    {"role": "me", "text": "夜班刚摸了条鱼"},
    {"role": "her", "text": "我也是夜猫子哈哈"},
    {"role": "me", "text": "那你这个点还不睡？"},
    {"role": "her", "text": "睡不着"},
]
import soul_stage as _SG
STAGE = _SG.turn_line("96691646314323844", "小丸子") or "熟悉（41 轮）· 本阶段目标：信息交换 + 情绪共鸣"
assert STAGE and "亲密度" in STAGE, "阶段行没带上 L 档：%r" % STAGE
FAIL = []

# ── 智囊团（真调方舟）──
for tag, her in (("快通道", "在干嘛"), ("深通道", "你是不是喜欢我？")):
    t0 = time.time()
    c = SB.brain_reply(HIST, her, stage_line=STAGE)
    dt = time.time() - t0
    _OUT.write("── 智囊团·%s  她=%r  耗时=%.1fs\n   候选(%s)=%r\n"
               % (tag, her, dt, len(c) if c else 0, c))
    ok = bool(c) and len(c) == 3
    _OUT.write("   %s\n" % ("✅ 3 条" if ok else "❌ 不满 3 条"))
    if not ok:
        FAIL.append("智囊团-" + tag)

# ── 本地（真调 Ollama）──
CAPT = {}
_real_llm = D._llm
def _cap(p, temp=0.85, timeout=180):
    CAPT["p"] = p
    return _real_llm(p, temp=temp, timeout=timeout)
D._llm = _cap
t0 = time.time()
raw, ms = D.gen_reply("在干嘛", HIST, attempt=0, stage_line=STAGE)
dt = time.time() - t0
lines = [l.strip() for l in (raw or "").splitlines() if l.strip()]
_OUT.write("\n── 本地 gen_reply  耗时=%.1fs\n   输出=%r\n   条数=%d\n" % (dt, raw, len(lines)))
_OUT.write("   提示词含阶段行: %s\n" % ("✅" if STAGE in CAPT.get("p", "") else "❌"))
_OUT.write("   提示词含战略铁律: %s\n" % ("✅" if "【我的战略铁律" in CAPT.get("p", "") else "❌"))
ok = len(lines) == 2 and STAGE in CAPT.get("p", "") and "【我的战略铁律" in CAPT.get("p", "")
_OUT.write("   %s\n" % ("✅ 2 条 + 双块都在" if ok else "❌ 异常"))
if not ok:
    FAIL.append("本地")

OUT = _OUT.getvalue() + "\n" + ("全部通过 ✅" if not FAIL else "失败: %s ❌" % FAIL) + "\n"
open("_e2e_stage_out.txt", "w", encoding="utf-8").write(OUT)
sys.exit(1 if FAIL else 0)
