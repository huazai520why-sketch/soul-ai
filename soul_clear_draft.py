# -*- coding: utf-8 -*-
"""清「聊天」列表里残留的输入框草稿（列表行预览显示成 `[草稿] xxx`）

为什么要这个脚本（2026-09-29 真实事故，用户点名过「红点/草稿」）：
  `soul_reply.find_by_search()` 走搜索兜底时，若**输入焦点不在搜索框而在会话输入框**，
  搜索词会被 `input_text` 写进**某个无关会话的输入框**并留成草稿。
  列表行的预览文本随之变成「[草稿] 十月」——后果有两层：
    ① 用户在聊天列表里看到一个莫名其妙的草稿（观感差，像没发出去的消息）；
    ② `soul_reply.find()` 的 `_hit()` 会匹配**预览文本**，
       于是「十月」被定位到这个**别人的会话**上 → 点进去标题对不上 → 被串台闸拦下。
       （拦得对，没有误发；但每次唤醒「十月」都会白跑一趟，属"隐性永久失效"。）

本脚本：回顶 → 逐屏找含「草稿」的列表行 → 进入 → 点输入框 + 清空 → 返回。
⛔ 只清草稿，**绝不发送任何消息**。

用法：
  python soul_clear_draft.py          # 实际清理
  python soul_clear_draft.py --dry    # 只列出含「草稿」的行位置
"""
import sys, os, time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BACK = (50, 105)


def find_drafts():
    """返回当前屏里含「草稿」的文本行 [(text, y)]（逻辑坐标）"""
    import soul_read as rd
    out = []
    for t, _x, y in rd.items():
        if "草稿" in t:
            out.append((t, float(y)))
    out.sort(key=lambda z: z[1])
    return out


def scroll_to_top(max_try=40):
    import soul
    from soul_clear_unread import _thumb_hash, _changed
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
    print(f"  ⚠️ 翻了 {max_try} 次仍未确认到顶")
    return False


def main(dry=False):
    import soul
    import soul_read as rd

    soul.ensure_foreground()
    soul.screenshot()
    cur = find_drafts()
    print("当前屏含「草稿」的行：", [(t, round(y)) for t, y in cur] or "无")

    if dry:
        return

    print("=" * 62)
    scroll_to_top()
    print("=" * 62)

    cleaned = 0
    for page in range(14):
        soul.ensure_foreground()
        soul.screenshot()
        rows = find_drafts()
        if not rows:
            print(f"[屏{page + 1}] 无草稿 → 下翻")
            soul.swipe_up()
            time.sleep(0.9)
            continue

        print(f"[屏{page + 1}] 草稿 {len(rows)} 个 → 逐个清理")
        for text, y in rows:
            soul.ensure_foreground()
            soul.tap(180, int(y))                 # 点头像区进会话
            time.sleep(1.9)

            in_chat = False
            try:
                texts = [t for t, _, _ in rd.items()]
                joined = " ".join(texts)
                in_chat = not (("星球" in joined) and ("广场" in joined))
            except Exception as e:
                print(f"    !! 读屏失败: {e!r}")

            if not in_chat:
                print(f"    ⚠️ y={y:.0f} 没进入会话（可能误触）→ 返回")
                soul.tap(*BACK)
                time.sleep(1.0)
                continue

            # 点输入框聚焦 → 清空（clear_text 只用 DEL 键，不会触发发送）
            soul.tap(*soul.BOX_XY)
            time.sleep(0.6)
            soul.clear_text()
            time.sleep(0.5)
            soul.screenshot()
            left = [t for t, _, yy in rd.items() if yy > 1150]
            print(f"    ✅ 已清空 y={y:.0f} ｜ 入前草稿={text!r} ｜ 输入框区残留={left}")

            soul.tap(*BACK)
            time.sleep(1.0)
            cleaned += 1

        soul.screenshot()
        soul.swipe_up()
        time.sleep(0.9)

        if page >= 3 and cleaned == 0:
            print("  连翻 4 屏无草稿 → 提前收工（列表顶部才有）")
            break

    print("-" * 62)
    print(f"本轮清了 {cleaned} 处草稿")
    soul.screenshot()
    print("复查：当前屏含草稿行 =", [(t, round(y)) for t, y in find_drafts()] or "无")


if __name__ == "__main__":
    main(dry="--dry" in sys.argv)
