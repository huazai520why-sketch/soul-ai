#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""全网话术候选 → 筛选 → 入库（零依赖）。

解决什么：用户要"每天全网找 100 句话术利用起来"。
实际情况：全网搜出来的"撩妹话术"里 **大部分不能用**——土味情话、PUA 套路、文艺金句、
超过 20 字的长句、"在吗/美女"这类零信息量开场，占了绝大多数。
硬凑 100 条塞进库，只会把聊天质量拉低（2026-09-25 已用真实数据证明：AI 味/套路感 = 回复率杀手）。

所以这个脚本的职责是**当筛子，不当搬运工**：宁可只留 12 条真能用的，也不放 100 条垃圾。

用法:
    # 1) 把搜索/网页拿到的候选文本存成文件（一行一条，或整段带序号都行）
    python soul_pick.py raw.txt                 # 只看筛选结果（不写库）
    python soul_pick.py raw.txt --add           # 通过筛选的直接入库（默认类别：网络精选）
    python soul_pick.py raw.txt --add -c 恋爱推进
    echo "候选句子" | python soul_pick.py -      # 从标准输入读

    # 2) 在 Agent 里更常用：搜完直接把要点塞进来
    python soul_pick.py - --add -c 网络精选 <<< "句子1
句子2"

筛选维度（任一不过即拒）:
    ① 长度：3~20 字（比库内素材更严，长句直接扔）
    ② 安全：复用 soul_daily.BLACK（时政/政要/灾难/案件/性别对立…）
    ③ ⛔ 擦边/性暗示：独立硬闸（SEXUAL），**专堵这类，不接受任何放宽**
       —— 用户 2026-09-25 提过"试试暧昧 擦边"，暧昧（NUANMEI 类）已支持，
          擦边**明确不做**：发给陌生人 = 平台判定性骚扰 → 举报封号，
          且与"认真谈恋爱"目标相反。行为层面不是"效果差"，是"不能做"。
    ④ 油腻：宝贝/美女/小姐姐/小仙女/亲爱的/想你了/么么…
    ⑤ PUA：打压/框架/服从/测试她/冷读/投资她/高位低位…
    ⑥ 文艺 AI 味：星河/山海/眼底/温柔了岁月/人间值得/岁月静好/愿你…
    ⑦ 分析式：你签里/我猜你/你是不（用户明确说看了没有回复欲望）
    ⑧ 零信息量：在吗/在不在/你好 这类（"在干嘛"保留，属可用直球）
    ⑨ 与现有素材库去重

「暧昧升温」与「情绪回应」两类**不在本脚本产出**：它们是**接住对方信号**用的，
必须带使用条件（cond），由人按对话情境手工维护在 soul_lines.json 里，
不适合从"全网话术"里批量灌入（批量灌入的那类，正是套路味来源）。
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(HERE, "soul_lines.json")

try:
    from soul_daily import BLACK as _POLITICAL_BLACK
except Exception:                                    # 脚本被单独挪走时兜底
    _POLITICAL_BLACK = []

# ⛔ 擦边 / 性暗示 —— 独立硬闸，2026-09-25 用户提"擦边"后专门加的，无意放宽
# 为什么单列一关：这类句子发给陌生人不是"效果不好"，是**行为层面不能做**——
#   ① 平台判定性骚扰 → 举报 → 封号（号是拿时间养出来的，不是素材库能补的）
#   ② 对方一旦不适 = 直接伤害别人，与"认真谈恋爱"完全相反
#   ③ 今晚已有真实数据：套路味/油腻 = 回复率杀手，擦边只会加速被拉黑
# 写法说明：**用品/短语，不用单字**——避免误伤"早点睡""腿酸""摸鱼"这类正经话。
SEXUAL = ["上床", "床上", "睡过", "一起睡", "陪我睡", "睡你", "同居", "裸", "脱衣",
          "脱了", "开房", "约炮", "情人", "撩骚", "勾引", "性感", "性欲", "性暗示",
          "姿势", "硬了", "湿了", "胸部", "胸大", "屁股", "亲我", "亲一下",
          "吻我", "吻一下", "摸我", "摸你", "摸一下", "抱睡", "穿少", "身体给我",
          "别穿", "内衣", "试试别", "试别的", "试一下别的",
          "照片发我", "照片给我", "发张照", "发个照", "发照片", "发我看看"]
OILY = ["宝贝", "美女", "小姐姐", "小仙女", "亲亲", "抱抱", "亲爱的", "想你了", "么么",
        "小可爱", "女神", "我的宝", "乖乖", "小笨蛋", "老婆", "媳妇"]
PUA = ["打压", "框架", "服从", "测试她", "冷读", "投资她", "高位", "低位", "推拉",
       "废物测试", "情感操控", "让她倒追", "忽冷忽热"]
ARTY = ["星河", "山海", "眼底", "温柔了岁月", "人间值得", "岁月静好", "愿你", "如风",
        "恰似", "浪漫至死不", "奔赴", "治愈系", "宇宙", "银河", "余生请", "惊鸿",
        "山海皆可平", "所念皆", "烟火", "来日方长"]
ANALYTIC = ["你签里", "你引力签", "我猜你", "你是不", "你是那种", "看你资料", "你这种人"]
EMPTY = ["在吗", "在不在", "你好", "在么", "在嘛", "hello", "hi",
         "睡了吗", "睡了么", "吃了吗", "吃了么", "喝了吗"]
# 明显是网页正文/说明文字的行，不是给女生发的话
NOISE = ["点击", "下载", "收藏", "转发", "关注", "来源于", "原文", "阅读", "责任编辑",
         "免责声明", "版权", "广告", "APP", "网址", "http", "www"]
# 明显是"教学说明"的口吻，不是聊天句
META = ["不要:", "不要发", "禁忌", "核心是", "公式", "要点", "技巧", "避雷", "❌", "✅",
        "记住一半", "万能", "话术核心", "很多男生", "学会", "搞定"]


def norm(s):
    return re.sub(r"\s+", "", (s or "")).strip()


def split_candidates(text):
    """从整段文本里拆出候选句。

    ⚠️ 拆句策略（2026-09-25 修 bug）：
    优先**按行**拆——网页/搜索结果通常一行就是一条话术。
    只有当某一行明显很长（>30 字）时才按句末标点再切，否则会出现
    "紧急提问:夏天续命三件套是什么?" / "我是西瓜、空调、冰可乐" 被切两半的碎句。
    一行里塞多条短句的极端情况（"a。b。c。"）由 >30 字规则覆盖不到，
    所以额外加一条：一行内出现 ≥2 个句末标点**且**总长 >30 才切。
    """
    text = text.replace("\r", "\n")
    text = re.sub(r"^\s*(?:[0-9]{1,2}\s*[、.．)）]|[一二三四五六七八九十]{1,2}\s*[、.．)）])\s*",
                  "", text, flags=re.M)
    parts = []
    for line in text.split("\n"):
        line = line.strip().strip("“”\"'，,、。 ")
        if not line:
            continue
        n_ends = len(re.findall(r"[。！？!?；;]", line))
        if len(line) > 30 and n_ends >= 2:
            for seg in re.split(r"(?<=[。！？!?；;])\s*", line):
                seg = seg.strip().strip("“”\"'，,、。 ")
                if seg:
                    parts.append(seg)
        else:
            parts.append(line)
    return parts


def check(s, existing):
    """返回 (是否可用, 原因)"""
    t = s.strip()
    n = norm(t)
    if len(t) < 3:
        return False, "过短"
    if len(t) > 20:
        return False, "超过20字"
    if any(k in n for k in NOISE):
        return False, "网页噪音"
    if any(k in n for k in META):
        return False, "教学口吻"
    # ⛔ 擦边硬闸放在最前（安全类之后先查它），确保任何情况下都拦得住
    hit = [w for w in SEXUAL if w in n]
    if hit:
        return False, "擦边:" + hit[0]
    hit = [w for w in _POLITICAL_BLACK if w in n]
    if hit:
        return False, "敏感:" + hit[0]
    for name, words in (("油腻", OILY), ("PUA", PUA), ("文艺AI味", ARTY),
                        ("分析式", ANALYTIC)):
        h = [w for w in words if w in n]
        if h:
            return False, "%s:%s" % (name, h[0])
    if n in [norm(e) for e in EMPTY]:
        return False, "零信息量"
    if n in existing:
        return False, "已存在"
    return True, "OK"


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    src = args[0]
    do_add = "--add" in args
    cat = "网络精选"
    if "-c" in args:
        cat = args[args.index("-c") + 1]

    if src == "-":
        raw = sys.stdin.read()
    else:
        if not os.path.exists(src):
            print("!! 文件不存在: %s" % src)
            sys.exit(2)
        with open(src, "r", encoding="utf-8") as f:
            raw = f.read()

    try:
        lib = json.load(open(LIB, encoding="utf-8"))
    except Exception as e:
        print("!! 素材库读取失败: %s" % e)
        sys.exit(3)
    existing = {norm(x["text"]) for x in lib["lines"]}

    cands = split_candidates(raw)
    ok, bad = [], {}
    seen_ok = set()
    for c in cands:
        good, why = check(c, existing)
        if good:
            k = norm(c)
            if k in seen_ok:
                continue
            seen_ok.add(k)
            ok.append(c)
        else:
            key = why.split(":")[0]
            bad[key] = bad.get(key, 0) + 1

    print("== 候选 %d 条 → 可用 %d 条 ==" % (len(cands), len(ok)))
    for s in ok:
        print("  ✓ %s" % s)
    if bad:
        print("-- 被拒统计: " + " | ".join("%s %d" % (k, v) for k, v in
                                          sorted(bad.items(), key=lambda i: -i[1])))
    print("-- 说明：全网话术里绝大多数过不了这八道筛，这是正常的，宁缺毋滥 --")

    if do_add:
        if not ok:
            print("!! 没有可用条目，未写入")
            return
        nid = max(x["id"] for x in lib["lines"]) + 1
        for s in ok:
            lib["lines"].append({"id": nid, "cat": cat, "text": s,
                                 "cond": None, "used": []})
            nid += 1
        cap = lib.get("max_lines", 260)
        if len(lib["lines"]) > cap:
            print("!! 已超上限 %d 条（当前 %d），请先清理低频素材再加"
                  % (cap, len(lib["lines"])))
        json.dump(lib, open(LIB, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("已入库 %d 条到类别「%s」，库内共 %d 条" % (len(ok), cat, len(lib["lines"])))
    else:
        print("(预览模式，未写库；加 --add 入库)")


if __name__ == "__main__":
    main()
