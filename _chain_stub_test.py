# -*- coding: utf-8 -*-
"""生成链桩测：新对话=本地层(brain=0)；老对话=只走智囊团(失败不发)。
不碰真机 UI，全量 monkeypatch。"""
import sys, io
_OUT = io.StringIO()
sys.stdout = _OUT
#sys.stderr = _OUT  # 调试期不禁用

import soul_daemon as D
import soul_pregen as PG

sys.stdout = _OUT          # soul_daemon 导入时接管过 stdout，这里抢回来
#sys.stderr = _OUT  # 调试期不禁用

CALLS = {"brain": 0, "pregen": 0, "gen_reply": 0}
HIST = []
PG_RET = []
BRAIN_RET = None
GATE_PASS = True

# ── 桩 ────────────────────────────────────────────────
D._account_gate_ok = lambda: True
D.im = type("X", (), {"has_device_session": staticmethod(lambda uid: True)})()
D._uid_from_sid = lambda s: "uid_1"
D._uid_by_name = lambda n: "uid_1"
D._sid_of = lambda uid: "sid_1"
D._wake_forgive = lambda st, name: None
D.hist_of = lambda sid, limit=24: HIST
D.gen_pick = lambda cand, h, m: cand[0]
D._deliver = lambda name, texts, my_recent=None, allow_chain=False: "SENT"
D._bump_try = lambda st, name, ht: 1
D._spend = lambda st, k=1: None
D._norm_nick = lambda n: n
D._split_msg = lambda t, limit=30: [t]
D._LOGS = []
D.log = lambda m: D._LOGS.append(str(m))


def _gate(raw, inc, n, my_recent=None):
    if not GATE_PASS:
        return [], "剔空"
    t = (raw or "").strip()
    return ([t] if t else []), ""


def _gen_reply(her_msg, hist, attempt=0, banned=None, **kw):
    CALLS["gen_reply"] += 1
    return ("本地稿%d" % (CALLS["gen_reply"]), 12)


def _brain_within(hist, her_text, budget=None, **kw):
    CALLS["brain"] += 1
    return BRAIN_RET


def _pregen_take(name, her_text):
    CALLS["pregen"] += 1
    return list(PG_RET)


D.gate = _gate
D.gen_reply = _gen_reply
D._brain_within = _brain_within
PG.take = _pregen_take


def _reset(her_n, pg=None, brain=None, gate_pass=True):
    global HIST, PG_RET, BRAIN_RET, GATE_PASS
    CALLS.update(brain=0, pregen=0, gen_reply=0)
    D._LOGS = []
    HIST = [{"role": "her", "text": "她%d" % i} for i in range(her_n)]
    HIST += [{"role": "me", "text": "我%d" % i} for i in range(her_n)]
    PG_RET = pg or []
    BRAIN_RET = brain
    GATE_PASS = gate_pass


ST = {"tries": {}, "wake_days": {}}
FAIL = []


def check(tag, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + tag + ("  " + extra if extra else ""))
    if not cond:
        FAIL.append(tag)


# A 新对话 + 预生成池命中 → brain=0 / pregen=1 / gen_reply=0
print("A 新对话(her=1) 预生成池命中 → 应走本地层、不碰智囊团")
_reset(1, pg=["预生成A1", "预生成A2"])
r = D.do_reply("香草派", "在吗", ST, sid_hint="sid_1")
print("   result=%s calls=%s" % (r, CALLS))
check("A: SENT", r == "SENT")
check("A: 智囊团 0 次", CALLS["brain"] == 0)
check("A: 预生成池 1 次", CALLS["pregen"] == 1)
check("A: 本地模型 0 次", CALLS["gen_reply"] == 0)

# B 新对话 + 池空 → 回落本地模型
print("B 新对话(her=2) 池空 → 应回落本地模型、仍不碰智囊团")
_reset(2, pg=[])
r = D.do_reply("香草派", "在吗", ST, sid_hint="sid_1")
print("   result=%s calls=%s" % (r, CALLS))
check("B: SENT", r == "SENT")
check("B: 智囊团 0 次", CALLS["brain"] == 0)
check("B: 预生成池 1 次", CALLS["pregen"] == 1)
check("B: 本地模型 ≥1 次", CALLS["gen_reply"] >= 1)

# C 老对话 + 智囊团出候选 → SENT、不降级
print("C 老对话(her=5) 智囊团出 3 条 → SENT、不走本地")
_reset(5, brain=["候选1", "候选2", "候选3"])
r = D.do_reply("香草派", "嗯", ST, sid_hint="sid_1")
print("   result=%s calls=%s" % (r, CALLS))
check("C: SENT", r == "SENT")
check("C: 智囊团 1 次", CALLS["brain"] == 1)
check("C: 预生成池 0 次", CALLS["pregen"] == 0)
check("C: 本地模型 0 次", CALLS["gen_reply"] == 0)

# D 老对话 + 智囊团拿不到 → SKIP、绝不降级
print("D 老对话(her=6) 智囊团超时/空 → 应 SKIP、不降级本地")
_reset(6, brain=None)
r = D.do_reply("香草派", "嗯", ST, sid_hint="sid_1")
print("   result=%s calls=%s" % (r, CALLS))
check("D: SKIP", r == "SKIP")
check("D: 智囊团 1 次", CALLS["brain"] == 1)
check("D: 预生成池 0 次", CALLS["pregen"] == 0)
check("D: 本地模型 0 次", CALLS["gen_reply"] == 0)
check("D: 日志含『不发』", any("不发" in m for m in D._LOGS))

# E 边界：her=3 → 新；her=4 → 老
print("E 边界 her=3 应判新(本地)；her=4 应判老(智囊团)")
_reset(3, pg=["边界3"])
r = D.do_reply("香草派", "在", ST, sid_hint="sid_1")
e1 = (r == "SENT" and CALLS["brain"] == 0 and CALLS["pregen"] == 1)
check("E1: her=3 → 新对话(本地)", e1, "calls=%s" % CALLS)
_reset(4, brain=["边界4"])
r = D.do_reply("香草派", "在", ST, sid_hint="sid_1")
e2 = (r == "SENT" and CALLS["brain"] == 1 and CALLS["pregen"] == 0)
check("E2: her=4 → 老对话(智囊团)", e2, "calls=%s" % CALLS)

print("\n" + ("全部通过 ✅" if not FAIL else "失败项: %s ❌" % FAIL))
with open("_chain_stub_out.txt", "w", encoding="utf-8") as f:
    f.write(_OUT.getvalue())
sys.exit(1 if FAIL else 0)
