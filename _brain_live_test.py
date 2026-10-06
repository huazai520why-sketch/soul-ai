# -*- coding: utf-8 -*-
"""真调智囊团：验证 doubao-seed-evolving 下快/深通道都能出 3 条候选。"""
import io, sys, time
_OUT = io.StringIO(); sys.stdout = _OUT
import soul_brain as B
sys.stdout = _OUT

HIST = [
    {"role": "me", "text": "夜班刚摸了条鱼"},
    {"role": "her", "text": "我也是夜猫子哈哈"},
    {"role": "me", "text": "那你这个点还不睡？"},
    {"role": "her", "text": "睡不着"},
]


def run(tag, her):
    t0 = time.time()
    is_deep = B.is_deep(her)
    try:
        c = B.brain_reply(HIST, her)
    except Exception as e:
        c = None
        _OUT.write("  !! 异常 %r\n" % (e,))
    dt = time.time() - t0
    _OUT.write("── %s  她=%r  (deep=%s)  耗时=%.1fs\n" % (tag, her, is_deep, dt))
    _OUT.write("   候选(%s 条)=%r\n" % (len(c) if c else 0, c))
    ok = bool(c) and len(c) == 3
    _OUT.write("   %s\n" % ("✅ 3 条齐" if ok else "❌ 不满 3 条"))
    return ok


a = run("快通道", "在干嘛")
b = run("深通道（触发词）", "你是不是喜欢我？")
c = run("快通道2", "嗯嗯")

_RESULT = "全部通过 ✅" if (a and b and c) else "有失败 ❌"
_OUT.write("\n" + _RESULT + "\n")
_OUT.write("用模型 = %s\n" % B.ARK_MODEL)
open("_brain_live_out.txt", "w", encoding="utf-8").write(_OUT.getvalue())
sys.exit(0 if (a and b and c) else 1)
