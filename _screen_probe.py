# -*- coding: utf-8 -*-
"""副机屏况探针 v5（session1）：搜索页 + 输入昵称后的结果页布局取证。

输出：E:\\soul\\_probe\\1x_*.png + E:\\soul\\_probe\\run5.log
"""
import os
import sys
import time
import traceback

sys.path.insert(0, r"E:\soul")
import soul
import soul_read as rd

OUT = r"E:\soul\_probe"
os.makedirs(OUT, exist_ok=True)
_f = open(os.path.join(OUT, "run5.log"), "w", encoding="utf-8", buffering=1)


def p(*a):
    _f.write(" ".join(str(x) for x in a) + "\n")
    _f.flush()


def dump(tag):
    try:
        png = os.path.join(OUT, tag + ".png")
        soul.screenshot(path=png, force=True)
        p("  png:", png, os.path.getsize(png) if os.path.exists(png) else "NA")
    except Exception as e:
        p("  png err:", repr(e))
    try:
        for t, cx, cy in rd.items():
            p("     y=%-5s x=%-5s %r" % (cy, cx, t))
    except Exception as e:
        p("  ocr err:", repr(e))


def sf(k, fx, fy):
    return (int(round(soul.DEV_W * fx)), int(round(soul.DEV_H * fy)))


try:
    soul.connect()
    soul.calibrate()
    p("VMI=%s DEV=%sx%s" % (soul.VMI, soul.DEV_W, soul.DEV_H))

    for _ in range(6):
        if "ConversationActivity" not in (soul.activity() or "?"):
            break
        p("  停在会话页，等 6s")
        time.sleep(6)

    soul.ensure_foreground()
    soul.tap(*soul.TAB_CHAT)
    time.sleep(2.2)
    p("--- 回聊天列表 act=%s ---" % (soul.activity() or "?"))

    # 打开搜索页
    icon = sf("icon", 0.8148, 0.0719)
    p("tap 放大镜", icon)
    soul.tap(*icon)
    time.sleep(2.6)
    p("--- 搜索页 act=%s ---" % (soul.activity() or "?"))

    # 点输入框并输入
    inp = sf("input", 0.4100, 0.0410)
    p("tap 输入框", inp)
    soul.tap(*inp)
    time.sleep(1.5)
    name = "力挽狂澜的小凶许"
    p("type:", name)
    try:
        ok = soul.type_text(name, click=False)
        p("  type_text ->", ok)
    except Exception as e:
        p("  type_text err:", repr(e))
    time.sleep(3.0)
    p("--- 输入后 act=%s ---" % (soul.activity() or "?"))
    dump("10_results")

    # 顺带看一眼「取消」坐标是否可回列表
    cancel = sf("cancel", 0.9185, 0.0427)
    p("tap 取消", cancel)
    soul.tap(*cancel)
    time.sleep(2.2)
    p("--- 取消后 act=%s ---" % (soul.activity() or "?"))
    dump("11_after_cancel")
except Exception:
    p("FATAL:\n" + traceback.format_exc())
finally:
    _f.close()
