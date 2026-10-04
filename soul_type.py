"""ADBKeyboard 中文输入封装（雷电模拟器 + Soul）
用法:
  python soul_type.py "要输入的中文"          # 只输入+校验，不发送
  python soul_type.py "要输入的中文" --send   # 输入+校验通过后点发送
关键点(踩过的坑):
  1) ADBKeyboard 广播只在"它是当前输入法 + 输入框有焦点"时生效 -> 每次先 settings put 强设
  2) emoji/特殊字符走 --es msg 会异常 -> 一律用 ADB_INPUT_B64
  3) 清空后要重新点一次输入框重建 InputConnection，再输入
"""
import subprocess, sys, base64, time, re
import xml.etree.ElementTree as ET

# ⭐ 无黑窗（2026-09-30）：adb.exe 是控制台程序，不带此标志每次调用都闪黑窗。
_NW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ADB = r"D:\leidian\LDPlayer14\adb.exe"
DEV = "127.0.0.1:5555"
IME = "com.android.adbkeyboard/.AdbIME"
PINYIN = "com.android.inputmethod.pinyin/.InputService"
XML_LOCAL = r"E:\soul\ui_tap.xml"

def adb(*args, timeout=40):
    return subprocess.run([ADB, "-s", DEV] + list(args),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, creationflags=_NW)

def ensure_ime():
    adb("shell", "settings", "put", "secure", "enabled_input_methods", f"{PINYIN}:{IME}")
    adb("shell", "settings", "put", "secure", "default_input_method", IME)
    time.sleep(0.8)
    return adb("shell", "settings", "get", "secure", "default_input_method").stdout.strip()

def dump():
    adb("shell", "uiautomator", "dump", "/sdcard/ui.xml")
    adb("pull", "/sdcard/ui.xml", XML_LOCAL)
    return XML_LOCAL

def find_edit(path):
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return None, None
    for n in root.iter("node"):
        if (n.get("resource-id") or "").endswith("et_sendmessage"):
            return n.get("text"), n.get("bounds")
    return None, None

def center(b):
    m = re.findall(r"\d+", b or "")
    if len(m) == 4:
        x1, y1, x2, y2 = map(int, m)
        return (x1 + x2) // 2, (y1 + y2) // 2
    return None

def type_text(text, x=300, y=1200):
    ime = ensure_ime()
    print("当前输入法 =", ime)
    adb("shell", "input", "tap", str(x), str(y)); time.sleep(1.5)
    adb("shell", "am", "broadcast", "-a", "ADB_CLEAR_TEXT"); time.sleep(1.2)
    adb("shell", "input", "tap", str(x), str(y)); time.sleep(1.2)
    b64 = base64.b64encode(text.encode("utf-8")).decode()
    adb("shell", "am", "broadcast", "-a", "ADB_INPUT_B64", "--es", "msg", b64)
    time.sleep(1.5)
    cur, bounds = find_edit(dump())
    return cur, bounds

def find_send_center(path=None):
    """动态找 btn_send 中心坐标；找不到则回退到已知位置。"""
    path = path or dump()
    try:
        root = ET.parse(path).getroot()
    except Exception:
        root = None
    if root is not None:
        for n in root.iter("node"):
            if "btn_send" in (n.get("resource-id") or ""):
                c = center(n.get("bounds"))
                if c:
                    return c
    return (666, 1211)  # 匹配聊天布局的已知回退位置

def send(x=None, y=None):
    if x is None:
        x, y = find_send_center()
        print("发送按钮中心 =", (x, y))
    adb("shell", "input", "tap", str(x), str(y))

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(0)
    text = sys.argv[1]
    do_send = "--send" in sys.argv
    cur, bounds = type_text(text)
    print("输入框实际内容 =", repr(cur))
    ok = (cur or "").strip() == text.strip()
    print("校验 =", "OK" if ok else "MISMATCH")
    if do_send:
        if ok:
            send(); print("已点击发送")
        else:
            print("校验不通过，未发送")
