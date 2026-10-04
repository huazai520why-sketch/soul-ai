# -*- coding: utf-8 -*-
"""session1 现场探针：定位 tap(聊天tab) 失效根因。
输出 E:\\soul\\_probe_navy\\out.log
"""
import os, sys, time, traceback
sys.path.insert(0, r"E:\soul")
import soul
import soul_read as rd
import soul_reply as rp              # ⚠️ 故意在 calibrate 之前 import，复现 daemon 的时序
from PIL import Image

OUT = r"E:\soul\_probe_navy"
os.makedirs(OUT, exist_ok=True)
_f = open(os.path.join(OUT, "out.log"), "w", encoding="utf-8", buffering=1)


def p(*a):
    _f.write(" ".join(str(x) for x in a) + "\n")
    _f.flush()


def snap(tag):
    png = os.path.join(OUT, tag + ".png")
    try:
        soul.screenshot(path=png, force=True)
        sz = os.path.getsize(png) if os.path.exists(png) else -1
        im = Image.open(png)
        p("   [png] %s size=%s dim=%sx%s" % (png, sz, im.size[0], im.size[1]))
        return png
    except Exception as e:
        p("   [png] ERR", repr(e))
        return None


def nav_report():
    try:
        items = rd.items()
    except Exception as e:
        items = []
        p("   OCR ERR", repr(e))
    bot = [(t, cx, cy) for t, cx, cy in items if cy > 860]
    for t, cx, cy in sorted(bot, key=lambda z: z[2]):
        p("     nav y=%s x=%s %r" % (cy, cx, t))
    return items


def tabcols():
    for tab in ("星球", "广场", "聊天", "自己"):
        p("     _tab_color(%s)=%s sel=%s" % (tab, rp._tab_color(tab), rp._tab_sel(tab)))


def state(label):
    try:
        act = soul.activity()
    except Exception as e:
        act = "ERR %r" % (e,)
    p("  [%s] activity=%r on_main=%s" % (label, act, soul.on_main()))
    p("  [%s] _page_state=%r _on_chat_list=%s"
      % (label, rp._page_state(), rp._on_chat_list()))


def do_tap(label, x, y):
    try:
        r = soul.tap(x, y)
    except Exception as e:
        r = "ERR %r" % (e,)
    p("  TAP %s (%s,%s) -> %r" % (label, x, y, r))
    return r


try:
    p("== import 时（calibrate 之前）==")
    p("  soul.DEV=%sx%s  soul.TAB_CHAT=%s" % (soul.DEV_W, soul.DEV_H, soul.TAB_CHAT))
    p("  soul_reply.CHAT_TAB=%s" % (rp.CHAT_TAB,))

    soul.connect()
    dw, dh = soul.calibrate()
    p("== calibrate 之后 ==")
    p("  calibrate() -> %sx%s" % (dw, dh))
    p("  soul.DEV=(%s,%s) soul.TAB_CHAT=%s soul.TAB_SQUARE=%s soul.TAB_ME=%s"
      % (soul.DEV_W, soul.DEV_H, soul.TAB_CHAT, soul.TAB_SQUARE, soul.TAB_ME))
    p("  >>> soul_reply.CHAT_TAB=%s   （是否仍为旧值？）" % (rp.CHAT_TAB,))
    p("  display(refresh)=%r display()=%r" % (soul.display(refresh=True), soul.display()))
    p("  VMI=%s VMINDEX=%s" % (soul.VMI, soul.VMINDEX))
    p("  rd.dev_size=%s rd._shot_size=%s" % (rd.dev_size(), rd._shot_size()))

    # 复位到默认页（clear-top 默认回「广场」）
    p("== 复位（am start --activity-clear-top）==")
    p("   " + str(soul.adb("shell", "am", "start", "-n", rp.ACT, "--activity-clear-top")))
    time.sleep(3.0)
    soul.close_kid_popup()
    time.sleep(1.0)
    state("复位后")
    snap("00_reset")
    nav_report()
    tabcols()

    # 对照 A：点「自己」tab —— 验证 tab 点击本身是否通
    do_tap("自己(482,947)", 482, 947)
    time.sleep(2.5)
    state("点自己后")
    snap("01_me")

    # 回广场
    do_tap("广场(164,947)", 164, 947)
    time.sleep(2.5)
    state("点广场后")

    # 主实验：复现 daemon 的调用 —— 用 soul_reply.CHAT_TAB
    do_tap("rp.CHAT_TAB(stale)", rp.CHAT_TAB[0], rp.CHAT_TAB[1])
    time.sleep(2.5)
    state("点 CHAT_TAB(stale) 后")
    snap("02_after_stale")
    tabcols()

    # 对照 B：用 soul.TAB_CHAT（新鲜值）
    do_tap("soul.TAB_CHAT(fresh)", soul.TAB_CHAT[0], soul.TAB_CHAT[1])
    time.sleep(2.5)
    state("点 TAB_CHAT(fresh) 后")
    snap("03_after_fresh")
    tabcols()

    # 对照 C：写死 (376,947)
    do_tap("硬坐标(376,947)", 376, 947)
    time.sleep(2.5)
    state("点(376,947)后")
    tabcols()

    # 底部像素：是否系统导航条遮挡
    p("== 底部像素 ==")
    png = os.path.join(OUT, "04_pixels.png")
    soul.screenshot(path=png, force=True)
    im = Image.open(png).convert("RGB")
    W, H = im.size
    px = im.load()
    p("  png=%sx%s" % (W, H))
    for yy in (900, 920, 935, 940, 945, 948, 951, 954, 956, 958, H - 1):
        row = [px[x, yy] for x in (int(W * 0.1), int(W * 0.31), int(W * 0.50),
                                   int(W * 0.70), int(W * 0.9))]
        p("   y=%s: %s" % (yy, row))
except Exception:
    p("FATAL:\n" + traceback.format_exc())
finally:
    _f.close()
