# -*- coding: utf-8 -*-
"""Soul / MuMu 模拟器 统一控制脚本（MuMu CLI 版）

迁移背景（2026-09-28）：
  雷电模拟器已卸载，Soul 及其数据整体迁到 MuMu 15.0（安卓15）。
  **adb 通道在 MuMu 上不可用**（Windows 侧无端口转发，16384 始终拒绝），
  但官方 CLI `mumu-cli.exe` 的 `sh` / `control` 子命令完全够用，且不需要端口。

  实测结论：
    - 点击/滑动/按键:  mumu-cli sh -c "input -d <display> tap x y"   ✅
    - 中文输入:        mumu-cli control tool cmd -c input_text -t "中文"  ✅（原生支持，弃用 ADBKeyboard）
    - 读消息:          直读 SQLite（soul_im.py），不经 UI             ✅
    - 截图:            screencap 只能截 display 0；App 在独立 display，
                       故用 winshot.py 截 MuMu 渲染窗口            ✅
    - UI 树 dump:      ❌ MuMu 精简镜像缺 x86_64 的 libnativeloader.so，
                       uiautomator 无法运行 → 本模块不再依赖 dump

铁律（沿用并加强）：
1. 禁止 input keyevent 4（返回键）→ 根页面会退出 App；用 tap_back_arrow()
2. 所有点击必须带 `-d <display>`，否则打到主屏（桌面）→ 等于乱点
3. 发送后必须**用数据库校验**（不是看界面），拒绝静默失败
"""
import subprocess, sys, os, re, time, shutil, sqlite3

# ---- MuMu CLI 定位（禁止硬编码版本号路径；这里路径是安装目录，稳定）----
MUMU_CLI = r"D:\MuMuPlayer\nx_main\mumu-cli.exe"
VMINDEX = os.environ.get("SOUL_VMINDEX", "0")

# ---- 多实例隔离（2026-09-30 双实例）：路径/状态文件一律按实例分开 ----
# ⚠️ 写死 `MuMuPlayer-15.0-0` 的后果不是报错，而是**实例1 读到实例0（主号）的库**
#    → 账号2 拿着主号的会话列表去发消息。属于最危险的静默串号，必须按实例取。
try:
    from soul_instance import vm_index as _vm_index, state_path as _state_path
except Exception:  # 隔离模块缺失也不许整个崩掉，退回老行为（=实例0）
    def _vm_index():
        return 0

    def _state_path(base, name):
        return os.path.join(base, name)

VMI = _vm_index()  # 归一化实例号：非数字/负数一律回退 0（fail-safe）

# 共享目录（Windows 侧）—— 与模拟器内 /mnt/shared/private_shared 互通
# ⚠️ 2026-09-30 双实例：目录名带实例号。实例0 = MuMuPlayer-15.0-0（与改造前逐字符相同）
SHARED_WIN = rf"D:\MuMuPlayer\vms\MuMuPlayer-15.0-{VMI}\private_shared"
SHARED_AND = "/mnt/shared/private_shared"

BASE = os.path.dirname(os.path.abspath(__file__))
XML = os.path.join(BASE, "ui.xml")          # 兼容旧引用（不再产出内容）
SHOT = _state_path(BASE, "wshot.png")       # 窗口截图输出（实例0 仍为 wshot.png；实例1 → wshot.1.png）

# 坐标一律用**屏幕比例**表示，再按实测分辨率换算（2026-09-30 重标定）
# ⚠️ 标定证据：720 空间 (492,1268) 点「聊天」→ 进了别人主页；900 空间 (615,1585) → 聊天列表 ✅
# 下面这组比例 = 那次实测坐标 ÷ 900x1600。以后换分辨率/换机型，跑一次 `soul.calibrate()`
# 就能整体换算，不会再出现"整套坐标系统性偏小 1.25 倍"这种静默错位。
DEV_W, DEV_H = 900, 1600          # 兜底（calibrate() 会用 `wm size` 覆盖）

# ⭐ 底导航一行 5 个 tab 的比例取自**本轮 OCR 真值**（y 全在 1585/1600）：
#   星球 x=102 / 广场 x=287 / 聊天 x=615 / 自己 x=802（y=1585）
_FRAC = {
    "planet": (0.1133, 0.9906),
    "square": (0.3189, 0.9906),
    "plus":   (0.5011, 0.9844),
    "chat":   (0.6833, 0.9906),
    "me":     (0.8911, 0.9906),
    "box":    (0.5689, 0.9400),    # 输入框
    "send":   (0.8822, 0.9488),    # 发送按钮（6.38.5）
    "back":   (0.0689, 0.0819),    # 左上角返回箭头
}


def _pt(k):
    fx, fy = _FRAC[k]
    return (int(round(DEV_W * fx)), int(round(DEV_H * fy)))


def _rebuild_coords():
    """按当前 DEV_W/DEV_H 重建所有坐标常量。"""
    global TAB_PLANET, TAB_SQUARE, TAB_PLUS, TAB_CHAT, TAB_ME, BOX_XY, SEND_XY, BACK_XY
    TAB_PLANET = _pt("planet")
    TAB_SQUARE = _pt("square")
    TAB_PLUS = _pt("plus")
    TAB_CHAT = _pt("chat")
    TAB_ME = _pt("me")
    BOX_XY = _pt("box")
    SEND_XY = _pt("send")
    BACK_XY = _pt("back")


# 上次校准时的分辨率（用于检测运行期间分辨率是否变化）
_LAST_CALIBRATED = {"w": DEV_W, "h": DEV_H}


def calibrate():
    """用 `wm size` 实测分辨率重建坐标。开局跑一次即可（约 0.3s，不必每进程都跑）。"""
    global DEV_W, DEV_H
    try:
        m = re.search(r"(\d{3,5})\s*[xX]\s*(\d{3,5})", sh("wm size", timeout=10) or "")
        if m:
            DEV_W, DEV_H = int(m.group(1)), int(m.group(2))
            _LAST_CALIBRATED["w"], _LAST_CALIBRATED["h"] = DEV_W, DEV_H
    except Exception:
        pass
    _rebuild_coords()
    return DEV_W, DEV_H


def _maybe_recalibrate():
    """检测分辨率是否变化，变了则重新校准。每次 display() 前调用。"""
    try:
        out = sh("wm size", timeout=5) or ""
        m = re.search(r"(\d{3,5})\s*[xX]\s*(\d{3,5})", out)
        if m:
            w, h = int(m.group(1)), int(m.group(2))
            if (w, h) != (_LAST_CALIBRATED["w"], _LAST_CALIBRATED["h"]):
                print(f"📐 检测到分辨率变化 {_LAST_CALIBRATED['w']}x{_LAST_CALIBRATED['h']} → {w}x{h}，重新校准")
                calibrate()
    except Exception:
        pass


_rebuild_coords()


# ============================ MuMu CLI 底层 ============================
# ⭐ 无窗口标志：mumu-cli.exe 是控制台程序，默认每次调用都会闪一个黑色 cmd 窗口。
#    Soul 每一步操作（截图/点击/输入/OCR）都要调它一次，一轮下来几十次 → 用户看到
#    "电脑总是跳黑色命令窗口"（2026-09-29 用户反馈）。CREATE_NO_WINDOW 让子进程
#    不分配控制台，从根上消除弹窗。
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def _spawn_kw():
    """给 subprocess 加上"别弹控制台窗口"的参数（仅 Windows）。"""
    return {"creationflags": _NO_WINDOW} if os.name == "nt" else {}


def _run(args, timeout=40):
    try:
        p = subprocess.run(args, capture_output=True, timeout=timeout, **_spawn_kw())
        out = (p.stdout or b"").decode("utf-8", "ignore")
        err = (p.stderr or b"").decode("utf-8", "ignore")
        return (out + err).strip()
    except subprocess.TimeoutExpired:
        return "TIMEOUT"
    except Exception as e:
        return f"ERR {e!r}"


def _run_hard(args, timeout=40):
    """超时**强杀**版子进程执行器。

    背景（2026-10-05）：mumu-cli 的 sh 桥（NemuShell）坏死时，子进程卡在
    CreateProcess/管道等待里，`subprocess.run` 的 timeout 到期后 kill() 也会挂死
    （实测 120s+ 不返回）→ daemon 会卡死在 calibrate()。这里用
    Popen + communicate(timeout) + taskkill /T 强杀进程树兜底。
    """
    try:
        p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **_spawn_kw())
    except Exception as e:
        return f"ERR {e!r}"
    try:
        out, err = p.communicate(timeout=timeout)
        return ((out or b"").decode("utf-8", "ignore") + (err or b"").decode("utf-8", "ignore")).strip()
    except subprocess.TimeoutExpired:
        # 强杀进程树（mumu-cli 可能带着子进程）
        try:
            subprocess.run(["taskkill", "/PID", str(p.pid), "/F", "/T"],
                           timeout=5, capture_output=True, **_spawn_kw())
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
        try:
            p.wait(timeout=3)
        except Exception:
            pass
        return "TIMEOUT"
    except Exception as e:
        try:
            p.kill()
        except Exception:
            pass
        return f"ERR {e!r}"


# ---- adb 兜底通道（2026-10-05）----
# ⚠️ 头部旧注释说"adb 通道不可用（16384 始终拒绝）"是旧版结论；
#    MuMu 15 现版本每个实例都暴露 adb 端口（vm1=16416 实测可用）。
#    当 mumu-cli 的 sh 桥（NemuShell）坏死时，adb 直连是唯一能恢复控制的路径。
_ADB_EXE = None
_ADB_PORT = None
_MUMU_SH_FAIL = {"ts": 0.0}            # mumu-cli sh 上次失败时刻
_MUMU_SH_COOLDOWN = float(os.environ.get("SOUL_SH_FALLBACK_COOLDOWN", "60"))
# ⭐ 2026-10-05：NemuShell 桥持续坏死（每次必超时）→ 永久直走 adb 直连。
#   环境变量 SOUL_SKIP_MUMU_SH=0 可恢复主通道试探（排查桥恢复时用）。
_MUMU_SH_DISABLED = os.environ.get("SOUL_SKIP_MUMU_SH", "1") != "0"
# ⭐ 2026-10-05 补：Soul 屏 HWC 长 id 缓存（MuMu 15 的虚拟屏 id 会变动，缓存 30s 即失效重解析）
_ADB_DISP = {"id": None, "ts": 0.0}
_ADB_DISP_TTL = 30.0


def _find_adb():
    global _ADB_EXE
    if _ADB_EXE is None:
        for cand in (r"D:\MuMuPlayer\nx_main\adb.exe",
                     r"D:\MuMuPlayer\nx_device\15.0\shell\adb.exe"):
            if os.path.exists(cand):
                _ADB_EXE = cand
                break
        else:
            _ADB_EXE = "adb"
    return _ADB_EXE


def _adb_port():
    """当前实例的 adb 端口：优先从 mumu-cli info 读（RPC 通道通常仍可用）；
    读不到按 MuMu15 惯例 16384+32*实例号 兜底。"""
    global _ADB_PORT
    if _ADB_PORT:
        return _ADB_PORT
    try:
        txt = cli("info", "-v", VMINDEX, timeout=10)
        m = re.search(r'"adb_port"\s*:\s*(\d+)', txt or "")
        if m:
            _ADB_PORT = int(m.group(1))
            return _ADB_PORT
    except Exception:
        pass
    _ADB_PORT = 16384 + 32 * VMI
    return _ADB_PORT


_ADB_SERIAL = {"s": None, "ts": 0.0}


def _adb_serial():
    """解析当前可用的 adb 设备 serial（重启后端口可能漂移，枚举 devices 最稳）。

    ⭐ 2026-10-05：MuMu 整机重启（control -v all shutdown/launch）后，
    新实例的 adb 注册成标准模拟器端口（emulator-5554 / 127.0.0.1:5555），
    而 mumu-cli info 的 adb_port 仍报旧值(16416, offline) → 硬连旧端口必失败。
    这里优先试 info 端口，离线则从 `adb devices` 里挑第一个 device 状态条目。
    缓存 60s；失败返回 None 由调用方走原有报错。
    """
    now = time.time()
    if _ADB_SERIAL["s"] and now - _ADB_SERIAL["ts"] < 60:
        return _ADB_SERIAL["s"]
    adb = _find_adb()
    s = None
    try:
        port = _adb_port()
        _run_hard([adb, "connect", f"127.0.0.1:{port}"], timeout=6)
        out = _run_hard([adb, "devices"], timeout=6) or ""
        for line in out.splitlines():          # 优先 info 端口（127.0.0.1:x）
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device" and line.strip().startswith("127.0.0.1:"):
                s = parts[0]
                break
        if not s:                              # 兜底：任何 device 条目
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[1] == "device":
                    s = parts[0]
                    break
    except Exception:
        pass
    if s:
        _ADB_SERIAL["s"], _ADB_SERIAL["ts"] = s, now
    return s


def _adb_shell(cmd, timeout=40):
    """真实 adb shell 执行（mumu-cli sh 桥坏时的兜底通道）"""
    adb = _find_adb()
    s = _adb_serial() or f"127.0.0.1:{_adb_port()}"
    return _run_hard([adb, "-s", s, "shell", cmd], timeout=timeout)


def _hwc_id_for_activity(disp):
    """activity displayId → HWC 长 id。
    解析 `dumpsys display displays` 的 DisplayViewport 行（displayId=N, uniqueId='local:xxx'）。"""
    try:
        adb = _find_adb()
        s = _adb_serial() or f"127.0.0.1:{_adb_port()}"
        out = _run_hard([adb, "-s", s, "shell",
                         "dumpsys display displays 2>/dev/null"], timeout=15)
        m = re.search(r"displayId=" + re.escape(disp) + r", uniqueId='local:([^']+)'", out or "")
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def _soul_disp_hwc(refresh=False):
    """Soul 所在屏的 HWC 长 id：display() 拿活动屏号 → 映射。失败返回 None。"""
    d = display(refresh=refresh, max_age=0)
    if d is None:
        return None
    return _hwc_id_for_activity(d)


def _adb_screencap(path, timeout=15):
    """adb exec-out screencap 兜底：从 **Soul 所在虚拟屏** 取帧写 PNG（宿主窗口全黑时用）。

    2026-10-05 实测：MuMu 15 把 Soul 放在动态虚拟屏（mumuscreen00x，id 会变动），
    默认 screencap 截到的是桌面 → 必须 -d <HWC长id>。返回 True/False。
    注意：exec-out 是二进制输出，必须用 subprocess 直接拿 bytes，
    不能走 PowerShell 重定向（会破坏字节流）。
    """
    try:
        adb = _find_adb()
        s = _adb_serial() or f"127.0.0.1:{_adb_port()}"
        # 用缓存的 HWC id；超过 30s 或取不到则重新解析（虚拟屏 id 会变）
        if time.time() - _ADB_DISP["ts"] > _ADB_DISP_TTL:
            _ADB_DISP["id"], _ADB_DISP["ts"] = _soul_disp_hwc(refresh=True), time.time()
        hwc = _ADB_DISP["id"]
        args = [adb, "-s", s, "exec-out", "screencap"]
        if hwc:
            args += ["-d", hwc]
        args += ["-p"]
        p = subprocess.run(args, capture_output=True, timeout=timeout, **_spawn_kw())
        if p.returncode == 0 and p.stdout and len(p.stdout) > 20000:
            data = p.stdout
            idx = data.find(b"\x89PNG\r\n\x1a\n")
            if idx > 0:
                data = data[idx:]   # 剥掉 adb 的 "[Warning] Multiple displays..." 前缀
            if len(data) > 20000:
                with open(path, "wb") as f:
                    f.write(data)
                return True
        # 拿不到 → 清缓存，下次重解析
        _ADB_DISP["id"], _ADB_DISP["ts"] = None, 0.0
    except Exception:
        pass
    return False


def sh(cmd, timeout=40):
    """在模拟器内执行 shell（等价 adb shell）。

    主通道：mumu-cli sh（免端口）。2026-10-05 起增加兜底：
    主通道超时/坏死时（NemuShell 桥挂死，Windows 上连 timeout 都杀不掉），
    自动切到真实 adb 直连通道；失败后进入 60s 冷却，冷却期内直走 adb。
    ⚠️ 2026-10-05 下午再确认：NemuShell 桥**持续坏死**（每次必超时 10s），
    冷却期外每轮都要白等一次 10s → reply 导航整轮被拖慢/撞上 dumpsys 抽风窗口。
    故直接禁用主通道（_MUMU_SH_DISABLED=True），永久直走 adb 直连。
    """
    if _MUMU_SH_DISABLED:
        return _adb_shell(cmd, timeout=timeout)
    if time.time() - _MUMU_SH_FAIL["ts"] > _MUMU_SH_COOLDOWN:
        out = _run_hard([MUMU_CLI, "sh", "-v", VMINDEX, "-c", cmd], timeout=min(timeout, 10))
        if out != "TIMEOUT":
            return out
        _MUMU_SH_FAIL["ts"] = time.time()
        print(f"!! mumu-cli sh 超时（{cmd[:40]}…）→ 切 adb 兜底通道")
    return _adb_shell(cmd, timeout=timeout)


def cli(*args, timeout=60):
    """直接调 mumu-cli 子命令"""
    return _run([MUMU_CLI] + [str(a) for a in args], timeout=timeout)


def adb(*args, timeout=40):
    """⚠️ 兼容旧调用的垫片：把 adb 风格参数翻译成 mumu-cli sh。
    只支持 'shell ...' 形态；用于那些还没改造完的旧调用点。"""
    a = [str(x) for x in args]
    if a and a[0] == "shell":
        a = a[1:]
    return sh(" ".join(f'"{x}"' if " " in x else x for x in a), timeout=timeout)


# ---- display 检测：Soul 跑在独立 display，点击必须带 -d ----
_DISP_CACHE = {"d": None, "ts": 0.0}

# ⭐ 2026-09-30 提速：截图短缓存 + 「列表已在顶部」状态。
#   事故背景：一轮里 soul_reply 会连做几十次 screenshot()+OCR，而 MuMu 窗口截图
#   实测 0.3~0.6s/次 → 单次 reply 动辄 3~5 分钟，用户反馈"每次操作都太慢"。
#   缓存只在同一进程内、极短时间窗内生效；任何改变界面的动作都会失效缓存。
SHOT_CACHE_TTL = float(os.environ.get("SOUL_SHOT_TTL", "0.35"))   # 秒；0 = 关闭缓存
_SHOT_CACHE = {"ts": 0.0}

# 「聊天列表已滚到顶」进程内状态： avoid 重复回顶（_scroll_top 一次要几十秒）
_TOP_STATE = {"at_top": False}

# ⭐ 2026-10-05：winshot（宿主窗口截图）失败冷却 —— MuMu 渲染管线坏掉时窗口恒黑，
#   每次截图先试 3 次 winshot 纯浪费（约 4s）。冷却期内直走 adb screencap。
_WINSHOT_FAIL = {"ts": 0.0}
_WINSHOT_COOLDOWN = 120.0


def _shot_used():
    """标记：刚用过截图（内部用）"""
    _SHOT_CACHE["ts"] = time.time()


def invalidate_shot():
    """界面即将/已经改变 → 作废截图缓存，下次必重新截图。

    所有会改变画面的动作（tap/swipe/type_text/launch/返回）都应调用它；
    保守起见默认在 tap/swipe 里调，宁可多截一次也不读旧帧。
    """
    _SHOT_CACHE["ts"] = 0.0


def mark_top(at=True):
    """标记聊天列表当前是否已在顶部（供 _scroll_top 复用）"""
    _TOP_STATE["at_top"] = bool(at)


def at_top():
    """聊天列表是否已知在顶部（未知=False，保守）"""
    return bool(_TOP_STATE.get("at_top"))


def _display_locate():
    """定位 Soul 当前所在 display（一次尝试）。失败返回 None。

    ⭐ 2026-10-05 实测：MuMu 15 上 dumpsys activity 会列出多个 Display（#40/#41/#0），
       Soul 的 topResumedActivity 行所在块才是真号；#0 是桌面。
       原 fallback（dumpsys window windows 找 mDisplayId）在 Android 15 格式下
       **会把 mDisplayId=0 错配给 Soul 窗口** → tap -d 0 = 点击全部打到桌面
       （"页面异常/导航失败"假象）。已删除该 fallback：activity 抽风时
       由 display() 的缓存回退兜住，宁可放弃本轮也不打桌面。
    """
    out = sh("dumpsys activity activities 2>/dev/null")
    d = None
    cur = None
    for line in out.splitlines():
        m = re.search(r"Display #(\d+)", line)
        if m:
            cur = m.group(1)
        # ⭐ 2026-10-05 治本：只认 topResumedActivity 行且包名必须带 cn.soulapp.android。
        #   此前 "MainActivity"/"Resumed:" 宽匹配会把 Display #0（桌面）块里的
        #   * Task{} / * Hist #0 历史记录行命中 → 拿到 0 → 点击全打桌面。
        if "cn.soulapp.android" in line and cur is not None:
            m2 = re.search(r"topResumedActivity=.*?cn\.soulapp\.android", line)
            if m2:
                d = cur
                break
    return d


def display(refresh=False, max_age=120):
    """返回 Soul 当前所在 display 号；找不到返回 None（调用方必须拒绝操作）。

    MuMu 15 把 App 放在独立虚拟屏（mumuscreenNNN），
    `input` 不带 -d 默认打到 display 0（桌面）→ 点了等于没点/乱点。
    """
    _maybe_recalibrate()          # ⭐ 2026-10-04 分辨率变化自动重校准
    if not refresh and _DISP_CACHE["d"] is not None and time.time() - _DISP_CACHE["ts"] < max_age:
        return _DISP_CACHE["d"]
    d = None
    for attempt in range(3):      # ⭐ 2026-10-05 重试：导航/切页瞬变期 dumpsys 偶发不全
        d = _display_locate()
        if d is not None:
            break
        if attempt < 2:
            time.sleep(2.0 + attempt * 2.0)   # adb shell dumpsys 慢通道留足时间
    # ⭐ 2026-10-05 治本（虚拟屏号漂移 6→15→21 后导航全挂的根因）：
    #   定位失败**绝不把有效缓存覆盖成 None**。虚拟屏号只会在「窗口重建」
    #   （am start 清栈 / launch_app / MuMu 渲染重建）时变，而那些路径都已
    #   invalidate_display() 清缓存。正常导航轮内 dumpsys 间歇抽风 ≠ display 变了
    #   → 回退缓存继续干；只有缓存被 invalidate（None）且重查失败才拒绝点击。
    if d is None and _DISP_CACHE["d"] is not None:
        d = _DISP_CACHE["d"]
    if d is not None:
        _DISP_CACHE["d"] = d
        _DISP_CACHE["ts"] = time.time()
    if d is None:
        print("!! 未能定位 Soul 所在 display → 拒绝所有点击（防止打到桌面）")
    return d


_ADB_ROOT_DONE = {}

def _adb_ensure_root():
    """⭐ 2026-10-05：MuMu 重启后 adbd 常以 shell 身份跑（uid=2000），
    /data/data 读不到 → im.pull / verify_sent 全部失败（实测"发送未成功"）。
    用 `adb root` 自愈；每个进程只做一次（root 后 adbd 重启，连接自动恢复）。"""
    try:
        adb = _find_adb()
        if _ADB_ROOT_DONE.get("once"):
            return True
        s = _adb_serial() or f"127.0.0.1:{_adb_port()}"
        out = _run_hard([adb, "-s", s, "shell", "id"], timeout=8) or ""
        if "uid=0" in out:
            _ADB_ROOT_DONE["once"] = True
            return True
        _run_hard([adb, "-s", s, "root"], timeout=10)
        time.sleep(3)                                  # adbd 重启窗口
        _ADB_SERIAL["s"] = None                        # root 后重解析 serial
        s2 = _adb_serial() or f"127.0.0.1:{_adb_port()}"
        out2 = _run_hard([adb, "-s", s2, "shell", "id"], timeout=8) or ""
        _ADB_ROOT_DONE["once"] = "uid=0" in out2
        return _ADB_ROOT_DONE["once"]
    except Exception:
        return False


def connect(max_age=60):
    """兼容旧 API。MuMu 走 CLI 不需要连接，仅做一次存活检查 + adb root 自愈。"""
    out = sh("echo ok", timeout=20)
    if "ok" in out:
        _adb_ensure_root()
    return "ok" if "ok" in out else out


# ============================ 输入 ============================
def tap(x, y, retry=2):
    """点击（自动带 -d）。找不到 display 时拒绝执行。

    ⭐ 2026-10-05：点击报错（input 命令本身失败）时，可能是 MainActivity 重建 /
       虚拟屏重新分配瞬间 → 强制 display(refresh=True) 重查后再试，不再死磕旧号。
    """
    d = display()
    if d is None:
        return "NO_DISPLAY"
    invalidate_shot()                 # 点击必然改变界面 → 作废截图缓存
    mark_top(False)                   # 位置可能变了 → 不再相信"在顶部"
    last = ""
    for i in range(retry + 1):
        last = sh(f"input -d {d} tap {int(x)} {int(y)}")
        if "Exception" not in last and "Error" not in last:
            return last
        time.sleep(0.4)
        if i < retry:
            d = display(refresh=True)   # 虚拟屏重建 → 重新定位再试
            if d is None:
                break
    return last


def swipe(x1, y1, x2, y2, dur=400):
    d = display()
    if d is None:
        return "NO_DISPLAY"
    invalidate_shot()
    mark_top(False)
    return sh(f"input -d {d} swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(dur)}")


def swipe_up(x=360, y1=900, y2=350, dur=400):
    """列表向下翻（内容上移）。翻完必然不在顶部。"""
    r = swipe(x, y1, x, y2, dur)
    mark_top(False)
    return r


def swipe_down(x=360, y1=400, y2=1000, dur=400):
    """列表向上翻（内容下移），回顶手势。"""
    return swipe(x, y1, x, y2, dur)


def keyevent(code, d=None):
    """⚠️ 铁律：不要用 4（返回键）。保留给 HOME 等安全键。"""
    dd = d or display()
    if dd is None:
        return "NO_DISPLAY"
    return sh(f"input -d {dd} keyevent {int(code)}")


def _click_xy(x, y):
    """点击输入框/按钮前，先把 display 确认一遍"""
    d = display()
    if d is None:
        return False
    sh(f"input -d {d} tap {int(x)} {int(y)}")
    return True


def clear_text():
    """清空输入框：全选(Ctrl+A) + 删除 + 兜底 BACKSPACE 若干次。
    （原 ADBKeyboard 的 ADB_CLEAR_TEXT 广播在 MuMu 上不存在）"""
    d = display()
    if d is None:
        return False
    sh(f"input -d {d} keyevent --longpress KEYCODE_DEL 2>/dev/null")
    for _ in range(3):
        sh(f"input -d {d} keyevent KEYCODE_MOVE_END")
        sh(f"input -d {d} keyevent --longpress KEYCODE_DEL")
    for _ in range(40):                       # 兜底：逐字删
        sh(f"input -d {d} keyevent KEYCODE_DEL")
    return True


def type_text(s, box_xy=None, click=True):
    """输入文本 —— 用 MuMu 官方 input_text（**原生支持中文**，已验证）。
    弃用 ADBKeyboard + base64 广播（MuMu 上无该输入法）。

    ⚠️ 2026-09-30（Soul 6.38.5）新增 click 开关：
       新版输入框默认是「长按此处说话」（语音模式），**重复点击输入框会把焦点点散**，
       导致 input_text 灌进去的字丢失（实测：消息发不出、界面无变化）。
       → 需要"先聚焦、后灌字"的场景传 click=False（如 soul_send 的 _input()）。
    """
    box = box_xy or BOX_XY
    if click:
        _click_xy(*box)
        time.sleep(0.6)
    invalidate_shot()
    r = cli("control", "-v", VMINDEX, "tool", "cmd", "-c", "input_text", "-t", s)
    time.sleep(0.8)
    ok = '"errcode": 0' in r or "errcode" in r
    if not ok:
        print(f"!! input_text 异常: {r[:120]}")
    return ok


def ensure_ime():
    """MuMu 的 input_text 不依赖第三方输入法，保留空实现以兼容旧调用。"""
    return True


# ============================ 页面/状态 ============================
def activity():
    """当前前台 Activity（解析 dumpsys window）。用于动作前校验「在不在目标页」。"""
    out = sh("dumpsys window 2>/dev/null")
    m = re.search(r"mFocusedWindow=Window\{[^ ]* [^/]+/([^\s\}]+)", out)
    if not m:
        m = re.search(r"mFocusedWindow=.*?/([A-Za-z0-9_.]+)", out)
    return m.group(1) if m else ""


def on_main():
    a = activity()
    return bool(a) and ("startup.main.MainActivity" in a or a.endswith("MainActivity"))


def app_running(pkg="cn.soulapp.android"):
    return pkg in sh(f"ps -A | grep {pkg}")


def _wait_display(timeout=25):
    """循环等待 Soul resumed 就绪（冷启动/重启后 topResumedActivity 迟现）。

    ⭐ 2026-10-05：Soul 冷启动到 resumed 需 10-15s，display(refresh=True) 的
       3 次重试（约 6s）不够 → 定位 None → 整轮放弃。这里每 2.5s 重查，
       最长 timeout 秒，拿到号即返回；超时返回 None。
    """
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = _display_locate()
        if d is not None:
            _DISP_CACHE["d"] = d
            _DISP_CACHE["ts"] = time.time()
            return d
        time.sleep(2.5)
    print(f"  !! 等不到 Soul resumed（{int(timeout)}s）→ display 定位放弃")
    return None


def launch_app(pkg="cn.soulapp.android", wait=8, wait_disp=25):
    """启动 Soul 到前台。

    ⭐ 2026-10-05 治本：mumu-cli 桥（NemuShell）持续坏死，每次调用必超时 10s，
       之前 launch_app 走 cli() → App 实际没被拉起 → 桌面 Launcher →
       display 定位失败 → 整轮作废。现直走 adb `am start`（与 sh() 直走 adb 一致），
       再循环等待 Soul resumed 就绪，确保号拿到才返回。
    """
    invalidate_shot()
    mark_top(False)
    _DISP_CACHE["d"] = None      # ⭐ 2026-10-05：App 重启可能落到新虚拟屏 → 不许复用旧号
    try:
        sh(f"am start -n {pkg}/.component.startup.main.MainActivity --activity-clear-top")
    except Exception as e:
        print(f"  !! am start 异常: {e!r}")
    time.sleep(wait)
    if wait_disp > 0:
        return _wait_display(wait_disp)
    return None


def close_app(pkg="cn.soulapp.android"):
    invalidate_shot()
    mark_top(False)
    return cli("control", "-v", VMINDEX, "app", "close", "--package", pkg)


def app_state(pkg="cn.soulapp.android"):
    return cli("control", "-v", VMINDEX, "app", "info", "--package", pkg)


def restart_app(wait=8):
    close_app()
    time.sleep(1.5)
    launch_app(wait=wait)
    _DISP_CACHE["d"] = None


def invalidate_display():
    """⭐ 2026-10-05：窗口重建/切屏后调用，强制下次重新定位 display 号。
    MuMu 虚拟屏号会在运行中漂移（实测 6→15），旧号点击会静默打空。"""
    _DISP_CACHE["d"] = None


# ============ 遮挡弹窗处理（奇遇铃等）============
# ⚠️ 2026-09-29 实战问题：奇遇铃是 **MainActivity 内的卡片**（不是独立 Window），
#    弹出来会盖住聊天列表 → OCR 找不到目标会话 → 无法定位点击。
#    而且它会不定时反复弹（服务端推送），单纯"躲开"不可行，必须主动清障。
CLOSE_X = 639          # 卡片右上角 × 的 x（固定；y 随卡片高度变，须动态取）
BELL_KEYS = ("奇遇铃", "立即私聊", "开启奇遇")


def find_overlay():
    """检测奇遇铃**弹窗**；有则返回 (标题文字, 标题y)，否则 None。

    ⚠️ 2026-09-29 修误判：聊天列表里存在名为「奇遇铃-稍后再聊」的**会话条目**
    （关掉的奇遇铃会落到列表里，不是消失）。用 `"奇遇铃" in t` 当判据会把
    这个会话误当成弹窗 → 每次都被"检测到弹窗"。
    → 改用**「立即私聊」按钮**做判据：那是卡片独有的，列表里绝不会出现。
    """
    try:
        import soul_read as rd          # 延迟导入：避免 soul <-> soul_read 循环依赖
    except Exception:
        return None
    items = rd.items()
    btn = None
    for t, cx, cy in items:
        if "立即私聊" in t:
            btn = (cx, cy)
            break
    if not btn:
        return None
    # 往上找标题行（同卡片内、x 相近、y 更小）
    for t, cx, cy in items:
        if "奇遇铃" in t and cy < btn[1] and abs(cx - btn[0]) < 260:
            return (t, cy)
    return ("奇遇铃卡片", max(60, btn[1] - 400))


def dismiss_overlay(max_try=3):
    """关掉奇遇铃等遮挡弹窗（点右上角 ×）。返回 True 表示当前无遮挡。

    × 的 y 用 OCR 读到的「奇遇铃」标题 y 动态定位 —— 实测不同卡片高度不同，
    写死 y 会点空（第一张卡片 × 在 y≈134，第二张在 y≈65）。
    """
    for _ in range(max_try):
        hit = find_overlay()
        if not hit:
            return True
        _, y = hit
        print(f"  检测到遮挡弹窗「{hit[0]}」→ 点 × (639, {y})")
        tap(CLOSE_X, y)
        time.sleep(1.6)
    return find_overlay() is None


# ---- 「Soul未成年模式」系统弹窗（2026-09-29 新增）----
# ⭐ 真实故障：该弹窗**每次冷启动 / activity-clear-top 后必弹一次**，盖住底导航，
#   于是 _on_chat_list() 取不到「聊天」选中色 → 被判成"主框架但页面异常（底导航无选中态）"
#   → reply/match 全部跳过，表面像"定位不到人"，真因是弹窗遮挡（静默误判，实测踩了 3 轮）。
#   特征：青色按钮「我知道了」固定在 (532,1157)（OCR 实测）；无弹窗时该处是列表空白（白）。
#   ⚠️ 优先级低于奇遇铃 —— 只在没有奇遇铃时才关它。
KID_BTN = (532, 1157)
KID_TRIES = 3          # ⭐ 2026-10-04：点「我知道了」最多试几次（点完即验，关掉就停）


def is_kid_popup():
    """当前是否弹着「Soul未成年模式」弹窗。

    ⭐ 2026-09-29 第 2 版：改用 **OCR 关键词**（"未成年" / "我知道了"）。
      第 1 版用像素取色 (450-620, 1150-1200) 判青色 —— 实测**在「广场」tab 会误报**
      （feed 里的青色图片/元素落进采样区）→ 误点 (532,1157)，反而把页面越点越乱。
      OCR 判据不受页面内容干扰，且「我知道了」在正常聊天界面几乎不出现。
    """
    try:
        import soul_read as rd
        for t, cx, cy in rd.items():
            if "未成年" in t or "我知道了" in t:
                return True
        return False
    except Exception as e:
        print(f"  !! 未成年弹窗检测失败: {e!r}")
        return False


def close_kid_popup():
    """若「未成年模式」弹窗在，点「我知道了」关掉。返回 True = 已关/无需关。

    必须在**每次冷启动或 activity-clear-top 之后**调用（那正是它弹出的时机）。

    ⭐ 2026-10-04（用户口径：「跳未成年模式的时候点击『我知道了』就行」）：
      动作**只有一件** —— 点「我知道了」，但要点到真的关掉为止。
      · 按钮坐标优先用**本轮 OCR 实测值**（这行文字就在屏幕上，OCR 给的就是真坐标）；
      · 点完立刻轮询确认，没关就再点一次（最多 KID_TRIES 次），关掉即返回；
      · 只有当 OCR 读不到按钮文字、只能用兜底坐标时才**只点一次** ——
        兜底坐标无法确认对错，盲点多次会像 2026-09-29 那样"越点越乱"。
      原实现只点一次 + 1.5s 后判定，点不掉就 return False → 弹窗继续盖住底导航 →
      `_on_chat_list()` 取不到「聊天」选中色 → 判"页面异常" → 整轮跳过（N2 实测）。
    """
    try:
        import soul_read as rd
    except Exception as e:
        print(f"  !! 弹窗检测失败: {e!r}")
        return True
    for i in range(max(1, KID_TRIES)):
        try:
            items = rd.items()
        except Exception as e:
            print(f"  !! 弹窗检测失败: {e!r}")
            return True
        btn, kid = None, False
        for t, cx, cy in items:
            if "未成年" in t:
                kid = True
            if "我知道了" in t:
                btn = (cx, cy)
        if not (kid or btn):
            return True                      # 没有弹窗 / 已经关掉 → 完成
        pos = btn or KID_BTN
        print(f"  🧹 检测到「未成年模式」弹窗（盖住底导航）→ 点「我知道了」{pos}"
              + ("" if i == 0 else "（第 %d 次）" % (i + 1)))
        tap(*pos)
        time.sleep(1.5)
        if not btn:
            # OCR 没读到按钮文字，用的是兜底坐标：只点一次，不盲点（防越点越乱）
            break
    try:
        for t, cx, cy in rd.items():
            if "未成年" in t or "我知道了" in t:
                print(f"  !! 弹窗仍未关闭（已点 {min(KID_TRIES, i + 1)} 次），仍遮挡底导航")
                return False
    except Exception:
        pass
    return True


# ---- 「星球 · 今日免费匹配机会已用完」弹层（2026-10-04 现场截图确认）----
# ⭐ 现象：星球页点匹配后弹出半屏底部弹层「今日免费匹配机会已用完 (50/50)」，
#   内含「和 Souler 聊满一颗心 → 每聊 1 位今日灵魂匹配的 souler +5 次」与购卡入口。
#   两个后果：① 此后匹配必然空手；② **它完全盖住底导航** → `_on_chat_list()` 取不到
#   「聊天」选中色 → 判"页面异常" → 回复流程被跳过。
#   用户口径：「这种情况下今天就不要再去匹配了」→「今天不再匹配」由 daemon 负责，
#   这里只负责**把它从屏幕上清掉**，让底导航重新可见。
QUOTA_KEYS = ("免费匹配机会已用完", "匹配机会已用完", "今日免费匹配")
QUOTA_TRIES = 3                    # 点「去聊天」最多试几次（点完即验，消失就停）
QUOTA_BTN_FRAC = (0.81, 0.82)      # 「去聊天」兜底位置（2026-10-04 11:09 现场截图按比例换算）


def is_quota_popup():
    """当前是否浮着「今日免费匹配机会已用完」弹层（OCR 关键词）"""
    try:
        import soul_read as rd
        txt = "".join(t for t, _, _ in rd.items())
    except Exception as e:
        print(f"  !! 额度弹层检测失败: {e!r}")
        return False
    return any(k in txt for k in QUOTA_KEYS)


def close_quota_popup():
    """若「今日免费匹配机会已用完」弹层在，**点「去聊天」**把它清掉。返回 True = 已清/无需清。

    ⭐ 2026-10-04（用户口径：「这种情况下 点击去聊天」）：
      动作只有一件 —— 点「去聊天」（点它会落到聊天列表，本来就是下一步要去的地方）；
      点完立刻复检，还在就再点一次（最多 QUOTA_TRIES 次），消失即返回 True。
      只有 OCR 读不到「去聊天」时才退到**坐标兜底**（位置由 11:09 现场截图按比例换算），
      且只点一次 —— 兜底坐标无法自证对错，盲点多次会把页面点乱。
      （首版用的"下滑关闭"已去掉：该弹层上下滑实测无效，用户已明确改用「去聊天」。）
    """
    if not is_quota_popup():
        return True
    try:
        import soul_read as rd
        W, H = rd.dev_size()
    except Exception:
        rd, W, H = None, 900, 1600
    print("  🎫 检测到「今日免费匹配机会已用完」弹层（盖住底导航）→ 点「去聊天」")
    for i in range(max(1, QUOTA_TRIES)):
        pos = None
        if rd is not None:
            try:
                for t, cx, cy in rd.items():
                    if "去聊天" in t:
                        pos = (cx, cy)
                        break
            except Exception:
                pos = None
        blind = pos is None
        if blind:
            pos = (int(W * QUOTA_BTN_FRAC[0]), int(H * QUOTA_BTN_FRAC[1]))
        print("     点「去聊天」%s%s%s"
              % (pos, "（OCR 未读到按钮 → 用兜底坐标，只点一次）" if blind else "",
                 "" if (i == 0 or blind) else "（第 %d 次）" % (i + 1)))
        tap(*pos)
        time.sleep(1.8)
        if not is_quota_popup():
            print("     ✓ 弹层已消失，底导航恢复可见")
            return True
        if blind:
            break                      # 兜底坐标：只点一次，不盲点
    print("  !! 额度弹层未清掉（点「去聊天」无效）")
    return False


def _is_soul_activity(a):
    """是否 Soul 的 Activity。⭐ 2026-09-29 修误判：
    旧判据是子串 `"soulapp" in a` —— 但 Soul 的 **RN 页面**（搜索页 / 用户主页 / 广场 feed）
    activity 是 `cn.soul.android.soul_rn_sdk.multiengine.RnContainerActivity`，**不含 soulapp**。
    于是每次停在 RN 页都被误判成"Soul 不在前台" → 触发 launch_app 重启 App
    → 页面被搞乱、点击落空（实测：搜索页里点用户行毫无反应）。
    判据改为包名前缀 `cn.soul`（cn.soulapp.android.* 与 cn.soul.android.* 都命中）。
    """
    return bool(a) and a.startswith("cn.soul")


def ensure_foreground(wait=8):
    """自动化前置：确保 Soul 在前台。

    ⚠️ 实测（2026-09-29）：Soul 被切到后台时前台是 lawnchair，
    此时 display() 检测不到 Soul → 所有点击被保护逻辑拒绝
    （表现成"脚本什么都没干"）。所以每个动作序列开头都要先确认前台。
    """
    a = activity()
    if not _is_soul_activity(a):
        print(f"  Soul 不在前台（当前 {a or '未知'}）→ 拉起")
        launch_app(wait=wait)
        time.sleep(1.5)
    return _is_soul_activity(activity())


def ensure_ready(wait=8, auto_bell=True):
    """一站式前置：Soul 前台 + （默认）优先处理奇遇铃。

    ⭐ 用户规则（2026-09-29 明确）：「**挡路了也要先奇遇铃**」
       → 本函数**绝不主动关闭奇遇铃**。
       → 检测到奇遇铃时，默认直接点「立即私聊」把它**处理掉**（第 1 优先级），
         而不是关掉它躲过去。
       → 只有 auto_bell=False 时才把决定权交还调用方。

    返回 True = 可以安全继续后续操作。
    """
    if not ensure_foreground(wait=wait):
        print("  !! 无法把 Soul 拉到前台，放弃")
        return False
    if is_love_bell():
        if not auto_bell:
            print("  ⚠️ 当前弹着奇遇铃（第 1 优先级），调用方需先处理")
            return False
        name, ok = accept_love_bell()
        print(f"🔔 奇遇铃优先处理（挡路也先它）：已点「立即私聊」，对方「{name}」")
        # 注意：此时已进入与该人的会话；调用方需按需重新导航
        return True
    close_kid_popup()      # ⭐ 2026-09-29：冷启动后必弹的「未成年模式」弹窗，会遮底导航
    close_quota_popup()    # ⭐ 2026-10-04：星球「今日免费匹配机会已用完」弹层同样盖住底导航
    return True


# ---- 奇遇铃：第 1 优先级（用户 2026-09-29 定）----
BELL_SKIP = ("立即私聊", "开启奇遇", "奇遇铃", "同城奇遇", "她在等你")


def is_love_bell():
    """当前是否弹着奇遇铃"""
    return find_overlay() is not None


def love_bell_name():
    """读奇遇铃卡片上的对方昵称（标题行下方、按钮上方那段文字）"""
    try:
        import soul_read as rd
    except Exception:
        return None
    items = rd.items()
    btn_y = None
    for t, cx, cy in items:
        if "立即私聊" in t:
            btn_y = cy
            break
    for t, cx, cy in items:
        s = (t or "").strip()
        if not s or any(k in s for k in BELL_SKIP):
            continue
        # 昵称在标题(y≈144)与按钮之间；排版上比提示语更短
        if 170 < cy < (btn_y or 500) and len(s) <= 16:
            return s
    return None


def accept_love_bell(wait=4):
    """奇遇铃第 1 优先级：点「立即私聊」直接开聊。

    返回 (对方昵称, True/False)。没弹铃返回 (None, False)。
    """
    if not ensure_foreground():
        return (None, False)
    try:
        import soul_read as rd
    except Exception:
        return (None, False)
    items = rd.items()
    btn = None
    for t, cx, cy in items:
        if "立即私聊" in t:
            btn = (cx, cy)
            break
    if not btn:
        return (None, False)
    name = love_bell_name()
    print(f"🔔 奇遇铃 → 点「立即私聊」({btn[0]},{btn[1]})，对方「{name}」")
    tap(*btn)
    time.sleep(wait)
    return (name, True)


# ============================ 截图（窗口级）============================
def screenshot(path=None, retry=2, force=False):
    """截 MuMu 窗口 → PNG。Soul 在独立 display，screencap 拿不到，必须走窗口截图。

    ⭐ 2026-09-30 提速：加**进程内短缓存**（默认 0.35s）。
    一轮里 `rd.items()` 与显式 `soul.screenshot()` 经常在同一瞬间被连着调用
    （同一帧被截两次 = 纯浪费）。缓存只在极短窗口内复用同一张 PNG，
    且 `tap()/swipe()/type_text()/launch_app()/restart_app()` 都会 `invalidate_shot()`
    → **不会读到操作前的旧帧**（那条是串台级事故，绝不能犯）。
    关缓存：`SOUL_SHOT_TTL=0`；调窗口：`SOUL_SHOT_TTL=0.6`。
    """
    # ⚠️ 运行时读全局变量（不是定义时绑定），便于测试/隔离时改 soul.SHOT 生效
    path = path or globals().get("SHOT")
    now = time.time()
    if (not force) and SHOT_CACHE_TTL > 0 and _SHOT_CACHE["ts"] \
            and (now - _SHOT_CACHE["ts"]) < SHOT_CACHE_TTL \
            and os.path.exists(path) and os.path.getsize(path) > 5000 \
            and os.path.getmtime(path) <= _SHOT_CACHE["ts"] + 0.001:
        # ↑ 最后一条：PNG 若被**别的进程**写新（本机存在并发实例），
        #   缓存视为过期重截 —— 防止读到别人操作前的旧帧（串台级风险）
        return path
    ws = os.path.join(BASE, "winshot.py")
    tried_winshot = False
    if time.time() - _WINSHOT_FAIL["ts"] > _WINSHOT_COOLDOWN:
        tried_winshot = True
        for _ in range(retry + 1):
            r = _run([sys.executable, ws, path], timeout=60)
            # ⭐ 2026-10-04：空白/单色帧同样不算成功（winshot 会在消息里带"空白"）
            if (os.path.exists(path) and os.path.getsize(path) > 5000
                    and "全黑" not in r and "空白" not in r):
                _SHOT_CACHE["ts"] = time.time()
                _WINSHOT_FAIL["ts"] = 0.0          # 恢复健康 → 清冷却
                return path
            time.sleep(0.8)
        _WINSHOT_FAIL["ts"] = time.time()          # winshot 本轮失败 → 进冷却
    # （冷却期内直接落到下面，不再试 winshot）
    # ⭐ 2026-10-05：宿主渲染窗口全黑（MuMuNxMain 渲染管线坏死）时，
    #   winshot 拿不到帧 → 切 adb screencap 从 guest 内部取帧。
    #   ⭐ 2026-10-05：导航/切页时 display 瞬变（HWC id 漂移），adb 兜底偶发失败 → 重试 2 次
    ok_shot = False
    for _ in range(3):
        if _adb_screencap(path):
            ok_shot = True
            break
        time.sleep(1.2)
    if ok_shot:
        _SHOT_CACHE["ts"] = time.time()
        return path
    print(f"!! 截图失败: {r[:150] if tried_winshot else 'winshot 冷却跳过 + adb 兜底失败'}")
    return None


# ============================ 兼容旧 API（dump 已不可用）============================
def dump(save=True, retry=1):
    """⚠️ MuMu 上 uiautomator 不可用（缺 libnativeloader.so）。
    本函数**不再返回 UI 树**，改为返回 None 并给出明确提示，
    调用方若依赖 UI 文本请改用数据库（soul_im）或截图（screenshot）。
    """
    print("!! dump() 在 MuMu 上不可用（镜像缺 libnativeloader.so）→ 请用数据库读消息 / screenshot 看界面")
    return None


def nodes(path=None):
    """兼容旧 API：无 UI 树，恒返回空列表（防止把过期 ui.xml 当现状）"""
    return []


def center(bounds):
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    if not m:
        return None
    x1, y1, x2, y2 = map(int, m.groups())
    return ((x1 + x2) // 2, (y1 + y2) // 2)


def find_text(kw, ns=None):
    return None


def tap_text(kw, ns=None):
    print(f"!! tap_text 不可用（无 UI 树）: {kw}")
    return False


def has_bottom_bar(ns=None):
    return False


def tap_back_arrow(ns=None):
    """点应用左上角返回箭头（绝不用 keyevent 4）"""
    print("点左上角返回箭头", BACK_XY)
    tap(*BACK_XY)
    return True


def find_send_center(ns=None):
    return SEND_XY


def send(ns=None):
    print("发送按钮", SEND_XY)
    tap(*SEND_XY)
    return SEND_XY


def safe_tap(text=None, rid=None):
    """防乱点护栏在无 UI 树时**失效** → 直接拒绝，绝不盲点。"""
    print(f"!! safe_tap 需要 UI 树，MuMu 不可用 → 拒绝盲点 text={text} rid={rid}")
    return False


def page():
    """无 UI 树 → 只能用 Activity 粗判：chat_list / conversation / other"""
    a = activity()
    if not a:
        return "blank"
    if "MainActivity" in a:
        return "chat_list"       # 无法区分会话页，需配合调用方坐标守卫
    return "other"


def modal_present():
    return None


def require_page(expected, nav_to=None):
    if page() == expected:
        return True
    if nav_to:
        nav_to()
        time.sleep(2.0)
        return page() == expected
    return False


def show(ns=None, only_text=False):
    print("!! show() 不可用（无 UI 树）；请用 screenshot() 看界面，或 soul_im.py 读消息")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        print("MuMu CLI:", MUMU_CLI, "存在" if os.path.exists(MUMU_CLI) else "❌缺失")
        print("Soul display:", display(refresh=True))
        print("前台 Activity:", activity())
        print("Soul 进程:", "运行中" if app_running() else "未运行")
    elif cmd == "screenshot":
        p = screenshot(sys.argv[2] if len(sys.argv) > 2 else None)
        print("截图:", p)
    elif cmd == "tapxy":
        print(tap(int(sys.argv[2]), int(sys.argv[3])))
    elif cmd == "type":
        print("输入:", type_text(sys.argv[2]))
    elif cmd == "restart":
        restart_app()
        print("已重启，display =", display(refresh=True))
