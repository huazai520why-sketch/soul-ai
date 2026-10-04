# -*- coding: utf-8 -*-
"""session1 现场探针：定位「发不出消息」的导航根因（VM1 / 账号2）。
只读诊断 + 安全点击（底导航 tab），不碰任何付费按钮。
输出：E:\\soul\\_probe\\navprobe.log 及各步骤 PNG。
"""
import os, sys, time, traceback
os.environ.setdefault("SOUL_VMINDEX", "1")
sys.path.insert(0, r"E:\soul")
OUT = r"E:\soul\_probe"
os.makedirs(OUT, exist_ok=True)
_f = open(os.path.join(OUT, "navprobe.log"), "w", encoding="utf-8", buffering=1)


def p(*a):
    try:
        _f.write(" ".join(str(x) for x in a) + "\n")
        _f.flush()
    except Exception:
        pass


try:
    import soul
    import soul_read as rd
    import soul_reply as rp
    from PIL import Image

    def shot(tag):
        png = os.path.join(OUT, tag + ".png")
        try:
            soul.screenshot(path=png, force=True)
            im = Image.open(png)
            p("   [png] %s %sx%s" % (tag, im.size[0], im.size[1]))
            return png
        except Exception as e:
            p("   [png] ERR %r" % (e,))
            return None

    def tabcols():
        for t in ("星球", "广场", "聊天", "自己"):
            try:
                p("     tab %s color=%s sel=%s" % (t, rp._tab_color(t), rp._tab_sel(t)))
            except Exception as e:
                p("     tab %s ERR %r" % (t, e))

    def state(tag):
        try:
            p("  [%s] activity=%r on_main=%s disp=%r" % (tag, soul.activity(), soul.on_main(), soul.display()))
        except Exception as e:
            p("  [%s] act ERR %r" % (tag, e))
        try:
            p("  [%s] _page_state=%r _on_chat_list=%s" % (tag, rp._page_state(), rp._on_chat_list()))
        except Exception as e:
            p("  [%s] state ERR %r" % (tag, e))

    p("== NAVPROBE START", time.strftime("%Y-%m-%d %H:%M:%S"))
    p("  import-time : soul.DEV=%sx%s soul.TAB_CHAT=%s | rp.CHAT_TAB=%s"
      % (soul.DEV_W, soul.DEV_H, soul.TAB_CHAT, rp.CHAT_TAB))
    soul.connect()
    dw, dh = soul.calibrate()
    p("  calibrate() -> %sx%s" % (dw, dh))
    p("  after-calib : soul.DEV=%sx%s soul.TAB_CHAT=%s | rp.CHAT_TAB=%s"
      % (soul.DEV_W, soul.DEV_H, soul.TAB_CHAT, rp.CHAT_TAB))
    p("  wm size=%r wm density=%r" % (soul.sh("wm size"), soul.sh("wm density")))
    p("  display(refresh)=%r display()=%r" % (soul.display(refresh=True), soul.display()))

    # 确保 Soul 真的起来（冷启动 ~8s）
    p("  ps soul: %r" % (soul.sh("ps -A | grep cn.soulapp")[:200],))
    soul.launch_app(wait=15)
    time.sleep(3)
    p("  after launch_app: activity=%r display=%r" % (soul.activity(), soul.display(refresh=True)))
    soul.ensure_foreground(wait=15)
    # 复位到默认页（clear-top 默认落「广场」）
    p("  reset: " + str(soul.adb("shell", "am", "start", "-n", rp.ACT, "--activity-clear-top")))
    time.sleep(6.0)
    soul.close_kid_popup()
    time.sleep(1.0)
    state("reset")
    shot("00_reset")
    tabcols()

    # 底部像素真值图（非白像素分布）
    try:
        im = Image.open(os.path.join(OUT, "00_reset.png")).convert("RGB")
        px = im.load()
        W, H = im.size
        p("  shot size=%sx%s" % (W, H))
        for yy in (900, 915, 920, 928, 935, 940, 945, 948, 951, 955, 959):
            row = "".join("X" if (px[x, yy][0] < 210 or px[x, yy][1] < 210 or px[x, yy][2] < 210) else "." for x in range(0, W, 5))
            p("   y=%3d |%s|" % (yy, row))
    except Exception as e:
        p("  pixelmap ERR %r" % (e,))

    tests = [
        ("stale_rpCHATTAB", rp.CHAT_TAB),
        ("fresh_soulTABCHAT", soul.TAB_CHAT),
        ("truth_376_947", (376, 947)),
        ("icon_376_920", (376, 920)),
        ("self_482_947", (482, 947)),
        ("square_164_947", (164, 947)),
    ]
    for label, (x, y) in tests:
        before = rp._page_state()
        r = soul.tap(x, y)
        time.sleep(2.2)
        after = rp._page_state()
        p("  TAP %-18s (%s,%s) ret=%r | before=%r -> after=%r" % (label, x, y, r, before, after))
        shot("tap_" + label)
        tabcols()
    state("final")
except Exception:
    p("FATAL:\n" + traceback.format_exc())
finally:
    try:
        _f.close()
    except Exception:
        pass
