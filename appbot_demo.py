# -*- coding: utf-8 -*-
"""按 hilbp/app-bot 的思路真机跑一遍（适配 Soul 6.12.0 / 720x1280）。

app-bot 原版：Python + adb + 百度OCR + qingyunke机器人 + 写死 2160x1080 坐标。
本脚本照搬其流程结构（loop_match / loop_replay_bot、is_focused_activity 守卫、ADBKeyBoard 发送），
把坐标/Activity 改成我模拟器的真实值；OCR 用「截图 + 人工 Read」替代（无百度 key），
chatbot 用 LLM 风格短句替代 qingyunke（已废弃）。

用法:
  python appbot_demo.py match          # loop_match：灵魂匹配 + 发一句招呼（真发）
  python appbot_demo.py reply          # loop_replay_bot 前半：进第一条会话 + 截图 conv.png（需 Read 当 OCR）
  python appbot_demo.py send "文本"     # 在当前会话用 ADBKeyBoard 发（app-bot 的 send_message）
  python appbot_demo.py state          # 打印前台 Activity（app-bot 的 is_focused_activity 原语）
  python appbot_demo.py shot           # 截一张 shot.png
"""
import sys, time, base64, subprocess, re, os
import xml.etree.ElementTree as ET
import soul_read as _rd   # 可靠 dump（带重试+成功校验），统一落盘 ui.xml

ADB = r"D:\leidian\LDPlayer14\adb.exe"
DEV = "127.0.0.1:5555"
IME = "com.android.adbkeyboard/.AdbIME"
MAIN = "cn.soulapp.android/.component.startup.main.MainActivity"
DELAY = 1.2

# 720x1280 真实坐标（来自 soul.py / 今日日志）
TAB_PLANET = (78, 1231)
TAB_CHAT = (501, 1263)
MATCH_START = (123, 461)   # 开始匹配按钮中心


def adb(*a, timeout=30):
    cmd = [ADB, "-s", DEV] + [str(x) for x in a]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return (p.stdout or b"").decode("utf-8", "ignore") + (p.stderr or b"").decode("utf-8", "ignore")
    except subprocess.TimeoutExpired:
        return "TIMEOUT"


def connect():
    adb("disconnect"); time.sleep(0.4); adb("connect", DEV); time.sleep(0.4)


def activity():
    out = adb("shell", "dumpsys", "window")
    m = re.search(r"mFocusedWindow=Window\{[^ ]* [^/]+/([^\s\}]+)", out)
    return m.group(1) if m else ""


def is_focused(sub):  # app-bot 的 is_focused_activity：dumpsys 命中即 True
    return sub in activity()


def tap(x, y):
    adb("shell", "input", "tap", str(int(x)), str(int(y))); time.sleep(DELAY)


def center(b):
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", b or "")
    if not m:
        return None
    x1, y1, x2, y2 = map(int, m.groups())
    return ((x1 + x2) // 2, (y1 + y2) // 2)


def dump(path=None):
    # 2026-09-25 修复：旧实现不校验 dump 成败，失败时残留旧 _ui.xml → safe_tap 拿着过期树找元素必然"未找到"
    # （星球页有旋转动画，uiautomator 经常报 could not get idle state）。改用 soul_read.dump_xml。
    return bool(_rd.dump_xml())


def screenshot(path):
    adb("shell", "screencap", "-p", "/sdcard/_shot.png")
    adb("pull", "/sdcard/_shot.png", path)
    return os.path.exists(path)


def ensure_ime():
    adb("shell", "settings", "put", "secure", "default_input_method", IME)
    adb("shell", "settings", "put", "secure", "enabled_input_methods", IME)
    time.sleep(0.3)


def find_input_send():
    # 动态定位输入框 + 发送钮（app-bot 写死坐标，这里用 rid 更稳，仍是「点输入→打字→点发送」同一思路）
    dump()
    ib = sb = None
    if os.path.exists(_rd.XML):
        for n in ET.parse(_rd.XML).getroot().iter("node"):
            rid = n.get("resource-id") or ""
            b = n.get("bounds") or ""
            if "et_sendmessage" in rid and ib is None:
                ib = center(b)
            if "btn_send" in rid and sb is None:
                sb = center(b)
            if "发送" in (n.get("text") or "") and sb is None:
                sb = center(b)
    if ib is None:
        ib = (300, 1211)
    if sb is None:
        sb = (634, 1211)
    return ib, sb


def send_message(text, box=None):
    if page() != "conversation":
        print("!! send_message：当前不在会话页(page=%s)，停手不发" % page()); return False
    if modal_present():
        print("!! send_message：检测到弹窗，停手不发"); return False
    ensure_ime()
    ib, _ = find_input_send()               # 输入框
    box = box or ib
    tap(*box); time.sleep(0.5)
    adb("shell", "am", "broadcast", "-a", "ADB_CLEAR_TEXT"); time.sleep(0.3)
    tap(*box); time.sleep(0.6)
    b = base64.b64encode(text.encode("utf-8")).decode()
    adb("shell", "am", "broadcast", "-a", "ADB_INPUT_B64", "--es", "msg", b); time.sleep(0.8)
    _, sb = find_input_send()               # 发送钮要打完字才出现，必须重新找
    tap(*sb); time.sleep(1.2)
    return True


def open_soul():
    # 轮询：冷启动有开屏页，单次等待不够；陷在会话里时 --activity-clear-top 清栈回主框架
    for _ in range(6):
        a = activity()
        if "startup.main.MainActivity" in a or "MainActivity" in a:
            return True
        adb("shell", "am", "start", "-n", MAIN, "--activity-clear-top")
        time.sleep(2.5)
    return "startup.main.MainActivity" in activity() or "MainActivity" in activity()


def first_conversation_point():
    """聊天列表里第一条会话的点击坐标（dump 取最靠上的真实会话行，排除搜索框/底导航）"""
    dump()
    if not os.path.exists(_rd.XML):
        return None
    EXCLUDE = ("搜索", "聊天", "星球", "广场", "我", "发现", "精选", "消息",
               "新的朋友", "关注", "推荐", "同城")
    best = None
    for n in ET.parse(_rd.XML).getroot().iter("node"):
        t = n.get("text") or ""
        c = center(n.get("bounds"))
        if not c or not t:
            continue
        if "EditText" in (n.get("class") or ""):   # 搜索框是 EditText，绝不误当会话
            continue
        if any(t.startswith(e) or e in t for e in EXCLUDE):
            continue
        y = c[1]
        if 210 <= y <= 1150 and (best is None or y < best[1]):
            best = (c[0], y, t)
    return best


# ===== 防乱点护栏（anti-blind-tap）=====
# 铁律：动作前先确认「我在哪页 + 目标元素真存在」；动作后校验「确实跳到预期页」；
#       一旦出现弹窗/付费/广告遮罩，立刻停手，绝不穿过去乱点。

def _nodes():
    if not dump():
        return []
    return list(ET.parse(_rd.XML).getroot().iter("node"))


def page():
    """语义化判断当前页面（不靠坐标猜）。chat_list / conversation / main_tab / other / blank"""
    ns = _nodes()
    if not ns:
        return "blank"
    bottom = any(("tab_planet" in (n.get("resource-id") or "") or "星球" in (n.get("text") or ""))
                 for n in ns)
    conv = any(("et_sendmessage" in (n.get("resource-id") or "")) or (n.get("text") == "发送")
               for n in ns)
    if conv:
        return "conversation"          # 有输入框 = 聊天/匹配聊天窗
    if bottom:
        return "chat_list"             # 底导航在 + 无输入框 = 聊天列表页（停在聊天 tab）
    return "other"


def modal_present():
    """检测弹窗/付费/广告遮罩；命中返回 (提示文案, 中心点)，否则 None。"""
    keys = ("关闭", "取消", "付费", "充值", "开通", "购买", "会员", "立即购买",
            "充值会员", "开通会员", "广告")
    for n in _nodes():
        t = n.get("text") or ""
        d = n.get("content-desc") or ""
        for k in keys:
            if k in t or k in d:
                return (t or d, center(n.get("bounds")))
    return None


def safe_tap(text=None, rid=None):
    """只在目标元素真实存在时才点它的中心点；找不到就返回 False，绝不盲点坐标。"""
    ns = _nodes()
    target = None
    for n in ns:
        if text and (text in (n.get("text") or "") or text in (n.get("content-desc") or "")):
            target = n; break
        if rid and rid in (n.get("resource-id") or ""):
            target = n; break
    if not target:
        print("!! safe_tap 未找到 text=%s rid=%s → 拒绝盲点" % (text, rid))
        return False
    tap(*center(target.get("bounds")))
    return True


def require_page(expected, nav_to=None):
    """不在 expected 页就先尝试 nav_to() 再校验；仍不就位返回 False（调用方必须停手）。"""
    if page() == expected:
        return True
    if nav_to:
        nav_to(); time.sleep(2.0)
        if page() == expected:
            return True
    return False



# ===== app-bot loop_match（适配版）=====
def loop_match_one(greet="哈喽，在干嘛呢"):
    connect()
    if not open_soul():
        print("!! open_soul 失败"); return False
    print("[match] 前台:", activity())
    tap(*TAB_PLANET); time.sleep(1.5)          # 点星球
    print("[match] 点星球后前台:", activity())
    tap(*MATCH_START); time.sleep(1.0)          # 点开始匹配
    print("[match] 已点开始匹配，等 10s 撮合...")
    time.sleep(10)
    screenshot("match_state.png")
    act = activity()
    print("[match] 截图 match_state.png；当前前台:", act)
    if "Conversation" in act:
        if modal_present():
            print("[match] 匹配聊天窗出现弹窗，停手不发"); return False
        print("[match] 已在聊天窗，直接发招呼")
        send_message(greet)
        print("[match] 已发招呼:", greet)
        return True
    print("[match] 非聊天窗 —— app-bot 思路下需 Read match_state.png 判断（撮合中/对方主页→私聊）")
    return "NEED_READ"


# ===== app-bot loop_replay_bot（适配版，OCR 用截图替代）=====
def loop_replay_open():
    connect()
    if not open_soul():
        print("!! open_soul 失败"); return False
    # 先确认就位聊天列表页（用 rid 点底导航，不靠坐标瞎点）；否则停手
    if not require_page("chat_list", nav_to=lambda: tap(*TAB_CHAT)):
        print("!! 无法就位聊天列表，停手"); return False
    if modal_present():
        print("!! 检测到弹窗，停手不点:", modal_present()); return False
    for _ in range(12):                         # 滚到顶
        adb("shell", "input", "swipe", "360", "400", "360", "1000", "400"); time.sleep(0.3)
    p = first_conversation_point()
    if not p:
        print("[reply] 没找到会话"); return False
    print("[reply] 第一条会话:", p[2], "点击", (p[0], p[1]))
    tap(p[0], p[1]); time.sleep(2.5)
    # 动作后校验：点完必须真进会话页（否则就是点到了搜索/弹窗，绝不继续截图当OCR）
    if require_page("conversation") is False:
        print("!! 点完没进会话（可能误点搜索/弹窗），停手"); return False
    if modal_present():
        print("!! 进会话却碰到弹窗，停手"); return False
    screenshot("conv.png")
    print("[reply] 已进会话并截图 conv.png —— 需 Read 提取对方最后一句（替代百度OCR）")
    print("[reply] 前台:", activity())
    return True


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "match"
    if cmd == "match":
        loop_match_one()
    elif cmd == "reply":
        loop_replay_open()
    elif cmd == "send":
        connect(); send_message(" ".join(sys.argv[2:])); print("[send] done")
    elif cmd == "state":
        connect(); print("activity:", activity())
    elif cmd == "shot":
        connect(); screenshot("shot.png"); print("shot.png")


if __name__ == "__main__":
    main()
