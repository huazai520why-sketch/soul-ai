#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Soul 聊天素材库管理（零依赖）。

用法:
    python soul_lines.py draw [N] [类别]   # 抽 N 条候选（默认5），自动跳过 7 天内用过的
    python soul_lines.py mark <id|原文>    # 标记某条今天用过
    python soul_lines.py add "文本" [类别] [条件]  # 追加新素材
    python soul_lines.py stats             # 库统计
    python soul_lines.py cool              # 列出冷却中的素材
    python soul_lines.py reset <id>        # 手动解除某条冷却

设计原则（重要）:
    - 素材只是"候选"，必须按 soul-chat 技能的 v2 方法论 + 人设改写后才能发，
      禁止原样套用长句、禁土味情话、禁 PUA 式开场。
    - 7 天冷却：同一条不会被同一批人反复收到。
"""
import json
import os
import random
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "soul_lines.json")


def _load():
    if not os.path.exists(DB):
        print("!! 素材库不存在: %s" % DB)
        sys.exit(2)
    with open(DB, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(data):
    data["updated"] = datetime.now().strftime("%Y-%m-%d")
    tmp = DB + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DB)


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def _is_cooling(item, days):
    if not item.get("used"):
        return False
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    return max(item["used"]) > cutoff


def cmd_draw(args):
    n = 5
    cat = None
    for a in args:
        if a.isdigit():
            n = int(a)
        else:
            cat = a
    data = _load()
    days = data.get("cooldown_days", 7)
    pool = [x for x in data["lines"] if (cat is None or x["cat"] == cat)]
    if not pool:
        print("!! 没有匹配的素材（类别=%s）" % cat)
        sys.exit(3)
    fresh = [x for x in pool if not _is_cooling(x, days)]
    relaxed = False
    if len(fresh) < n:
        # 池子不够，放宽到全池（显式提示，不静默降级）
        fresh = pool
        relaxed = True
    pick = random.sample(fresh, min(n, len(fresh)))
    if relaxed:
        print("!! 可用素材不足 %d 条，已放宽冷却限制（含近期用过的）" % n)
    print("== 候选素材 %d 条（自 %s）==" % (len(pick), cat or "全部"))
    for x in pick:
        cond = (" [仅:%s]" % x["cond"]) if x.get("cond") else ""
        print("[%d] (%s)%s %s" % (x["id"], x["cat"], cond, x["text"]))
    print("-- 提醒：按 v2 方法论改写后再发，禁止原样套用长句/土味情话 --")
    print("-- 发完执行：python soul_lines.py mark <id> --")


def cmd_mark(args):
    if not args:
        print("!! 用法: mark <id|原文>")
        sys.exit(2)
    key = " ".join(args)
    data = _load()
    hit = None
    if key.isdigit():
        for x in data["lines"]:
            if x["id"] == int(key):
                hit = x
                break
    if hit is None:
        for x in data["lines"]:
            if x["text"] == key:
                hit = x
                break
    if hit is None:
        print("!! 未找到素材: %s" % key)
        sys.exit(3)
    t = _today()
    if t not in hit["used"]:
        hit["used"].append(t)
    hit["used"] = hit["used"][-10:]
    _save(data)
    print("已标记使用 [%d] %s (%s)" % (hit["id"], hit["text"], t))


def cmd_add(args):
    if not args:
        print('!! 用法: add "文本" [类别] [条件]')
        sys.exit(2)
    data = _load()
    text = args[0]
    cat = args[1] if len(args) > 1 else "生活碎片"
    cond = args[2] if len(args) > 2 else None
    for x in data["lines"]:
        if x["text"] == text:
            print("已存在，跳过: %s" % text)
            return
    nid = max(x["id"] for x in data["lines"]) + 1
    data["lines"].append({"id": nid, "cat": cat, "text": text, "cond": cond, "used": []})
    _save(data)
    total = len(data["lines"])
    print("已追加 [%d] (%s) %s  | 库内共 %d 条" % (nid, cat, text, total))
    cap = data.get("max_lines", 200)
    if total > cap:
        print("!! 已超出上限 %d 条，建议清理低频素材" % cap)


def cmd_stats(args):
    data = _load()
    days = data.get("cooldown_days", 7)
    lines = data["lines"]
    cats = {}
    cool = 0
    for x in lines:
        cats[x["cat"]] = cats.get(x["cat"], 0) + 1
        if _is_cooling(x, days):
            cool += 1
    print("库: %s" % DB)
    print("总数: %d 条 | 冷却中(近%d天用过): %d | 可用: %d" % (len(lines), days, cool, len(lines) - cool))
    print("上次更新: %s" % data.get("updated"))
    for k, v in sorted(cats.items(), key=lambda i: -i[1]):
        print("  %-8s %d" % (k, v))


def cmd_cool(args):
    data = _load()
    days = data.get("cooldown_days", 7)
    rows = [x for x in data["lines"] if _is_cooling(x, days)]
    if not rows:
        print("无冷却中素材")
        return
    for x in rows:
        print("[%d] (%s) %s  last=%s" % (x["id"], x["cat"], x["text"], max(x["used"])))


def cmd_reset(args):
    if not args or not args[0].isdigit():
        print("!! 用法: reset <id>")
        sys.exit(2)
    data = _load()
    for x in data["lines"]:
        if x["id"] == int(args[0]):
            x["used"] = []
            _save(data)
            print("已解除冷却 [%d] %s" % (x["id"], x["text"]))
            return
    print("!! 未找到 id=%s" % args[0])
    sys.exit(3)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd, args = sys.argv[1], sys.argv[2:]
    fn = {
        "draw": cmd_draw, "mark": cmd_mark, "add": cmd_add,
        "stats": cmd_stats, "cool": cmd_cool, "reset": cmd_reset,
    }.get(cmd)
    if fn is None:
        print("!! 未知命令: %s" % cmd)
        print(__doc__)
        sys.exit(2)
    fn(args)


if __name__ == "__main__":
    main()
