# -*- coding: utf-8 -*-
"""soul_impact.py —— 「全局影响分析」查询工具（**只读**，绝不改任何文件）

用途：agentG（观全局）在执行「改这处代码会不会波及别处」时的一条命令入口。
      回答三类问题：谁依赖我 / 我依赖谁 / 有没有循环依赖与共享状态。

用法：
    python soul_impact.py                # 全库概览：枢纽模块 / 最脆模块 / 循环依赖 / 模块级可变全局
    python soul_impact.py soul_im        # 指定模块：消费者清单 + 自身依赖 + 是否在环上
    python soul_impact.py --json soul_im # 机器可读（供编排器/派活单使用）

设计约束：
    · **只读**：只读 .py 源码做静态分析，不执行、不写生产文件、不碰守护、不碰 git
    · 只做**静态**import 图，不解析动态 import / getattr 调用 —— 那是 agentG 要人工补的部分
"""
import io
import json
import os
import re
import sys
import collections

ROOT = os.path.dirname(os.path.abspath(__file__))
# (被|import) 的只认本项目模块名：soul_* 与 _*（下划线私有工具）
IMP_RE = re.compile(r"^\s*(?:from\s+(soul_\w+|_\w+)\s+import|import\s+(soul_\w+|_\w+))", re.M)
# 模块级可变全局（跨模块共享状态，改读写时机就会静默影响别处）
GVAR_RE = re.compile(r"^([A-Z_][A-Z0-9_]{2,})\s*=\s*(?:\{\}|\[\]|dict\(\)|list\(\)|\{\s*\})", re.M)


def modules():
    out = []
    for f in os.listdir(ROOT):
        if f.endswith(".py") and not f.startswith("_impact"):
            out.append(f[:-3])
    return sorted(out)


def read(mod):
    p = os.path.join(ROOT, mod + ".py")
    try:
        return io.open(p, encoding="utf-8", errors="replace").read()
    except Exception:
        return ""


def build():
    """→ (deps[A]=A依赖的模块集合, rev[B]=依赖B的模块集合, globals[A]=模块级可变全局名)"""
    deps, rev, globs = {}, collections.defaultdict(set), {}
    allsoul = set(modules())
    for m in sorted(allsoul):
        src = read(m)
        d = set()
        for a, b in IMP_RE.findall(src):
            dep = a or b
            if dep and dep != m and dep in allsoul:
                d.add(dep)
        deps[m] = d
        for x in d:
            rev[x].add(m)
        g = GVAR_RE.findall(src)
        if g:
            globs[m] = g
    return deps, rev, globs


def cycles(deps):
    out = []
    for a in sorted(deps):
        for b in deps[a]:
            if b in deps and a in deps[b] and a < b:
                out.append((a, b))
    return out


def summary(deps, rev, globs):
    print("模块总数: %d" % len(deps))
    print("\n== 枢纽模块（被依赖最多 → 改它先数消费者）==")
    for d, users in sorted(rev.items(), key=lambda kv: -len(kv[1]))[:12]:
        if len(users) < 2:
            break
        print("  %-22s 被 %2d 个模块依赖: %s%s"
              % (d, len(users), ", ".join(sorted(users)[:8]),
                 "..." if len(users) > 8 else ""))
    print("\n== 最脆模块（依赖最多 → 它一动连锁最广）==")
    for m, d in sorted(deps.items(), key=lambda kv: -len(kv[1]))[:8]:
        if len(d) < 3:
            break
        print("  %-22s 依赖 %2d 个: %s" % (m, len(d), ", ".join(sorted(d)[:8])))
    print("\n== 循环依赖（改一边必须看另一边）==")
    c = cycles(deps)
    if c:
        for a, b in c:
            print("  ⚠️ %s  <->  %s" % (a, b))
    else:
        print("  （无）")
    print("\n== 模块级可变全局（跨模块共享状态，改读写时机=别处读脏数据）==")
    for m, g in sorted(globs.items()):
        print("  %-22s %s" % (m, ", ".join(g)))


def detail(mod, deps, rev, globs):
    if mod not in deps:
        print("!! 未找到模块 %s" % mod)
        return 1
    users = sorted(rev.get(mod, []))
    print("== %s ==" % mod)
    print("\n[消费者] 谁依赖它（%d 个）—— 改它的签名/语义，这些都要确认：" % len(users))
    for u in users:
        print("  · %s" % u)
    if not users:
        print("  （无）")
    print("\n[自身依赖] 它依赖谁（%d 个）:" % len(deps[mod]))
    for d in sorted(deps[mod]):
        print("  · %s" % d)
    cyc = [b for b in deps[mod] if b in deps and mod in deps[b]]
    print("\n[循环依赖] %s" % (("⚠️ " + ", ".join(sorted(cyc))) if cyc else "（不在环上）"))
    print("\n[模块级可变全局] %s" % (", ".join(globs.get(mod, [])) or "（无）"))
    print("\n[人工补查项] 静态图看不到的，必须由 agentG 自己确认：")
    print("  · 动态 import / getattr 调用 / 跨模块注入的回调")
    print("  · 跨模块缓存对象的读写时机（如 soul_read 的 OCR 复用缓存）")
    print("  · 时序耦合：UI 操作序列、soul_global_lock 释放时机")
    print("  · 数据库 schema / 字段语义变更")
    return 0


def main():
    args = [a for a in sys.argv[1:]]
    as_json = "--json" in args
    mods = [a for a in args if not a.startswith("--")]
    deps, rev, globs = build()
    if mods:
        m = mods[0]
        if as_json:
            print(json.dumps({
                "module": m,
                "consumers": sorted(rev.get(m, [])),
                "depends_on": sorted(deps.get(m, [])),
                "cycles": sorted(b for b in deps.get(m, []) if b in deps and m in deps[b]),
                "globals": globs.get(m, []),
            }, ensure_ascii=False, indent=1))
            return 0
        return detail(m, deps, rev, globs)
    summary(deps, rev, globs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
