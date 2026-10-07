# -*- coding: utf-8 -*-
"""MuMu 窗口截图（纯标准库 ctypes，零依赖）

MuMu 15 把 App 放在独立虚拟 display（mumuscreenNNN），
`screencap` 只能截 display 0，拿不到 Soul 界面。
故直接截 Windows 侧的 MuMu 渲染窗口（render_wnd），
它显示的就是 App 真实画面。

用法:
    python winshot.py                  # 自动找 MuMu 窗口，存到 wshot.png
    python winshot.py <hwnd> <out.png> # 指定窗口句柄和输出路径
"""
import ctypes, ctypes.wintypes as wt, struct, zlib, sys, os, subprocess, re, time

# ⭐ 无黑窗（2026-09-30）：mumu-cli.exe / tasklist 是控制台程序，不带此标志每次调用都闪黑窗。
_NW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

# ⭐ 2026-10-07：MuMu 安装目录迁移（D:\MuMuPlayer → Program Files\Netease\MuMu），
#   截图链路查 render_wnd 依赖 mumu-cli，写死旧路径会致截图全失败。候选自动发现。
def _mumu_cli():
    env = os.environ.get("SOUL_MUMU_ROOT")
    if env and os.path.isfile(os.path.join(env, "nx_main", "mumu-cli.exe")):
        return os.path.join(env, "nx_main", "mumu-cli.exe")
    for cand in (r"D:\Program Files\Netease\MuMu", r"D:\MuMuPlayer"):
        p = os.path.join(cand, "nx_main", "mumu-cli.exe")
        if os.path.isfile(p):
            return p
    return r"D:\Program Files\Netease\MuMu\nx_main\mumu-cli.exe"


_MUMU_CLI = _mumu_cli()

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
user32.SetProcessDPIAware()

# ---- 需要用到的 Win32 常量 ----
SRCCOPY = 0x00CC0020
PW_RENDERFULLCONTENT = 0x00000002
BI_RGB = 0
DIB_RGB_COLORS = 0

SRCCOPY = 0x00CC0020
PW_CLIENTONLY = 0x00000001

class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
        ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
        ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
        ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD),
    ]

class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


def write_png(path, width, height, bgra: bytes):
    """把 BGRA 字节写成 PNG（纯标准库）"""
    stride = width * 4
    raw = bytearray()
    for y in range(height):
        row = bgra[y * stride:(y + 1) * stride]
        rgb = bytearray(width * 3)
        # ⭐ 2026-10-04 提速（用户要求）：扩展切片（stride 赋值）在 C 层完成 B/R 交换。
        #   原来是逐像素 Python 循环（533x948 ≈ 50 万次），实测 299ms/次，现在 10ms。
        rgb[0::3] = row[2::4]          # R
        rgb[1::3] = row[1::4]          # G
        rgb[2::3] = row[0::4]          # B
        raw.append(0)                  # filter type 0
        raw += rgb

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
           + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)


def _vm_index():
    """当前实例号（2026-09-30 双实例）。取不到就退回 0 —— 绝不抛异常打断截图链路。"""
    try:
        import soul_instance
        return str(soul_instance.vm_index())
    except Exception:
        return os.environ.get("SOUL_VMINDEX", "0") or "0"


def _hwnd_cache_path():
    d = os.path.dirname(os.path.abspath(__file__))
    try:
        import soul_instance
        # 实例0 → .winshot_hwnd（与改造前相同）；实例1 → .winshot_hwnd.1
        return soul_instance.state_path(d, ".winshot_hwnd")
    except Exception:
        return os.path.join(d, ".winshot_hwnd")


def _hwnd_cached():
    """读上次的 render_wnd（跨进程复用，避免每次都跑 `mumu-cli info`）。

    ⭐ 2026-09-30 提速：soul.screenshot() 每次都会新起一个本脚本进程，
       而 `mumu-cli info -v 0` 实测要几百毫秒（一轮几十次 → 用户反馈"太慢"）。
       缓存有效期 `SOUL_HWND_TTL`（默认 600s）；句柄失效（窗口已关）立刻回退真查。
    """
    ttl = float(os.environ.get("SOUL_HWND_TTL", "600"))
    # ⭐ 2026-10-01：只判 IsWindow 不够。窗口重启后句柄会被系统回收给别的应用，
    #   旧句柄照样 IsWindow=True，于是**静默截到别的窗口**（实测截图从 533x948 变
    #   868x584、OCR 全崩、脚本一句错都不报，整轮浪费 40 分钟）。故每隔
    #   SOUL_HWND_CHECK（默认 120s）就与 `mumu-cli info` 的 render_wnd 对一次账。
    check = float(os.environ.get("SOUL_HWND_CHECK", "120"))
    p = _hwnd_cache_path()
    try:
        if not os.path.exists(p):
            return None
        age = time.time() - os.path.getmtime(p)
        if age > ttl:
            return None
        txt = open(p, "r", encoding="utf-8").read().strip()
        h = int(txt, 16)
        if not user32.IsWindow(h):
            return None
        if age > check:
            cur = _cur_render_wnd()
            if cur is None:
                # ⭐ 2026-10-04：对不上账（mumu-cli 查不到 render_wnd）时**不能**继续信缓存。
                #   实测事故：重启后句柄被系统回收给别的窗口，IsWindow 仍为真，缓存被当成
                #   有效句柄用了一整轮 —— PrintWindow 返回 1 但整帧单色，下游全盘误判。
                return None
            if cur != h:
                _hwnd_save(cur)
                return None       # 句柄已换人 → 不拿它截图，宁可慢一次也不静默截错
        return h
    except Exception:
        return None


def _cur_render_wnd():
    """问 mumu-cli 当前实例的 render_wnd 实际是多少（拿不到就 None，绝不抛）。"""
    cli = _MUMU_CLI
    try:
        out = subprocess.run([cli, "info", "-v", _vm_index()], capture_output=True,
                             timeout=10, creationflags=_NW).stdout.decode("utf-8", "ignore")
        m = re.search(r'"render_wnd"\s*:\s*"([0-9A-Fa-f]+)"', out)
        return int(m.group(1), 16) if m else None
    except Exception:
        return None


def _hwnd_save(h):
    try:
        with open(_hwnd_cache_path(), "w", encoding="utf-8") as f:
            f.write(f"{h:X}")
    except Exception:
        pass


def find_mumu_hwnd():
    """优先用 mumu-cli info 的 render_wnd；失败则按类名枚举

    ⭐ 2026-09-30：命中缓存时直接返回（跳过 mumu-cli info），来源标 `render_wnd(cache)`。
    """
    h = _hwnd_cached()
    if h:
        return h, "render_wnd(cache)"
    cli = _MUMU_CLI
    try:
        out = subprocess.run([cli, "info", "-v", _vm_index()], capture_output=True,
                             timeout=25, creationflags=_NW).stdout.decode("utf-8", "ignore")
        m = re.search(r'"render_wnd"\s*:\s*"([0-9A-Fa-f]+)"', out)
        if m:
            h = int(m.group(1), 16)
            if user32.IsWindow(h):
                _hwnd_save(h)
                return h, "render_wnd(info)"
    except Exception:
        pass
    # 兜底：枚举可见窗口找 MuMu
    res = []
    EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(h, l):
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        try:
            name = subprocess.run(["tasklist", "/FI", f"PID eq {pid.value}", "/FO", "CSV", "/NH"],
                                  capture_output=True, timeout=8, creationflags=_NW).stdout.decode("utf-8", "ignore")
        except Exception:
            return True
        if "MuMu" in name and user32.IsWindowVisible(h):
            r = wt.RECT()
            user32.GetWindowRect(h, ctypes.byref(r))
            w, hh = r.right - r.left, r.bottom - r.top
            if w > 400 and hh > 400:
                res.append((w * hh, h, w, hh))
        return True
    user32.EnumWindows(EnumProc(cb), 0)
    if not res:
        return None, "not-found"
    res.sort(reverse=True)
    _hwnd_save(res[0][1])
    return res[0][1], f"enum({res[0][2]}x{res[0][3]})"


# ⭐ 2026-10-04 空白帧检测（用户实测事故根因）：
#   模拟器渲染进程挂死时 PrintWindow 仍返回 1，但整帧是**纯色**（实测 wshot.png 恒 3406B，
#   采样只有 2 种颜色）。旧版只检查"全黑"，于是纯白帧被当成"截图成功"→ OCR 全空 →
#   下游所有 UI 判定失败却一句错都不报，守护白烧 100~200s/轮，还把真人误判成"重试冷却"。
#   正常 Soul 界面采样 3000 点有数百种颜色；阈值 12 远低于正常值，不会误杀。
BLANK_COLORS = int(os.environ.get("SOUL_BLANK_COLORS", "12"))


def _distinct_colors(bgra, w, h, n=3000):
    """抽样统计不同颜色数（纯标准库，约 1ms）"""
    total = w * h
    if total <= 0:
        return 0
    step = max(1, total // n) * 4
    cols = set()
    for i in range(0, len(bgra) - 4, step):
        cols.add(bgra[i:i + 3])
        if len(cols) > 64:
            break
    return len(cols)


def capture(hwnd, out):
    r = wt.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return "GetWindowRect 失败"
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        return f"窗口尺寸异常 {w}x{h}"

    hdc_win = user32.GetWindowDC(hwnd)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_win)
    hbm = gdi32.CreateCompatibleBitmap(hdc_win, w, h)
    gdi32.SelectObject(hdc_mem, hbm)

    ok = user32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT | PW_CLIENTONLY)
    if not ok:
        ok = user32.PrintWindow(hwnd, hdc_mem, 0)

    bi = BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h            # 负数 = 自顶向下
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bi.bmiHeader.biCompression = BI_RGB
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(hdc_mem, hbm, 0, h, buf, ctypes.byref(bi), DIB_RGB_COLORS)
    data = buf.raw

    # ⭐ 2026-10-05 治本（daemon 全天"发送未成功"的终极根因）：
    #   PrintWindow 对 MuMu 15 的 GPU 渲染窗口极不稳定（窗口后台/被遮挡时必黑，
    #   daemon 后台跑 → 截图全黑 → OCR 失败 → 页面误判 → 导航/发送全失败）。
    #   加 BitBlt 屏幕区域兜底：从屏幕 DC 直接截取窗口矩形（GPU 内容可靠，不依赖
    #   窗口前台状态）。实测 BitBlt 稳定拿到 Soul 画面（540x960）。
    #   MuMu 窗口被完全遮挡时 BitBlt 会截到遮挡窗口 → 由下方 BLANK_COLORS 单色检测
    #   与文件大小校验兜住（完全遮挡=纯色/他人画面，下游 OCR 对不上页面即放弃本轮）。
    if (not ok) or data[:4000] == b"\x00" * 4000:
        hdc_screen = user32.GetDC(0)
        ok2 = gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, r.left, r.top, SRCCOPY)
        user32.ReleaseDC(0, hdc_screen)
        if not ok2:
            gdi32.DeleteObject(hbm)
            gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(hwnd, hdc_win)
            return "PrintWindow 与 BitBlt 都失败"
        gdi32.GetDIBits(hdc_mem, hbm, 0, h, buf, ctypes.byref(bi), DIB_RGB_COLORS)
        data = buf.raw

    gdi32.DeleteObject(hbm)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(hwnd, hdc_win)

    # 全黑说明没截到内容
    if data[:4000] == b"\x00" * 4000:
        # 残留的旧帧会被下游当成"刚截的" → 必须删掉
        try:
            os.path.exists(out) and os.remove(out)
        except OSError:
            pass
        return f"截图全黑(PrintWindow={ok}, {w}x{h})"

    # ⭐ 2026-10-04：空白/单色帧同样不可用（纯白最常见）→ 删掉输出文件并明确报错，
    #   让 soul.screenshot() 的 size>5000 检查自然失败，绝不把纯色帧当成功帧传下去。
    ncol = _distinct_colors(data, w, h)
    if ncol <= BLANK_COLORS:
        try:
            os.path.exists(out) and os.remove(out)
        except OSError:
            pass
        return (f"截图空白/单色帧(颜色数={ncol}<= {BLANK_COLORS}, PrintWindow={ok}, {w}x{h}) "
                f"→ 模拟器画面很可能已挂死")

    write_png(out, w, h, data)
    return f"OK {w}x{h} PrintWindow={ok}"


if __name__ == "__main__":
    if len(sys.argv) >= 3:
        hwnd, out = int(sys.argv[1], 0), sys.argv[2]
        src = "指定"
    else:
        out = sys.argv[1] if len(sys.argv) == 2 else r"E:\soul\wshot.png"
        hwnd, src = find_mumu_hwnd()
    if not hwnd:
        print(f"❌ 未找到 MuMu 窗口 ({src})"); sys.exit(1)
    msg = capture(hwnd, out)
    # ⭐ 2026-10-04 自愈：句柄缓存可能已被"系统把旧句柄回收给别的窗口"而污染。
    #   实测：缓存 0xAD0532（564x1004，MuMu 管理器的 CEF 窗口）→ PrintWindow 返回 1
    #   但整帧只有 1 种颜色 → daemon 每轮判「画面不健康」，分级恢复也修不好——因为
    #   缓存从不失效。故截失败时**清掉缓存重查一次**，让链路自己爬出来。
    if not msg.startswith("OK"):
        try:
            os.remove(_hwnd_cache_path())
        except OSError:
            pass
        h2, s2 = find_mumu_hwnd()
        if h2 and h2 != hwnd:
            msg2 = capture(h2, out)
            if msg2.startswith("OK"):
                hwnd, src, msg = h2, s2, msg2
            else:
                msg = f"{msg} ｜ 换窗口仍失败({s2} hwnd=0x{h2:X}): {msg2}"
    print(f"窗口={src} hwnd=0x{hwnd:X} → {msg}")
    if msg.startswith("OK"):
        print(f"输出: {out} ({os.path.getsize(out)} 字节)")
