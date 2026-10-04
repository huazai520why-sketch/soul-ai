# -*- coding: utf-8 -*-
"""Soul 广场**评论**器 —— 填「没人可回」那段空档（2026-09-26 用户要求）

用户口径（2026-09-26）：
  · **只做评论**，点赞/关注/私聊不用花心思；
  · **坐标不是固定的**，每次必须**从 dump 里动态取**（并用截图确认）。

⚠️ 关键修正（推翻此前"广场走不通"的结论）：
  之前失败是**没先点「评论」按钮就直接找输入框** —— 那种状态下根本没有 EditText。
  正确路径：**广场列表 → 点该帖的「评论」→ 进 PostDetailActivity，此时评论框是
  `class=EditText / content-desc="请输入你的评论" / focused=true`，可以写字；
  写完出现 `tvSend`(「发送」) → 点它发出。**

用法:
  python soul_plaza.py browse [n]        # 浏览广场，打印帖子（作者/内容/分类）
  python soul_plaza.py comment <序号> "内容"   # **评论第 n 个帖子**（自动定位框+发送+截图验证）
  python soul_plaza.py run [n] [--auto]  # 连续评论前 n 帖（--auto 用预设话术，否则需人工给词）

退出码: 0 正常 ｜ 1 进不去广场 ｜ 2 找不到评论框
"""
import sys, io, os, re, time, subprocess

# ⭐ 无黑窗（2026-09-30）：调 soul_shot.py 时若解释器是 python.exe 会闪黑窗。
_NW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

sys.path.insert(0, r"E:\soul")
# 用 reconfigure 而不是新建 TextIOWrapper —— 后者会在被替换时 GC 掉并**关闭底层 buffer**，
# 导致 import 本模块的脚本报 "I/O operation on closed file"。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import soul

TAB_SQUARE = (293, 1539)
SHOT = r"E:\soul\shot.png"

# ⛔ 不评：擦边/涉性/约/艳遇、炫富彩礼交易、性别对立骂人、消极自伤
BLOCK = ("约", "艳遇", "bbw", "BBW", "裸", "性", "包养", "彩礼", "嫖", "一夜",
         "私密", "骚", "自杀", "不想活", "去死", "废物", "舔狗", "普信")
# ✅ 高价值：认真找对象/单身求认识（用户原话："找对象正式我的目的啊 为什么不评论呢"）
GOOD = ("找对象", "结婚", "单身", "脱单", "恋爱", "处对象", "认真", "奔现", "交往")


def _p(*a):
    print(*a)
    sys.stdout.flush()


def _nodes():
    """刷新 dump → 从 ui.xml 解析全部节点（**坐标不固定，每次都要重新取**）"""
    soul.dump()
    xml = open(r"E:\soul\ui.xml", encoding="utf-8").read()
    out = []
    for m in re.finditer(r"<node[^>]*>", xml):
        n = m.group(0)
        b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', n)
        if not b:
            continue
        g = lambda p: (re.search(p, n).group(1) if re.search(p, n) else "")
        out.append({
            "rid": g(r'resource-id="([^"]*)"'),
            "cls": g(r'class="([^"]*)"'),
            "text": g(r'text="([^"]*)"'),
            "desc": g(r'content-desc="([^"]*)"'),
            "clickable": g(r'clickable="([^"]*)"') == "true",
            "bounds": tuple(int(v) for v in b.groups()),
        })
    return out


def _center(b):
    return ((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)


def on_square(ns):
    """是否真的在广场页（**必须排除聊天列表** —— 两者都有底部「广场」导航）"""
    texts = [n["text"] for n in ns if n["text"]]
    if any("搜索备注" in t for t in texts):
        return False                                  # 聊天列表的搜索框
    tabs = {t for n in ns if n["bounds"][1] < 200 for t in [n["text"]]}
    return bool(tabs & {"推荐", "重庆"}) and "广场" in texts


def goto_square():
    """回主框架 → 底导航点「广场」，最多试 5 次"""
    soul.connect()
    for _ in range(5):
        ns = _nodes()
        if on_square(ns):
            return True
        soul.tap(*TAB_SQUARE)
        time.sleep(3)
        if on_square(_nodes()):
            return True
        soul.tap_back_arrow()
        time.sleep(1.8)
    return False


def posts():
    """解析广场列表里的帖子：按「关注/已关注」按钮定位每条"""
    ns = _nodes()
    out = []
    for n in ns:
        if n["text"] in ("关注", "已关注") and n["bounds"][0] > 480:
            ay = n["bounds"][1] - 22                       # 作者昵称行
            author = next((m["text"] for m in ns
                           if abs(m["bounds"][1] - ay) < 16 and m["bounds"][0] < 400
                           and m["text"]), "?")
            # 正文节点：x0>100 且在该帖卡片内（x 起点不固定，别写死 >300）
            cand = [m for m in ns
                    if abs(m["bounds"][1] - (ay + 104)) < 34
                    and m["bounds"][0] > 100 and len(m["text"]) > 4]
            cnode = max(cand, key=lambda m: len(m["text"])) if cand else None
            out.append({"author": author, "followed": n["text"] == "已关注",
                        "content": (cnode["text"][:46] if cnode else ""),
                        "tap": (_center(cnode["bounds"]) if cnode else (360, ay + 104))})
    return out


def classify(text):
    s = str(text)
    if any(k in s for k in BLOCK):
        return "⛔跳过"
    if any(k in s for k in GOOD):
        return "⭐高价值"
    return "可评论"


def browse(n=8):
    if not goto_square():
        _p("!! 进不去广场页"); return 1
    ps = posts()
    if not ps:
        _p("（列表里没解析到帖子）")
    for i, p in enumerate(ps[:n], 1):
        _p(f"  {i}. [{classify(p['content'])}] {p['author']:<14} {p['content']}"
           f"{'  (已关注)' if p['followed'] else ''}")
    _p(f"--- 共 {len(ps)} 帖 ---")
    return 0


def _find_comment_edit(ns):
    """返回评论输入框节点（**必须带 focusable 信息**）"""
    for n in ns:
        if "EditText" in n["cls"] and n["bounds"][1] > 1100:
            return n
    for n in ns:
        if ("评论" in n["desc"] or "说点什么" in n["text"]) and n["bounds"][1] > 1100:
            return n
    return None


def _find_comment_box(ns):
    n = _find_comment_edit(ns)
    return _center(n["bounds"]) if n else None


def _focusable_attr(ns):
    """输入框是否可聚焦 —— focusable=false 时**无论怎么点都写不进去**（实测踩坑）"""
    xml = open(r"E:\soul\ui.xml", encoding="utf-8").read()
    for m in re.finditer(r"<node[^>]*>", xml):
        s = m.group(0)
        if "EditText" not in s:
            continue
        b = re.search(r'bounds="\[(\d+),(\d+)\]', s)
        if b and int(b.group(2)) > 1100:
            f = re.search(r'focusable="([^"]*)"', s)
            d = re.search(r'focused="([^"]*)"', s)
            return f.group(1) if f else "?", d.group(1) if d else "?"
    return "?", "?"


def _find_send(ns):
    """动态找发送按钮：优先含 send 的 rid，其次文本「发送」"""
    for n in ns:
        if "send" in n["rid"].lower() and n["bounds"][1] > 1100:
            return _center(n["bounds"])
    for n in ns:
        if n["text"] == "发送" and n["bounds"][0] > 500:
            return _center(n["bounds"])
    return None


def comment(idx, text, shot=True):
    """评论第 idx 个帖子（1-based）。全流程动态定位 + 截图验证。"""
    if not goto_square():
        _p("!! 进不去广场页"); return 1
    ps = posts()
    if not ps or idx > len(ps):
        _p(f"!! 没有第 {idx} 个帖子（共 {len(ps)}）"); return 1
    p = ps[idx - 1]
    kind = classify(p["content"])
    if kind == "⛔跳过":
        _p(f"  [{kind}] {p['author']} {p['content'][:24]} —— 不评")
        return 0
    _p(f"  目标 {idx}. [{kind}] {p['author']} | {p['content'][:34]}")
    ns = _nodes()

    # ① **从广场列表直接点该帖的「评论」按钮**（不能先打开帖子 —— 那样进来的输入框
    #    focusable=false，永远写不进去。2026-09-26 实测踩坑）
    cbtns = sorted([n for n in ns if n["text"] == "评论" and n["bounds"][0] > 560],
                   key=lambda n: n["bounds"][1])
    if not cbtns or idx > len(cbtns):
        _p("  !! 列表上找不到评论按钮"); return 2
    cb = _center(cbtns[idx - 1]["bounds"])
    _p(f"  评论入口: {cb}")
    soul.tap(*cb); time.sleep(4.5)
    ns = _nodes()

    # ② 动态定位输入框（坐标不固定）+ 检查是否可聚焦
    box = _find_comment_box(ns)
    if not box:
        _p("  !! 找不到评论输入框（exit 2）"); return 2
    foc, fed = _focusable_attr(ns)
    _p(f"  评论框: {box}  focusable={foc} focused={fed}")
    if foc == "false":
        _p("  ⚠ 输入框不可聚焦 → 本条放弃（换个帖子重试，不要硬点）")
        return 2

    # ③ 输入（点一下聚焦，写不进去就再点一次重试，最多 3 次）
    soul.ensure_ime()
    b = base64.b64encode(text.encode("utf-8")).decode()
    typed = False
    for attempt in range(3):
        soul.tap(*box)
        time.sleep(1.2 if attempt == 0 else 1.8)
        soul.adb("shell", "am", "broadcast", "-a", "ADB_INPUT_B64", "--es", "msg", b)
        time.sleep(2.0)
        ns = _nodes()
        typed = any(text[:6] in n["text"] for n in ns if n["text"])
        if typed:
            break
    _p(f"  输入校验: {'✅ 已进框' if typed else '⚠ 框内未见到文本'}")
    if not typed:
        return 2
    # ⑤ 动态定位发送
    snd = _find_send(ns)
    if not snd:
        _p("  !! 找不到发送按钮（可能没输进去）"); return 2
    _p(f"  发送按钮: {snd}")
    soul.tap(*snd); time.sleep(2.5)
    # ⑥ 截图 + dump 验证
    if shot:
        subprocess.run([sys.executable, r"E:\soul\soul_shot.py"],
                       capture_output=True, timeout=60, creationflags=_NW)
    ns2 = _nodes()
    still = any(text[:6] in n["text"] for n in ns2 if n["text"])
    _p(f"  发送校验: {'✅ 已发出（框已清空）' if not still else '⚠ 文本仍在框内，可能没发出'}")
    _p(f"  截图: {SHOT}（请用 Read 工具看图确认）")
    return 0


AUTO_LINES = [
    "重庆这天气 說变就变",
    "厂里上班的 刷到这个还挺解压",
    "同款 我也是这么想的",
]


def run(n=3, auto=False):
    if not goto_square():
        _p("!! 进不去广场页"); return 1
    ps = posts()
    for i, p in enumerate(ps[:n], 1):
        if classify(p["content"]) == "⛔跳过":
            _p(f"  {i}. [⛔] 跳过")
            continue
        txt = AUTO_LINES[(i - 1) % len(AUTO_LINES)] if auto else None
        if not txt:
            _p(f"  {i}. 需人工给评论内容，跳过（用 comment {i} \"内容\"）")
            continue
        comment(i, txt)
        goto_square()
    return 0


if __name__ == "__main__":
    a = sys.argv[1:]
    cmd = a[0] if a else "browse"
    if cmd == "browse":
        sys.exit(browse(int(a[1]) if len(a) > 1 else 8))
    elif cmd == "comment":
        sys.exit(comment(int(a[1]) if len(a) > 1 else 1,
                         a[2] if len(a) > 2 else "路过 说句实话"))
    elif cmd == "run":
        sys.exit(run(int(a[1]) if len(a) > 1 else 3, "--auto" in a))
    else:
        print(__doc__)
