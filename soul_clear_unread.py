# -*- coding: utf-8 -*-
"""清「聊天」红点（v2 · 红点视觉定位版）

为什么要这个脚本（2026-09-29 用户实测反馈「聊天红点都没有清理完」）：
  Soul 聊天列表的红点把**系统卡片**也算未读——典型是
    「[快来看]重拾一下你们的记忆吧～」、互动推送（type=35）、官方号提醒。
  `soul_im.pending()` 的判据是「该会话最后一条**真人**消息是不是她发的」，
  系统卡片不算她发言 → 这些会话**永远不进待回队列，红点也永远挂着**。
  用户看到红点以为有消息没回，实际没有。本脚本负责把红点清掉。

⛔ 只进入会话 + 返回，**绝不发送任何消息**。

v2 为什么重写（v1 用昵称 OCR 找人 → 5/5 全失败）：
  · 昵称太短（「未」）→ `_hit` 的子串匹配撞上一堆无关文字 → 拒绝返回（防串台设计），必然找不到
  · 列表 150+ 会话，`find()` 默认只翻 6 屏 → 深位置必然漏
  · `_scroll_top()` 的双击回顶在这台 MuMu 上**不生效**（实测连点 10+ 次仍停在 9月25日 区）
  → 改为：**不认名字，认红点**。红点是很稳定的纯色圆 (254,96,99)，直接色彩聚类定位，再点那一行。

用法：
  python soul_clear_unread.py            # 回顶 → 逐屏找红点 → 逐个进入清掉
  python soul_clear_unread.py --dry      # 只列红点位置，不点击
"""
import sys, io, os, time, sqlite3

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
# ⚠️ 2026-09-29 修：模块级 `sys.stdout = io.TextIOWrapper(...)` 会**关掉原 stdout**，
#    导致别人 `import soul_clear_unread` 时后续 print 全挂（ValueError: I/O operation on closed file）。
#    改用 reconfigure —— 只改编码，不动对象。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SHOT = os.path.join(BASE, "wshot.png")
BACK = (62, 131)                 # 左上角返回箭头（⛔ 不用 keyevent 4）2026-09-30 ×1.25 重标定
SCREEN = (900, 1600)             # 逻辑坐标空间（OCR/input 用，实测 wm size = 900x1600）
BADGE_X_MIN = 600                # 红点在右侧；❤️ 心形在名字后(~x310) → 用 x>=600 排除
BADGE_Y_MAX = 1100               # 排除底部导航角标 + y≈1121 的固定红色控件
# ⚠️ 2026-09-29 从 1150 降到 1130：导航「聊天」角标的上沿会压到 1150 这条线
#    （实测检出 y=1124, n=351 —— 那其实是**导航角标**，不是会话行红点 → 会白跑一趟）
# 🔴 2026-09-29 再降到 1100（真实假阳性事故）：
#    正式跑到「屏3~屏14」时，**每屏都在 y=1121 x=619 n=255 检出 1 个"红点"** ——
#    它不随列表滚动而移动 ⇒ 是**固定 overlay 控件**（会话页该位置是输入框上方的「礼物」按钮，
#    列表页同位置也有一个红色元素），根本不是会话行的未读红点。
#    后果：每屏白点一次 tap(180,1121) 进入某个会话再返回，脚本却报「✅已清」→
#    "清了 18 个红点"里有 12 个是假的，且浪费 ~40 秒。→ 下限收到 1100。
BADGE_RGB = (200, 140, 140, 80)  # r>200, g<140, b<140, r-g>80


def _red(p):
    lim_r, lim_g, lim_b, lim_d = BADGE_RGB
    r, g, b = p
    return r > lim_r and g < lim_g and b < lim_b and (r - g) > lim_d


def badges(path=SHOT):
    """从截图里找红色角标，返回**逻辑坐标** [{'x','y','n'}]（按 y 升序）。"""
    from PIL import Image
    im = Image.open(path).convert("RGB")
    W, H = im.size
    sx, sy = SCREEN[0] / float(W), SCREEN[1] / float(H)
    px = im.load()
    pts = []
    for yp in range(int(150 / sy), min(H, int(BADGE_Y_MAX / sy))):
        for xp in range(int(BADGE_X_MIN / sx), W):
            if _red(px[xp, yp]):
                pts.append((xp, yp))
    clusters = []
    for xp, yp in pts:
        for c in clusters:
            if abs(c["xp"] - xp) < 45 and abs(c["yp"] - yp) < 45:
                c["n"] += 1
                c["xp"] += (xp - c["xp"]) / c["n"]
                c["yp"] += (yp - c["yp"]) / c["n"]
                break
        else:
            clusters.append({"xp": float(xp), "yp": float(yp), "n": 1})
    out = [{"x": c["xp"] * sx, "y": c["yp"] * sy, "n": c["n"]}
           for c in clusters if c["n"] >= 25]
    out.sort(key=lambda z: z["y"])
    return out


def _thumb_hash(path=SHOT):
    """降采样灰度指纹，用来判断"列表有没有真的滚动"（截图有噪点，不能逐像素比）。"""
    from PIL import Image
    im = Image.open(path).convert("L").resize((32, 64))
    return list(im.getdata())


def _changed(a, b, tol=2.5):
    if a is None or b is None:
        return True
    diff = sum(abs(x - y) for x, y in zip(a, b)) / float(len(a))
    return diff > tol


def scroll_to_top(max_try=40):
    """向上翻到列表顶：连点底部「聊天」不生效 → 改用手势，直到画面不再变化。"""
    import soul
    soul.ensure_foreground()
    prev, still = None, 0
    for i in range(max_try):
        soul.ensure_foreground()
        soul.swipe_down()
        time.sleep(0.45)
        soul.screenshot()
        h = _thumb_hash()
        if _changed(prev, h):
            still = 0
        else:
            still += 1
            if still >= 2:
                print(f"  已到顶（第 {i + 1} 次上滑后画面不再变化）")
                return True
        prev = h
    print(f"  ⚠️ 翻了 {max_try} 次仍未确认到顶，继续按当前屏处理")
    return False


def main(dry=False):
    import soul
    import soul_reply as R

    b = badges()
    if b:
        print("当前屏红点：")
        for z in b:
            print(f"  y={z['y']:.0f} x={z['x']:.0f} n={z['n']}")
    else:
        print("当前屏无红点")

    if dry:
        return

    print("=" * 62)
    scroll_to_top()
    print("=" * 62)

    cleared = 0
    for page in range(14):                        # 最多扫 14 屏
        soul.ensure_foreground()
        soul.screenshot()
        bs = badges()
        if not bs:
            print(f"[屏{page + 1}] 无红点 → 下翻")
            soul.swipe_up()
            time.sleep(0.9)
            continue
        print(f"[屏{page + 1}] 红点 {len(bs)} 个 → 逐个进入")
        for z in bs:                              # 位置会随进入/返回变化 → 只处理当前屏已检出的
            soul.ensure_foreground()
            soul.tap(180, int(z["y"]))            # 点头像区进会话（避开名字后的心形❤️）
            time.sleep(1.9)
            # 校验：进会话后底导航应消失；没进去就当成误触、原样返回
            try:
                import soul_read as rd
                texts = [t for t, _, _ in rd.items()]
                in_chat = not (("星球" in " ".join(texts)) and ("广场" in " ".join(texts)))
            except Exception as e:
                print(f"    !! 读屏失败: {e!r}")
                in_chat = False
            if not in_chat:
                print(f"    ⚠️ y={z['y']:.0f} 没进入会话（可能误触）→ 返回")
                soul.tap(*BACK)
                time.sleep(1.0)
                continue
            soul.tap(*BACK)
            time.sleep(1.0)
            cleared += 1
            print(f"    ✅ 已进入并返回（清红点）y={z['y']:.0f}")
        soul.screenshot()
        if not badges():
            print(f"  本屏已清空")
        soul.swipe_up()
        time.sleep(0.9)

    print("-" * 62)
    print(f"本轮清了 {cleared} 个红点")
    left = badges()
    print(f"复查：当前屏还剩 {len(left)} 个红点")


if __name__ == "__main__":
    main(dry="--dry" in sys.argv)
