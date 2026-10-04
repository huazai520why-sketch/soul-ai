# -*- coding: utf-8 -*-
"""soul_persona.py — 多实例人设隔离（账号1/账号2 身份分离）

背景（2026-10-04 用户口径：「人设优化一下就行，关键不变」）
--------------------------------------------------------
两个账号在同一台机器上双开跑，如果**人设逐字相同**，平台侧很容易把两个号
关联起来（风控）。所以给实例 N>0 换一套**独立身份**：名字 / 城市 / 职业 / 口吻
全换；而**关系战略与铁律（用户 2026-10-03 定稿）分毫不动**。

铁律
----
  · 实例 0（账号1 江华）：`rewrite()` 原样返回入参、`anchors()` 返回与改造前
    **逐字一致**的锚点集合 → 账号1 行为**逐字节不变**。
  · 实例 N>0：只替换身份词（名字/城市/职业/口吻），战略文本与锚点语义不动。
  · 本模块**不得 import 任何业务模块**（避免循环依赖），只用标准库。
  · 想换账号2 的人设：只改下面 `IDENT1` 一处即可（不用动其它文件）。

环境变量
--------
  SOUL_VMINDEX   实例号，默认 "0"；非法值一律回退 0（fail-safe）。
"""
import os

# ── 实例0 = 账号1（江华）：**只作对照，勿改** ───────────────────────
IDENT0 = {"name": "江华", "city": "重庆", "job": "在厂里上班", "flavor": "重庆味"}

# ── 实例>0 = 账号2：独立身份（名字/城市/职业/口吻全换，穷不装富 等骨架不动）──
IDENT1 = {"name": "阿凯", "city": "沈阳", "job": "在汽修厂修车", "flavor": "东北味"}


def vm_index():
    """当前实例号。非法值回退 0（宁可按老身份跑，也不要串号）。"""
    raw = (os.environ.get("SOUL_VMINDEX") or "").strip()
    if not raw.isdigit():
        return 0
    v = int(raw)
    return v if v > 0 else 0


def ident():
    """当前实例的身份表（实例0 → 江华）。"""
    return IDENT1 if vm_index() else IDENT0


def rewrite(text):
    """按当前实例重写人设提示词。

    实例0 → **原样返回**（保证账号1 的提示词逐字节不变）；
    实例>0 → 只替换身份词：口吻→名字→城市→职业
             （「重庆味」必须先于「重庆」替换，否则会被吞掉）。
    """
    if not text or vm_index() <= 0:
        return text
    a = ident()
    return (text.replace("重庆味", a["flavor"])
                .replace("江华", a["name"])
                .replace("重庆", a["city"])
                .replace("在厂里上班", a["job"]))


# 实例0 的城市锚点词（**与改造前 soul_reply.py 里的字面量逐字一致**）
_ANCHOR0 = {"重庆", "小面", "火锅", "南山", "渝中", "解放碑", "洪崖洞",
            "鹅岭", "山城", "綦江", "江边", "巷子", "龙门阵",
            "南滨路", "轻轨", "凉虾", "十八梯", "朝天门", "观音桥", "磁器口",
            "坡", "梯坎", "江风", "老楼"}

# 实例>0 的城市锚点词（沈阳/东北的地名与特产；用于 soul_reply 的「死磕」排除）
_ANCHOR1 = {"沈阳", "鸡架", "中街", "太原街", "铁西", "浑河", "老四季",
            "北陵", "大东", "于洪", "铁锅", "烧烤", "老雪", "澡堂", "炕",
            "辽篮", "五爱", "故宫", "冻梨", "雪天"}


def anchors():
    """当前实例的城市锚点词集合（实例0 与现网逐字一致）。"""
    return _ANCHOR1 if vm_index() else _ANCHOR0


if __name__ == "__main__":
    try:
        import sys, io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    except Exception:
        pass
    print("SOUL_VMINDEX = %s" % (os.environ.get("SOUL_VMINDEX", "(未设置)")))
    print("vm_index()   = %d" % vm_index())
    print("身份         = %s" % ident())
    print("锚点数       = %d" % len(anchors()))
